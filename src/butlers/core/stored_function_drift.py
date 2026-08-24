"""Stored-function body drift probe (bu-bi5an).

The gap this closes
-------------------
``scripts/init-db.sql`` is the single privileged bootstrap entrypoint, and it
defines the body of every managed stored function -- some at the top level,
most nested inside a bootstrap-owned installer or finalizer whose whole job is
to emit them.  Changing one of those bodies reaches an **already installed**
database only when an operator re-runs that script by hand: nothing in
``deploy/`` or the Makefile invokes it, and ``AGENTS.md`` tells operators to
re-run it "only when the managed schema/role surface changes" -- which a change
to a stored body is not.  Alembic cannot close the gap either; several of these
functions are owned by NOLOGIN roles the migration role is deliberately not a
member of.

So a deployed database can keep executing an old body indefinitely, and --
before this module -- nothing anywhere reported that the deployed body and the
committed body disagreed.  bu-95gq7 is the case that surfaced it; the problem
generalises to every future body change.

This module does not converge anything.  It converts silent drift into a
visible, actionable state: it parses the committed definitions out of
``scripts/init-db.sql``, reads the deployed bodies out of ``pg_proc.prosrc``,
and reports which functions disagree.

Reported, never fatal
---------------------
A mismatch is reported (``GET /api/system/stored-functions``, plus one WARNING
line at dashboard-api startup), never raised.  Refusing to boot over a
cosmetic body difference would be strictly worse than the drift it complains
about, and this probe's own comparison is the thing most likely to be wrong.

Scope: every function ``init-db.sql`` defines
---------------------------------------------
Scope is *discovered*, not declared: every ``CREATE [OR REPLACE] FUNCTION``
statement in ``scripts/init-db.sql``, including the ones nested inside an
installer body, is in scope.  There is deliberately no opt-in list, because an
opt-in list is one more thing to forget to update -- a function added tomorrow
is covered tomorrow.

The two outcomes that a naive check would swallow silently are named instead:

* a function ``init-db.sql`` defines that is **not deployed** is reported as
  ``not_deployed`` rather than skipped.  This is a legitimate state, not drift
  -- ``restore_drill_executor.*`` and the ``public.runtime_attention_*``
  interface only exist once ``core_196``/``core_198`` have invoked their
  bootstrap installers -- so it is surfaced separately from ``drifted``
  instead of being escalated as if it were.
* a function with **more than one committed variant** is matched against all of
  them.  ``public.append_runtime_attention_model_breaker`` and
  ``public.append_runtime_attention_fleet_halt`` each have a v1 body (emitted
  by ``runtime_attention_admin.install_interface``) and a v2 body (emitted by
  ``runtime_attention_admin.upgrade_producers_v2``, invoked once by
  ``core_199``).  Both are committed states, so a database sitting on either is
  ``matched``; the report names *which* variant matched, by ``init-db.sql``
  line, so an operator can still see that a database is on the older one.

Comparison: whitespace-insensitive, literal-sensitive
-----------------------------------------------------
``pg_proc.prosrc`` stores the function body **verbatim** -- PostgreSQL does not
reformat it -- so against a freshly bootstrapped database the committed body
and the deployed body are byte-identical today.  The normalisation in
:func:`normalize_function_body` is therefore defensive rather than load-bearing:
it exists so that a body re-installed from differently indented source, or a
checkout with different line endings, does not read as drift.  A probe that
cries wolf on every deploy gets ignored, which would reproduce the exact defect
this module exists to fix.

Every rule is whitespace-only.  Nothing here folds case, strips comments, or
canonicalises SQL, because each of those would mask a real change to a body:
the literals inside these bodies are the thing most likely to be edited, and
the comments are load-bearing documentation of a security boundary.

Redaction
---------
Bodies are never returned, logged, or serialised.  A stored body can contain
operator-supplied literals, so the report carries the function name, the
outcome, and a short digest of each side -- enough to see *that* two bodies
disagree and to tell two drifts apart, never enough to reconstruct one.
"""

from __future__ import annotations

import hashlib
import logging
import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: The committed authority for every managed stored-function body.
INIT_DB_SQL_PATH = _REPO_ROOT / "scripts" / "init-db.sql"

#: Deployed body matches one of the committed definitions.
MATCHED = "matched"
#: Deployed body matches none of them -- the actionable outcome.
DRIFTED = "drifted"
#: ``init-db.sql`` defines it, but no such function exists in the catalog.
NOT_DEPLOYED = "not_deployed"

_CREATE_FUNCTION_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
    r"(?P<name>[A-Za-z_][\w$]*\.[A-Za-z_][\w$]*)\s*\(",
    re.IGNORECASE,
)
_BODY_TAG_RE = re.compile(r"\bAS\s+(?P<tag>\$[A-Za-z_0-9]*\$)", re.IGNORECASE)

_DEPLOYED_BODIES_SQL = """
    SELECT n.nspname || '.' || p.proname AS function_name, p.prosrc AS body
    FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE p.prokind = 'f'
      AND n.nspname || '.' || p.proname = ANY($1::text[])
"""


class StoredFunctionParseError(RuntimeError):
    """``init-db.sql`` could not be parsed into function definitions."""


@dataclass(frozen=True)
class FunctionDefinition:
    """One ``CREATE [OR REPLACE] FUNCTION`` statement's committed body."""

    function: str
    #: 1-based line in ``init-db.sql`` where the ``CREATE`` starts.
    line: int
    body: str


@dataclass(frozen=True)
class StoredFunctionEntry:
    """One function's committed-vs-deployed comparison outcome.

    Carries digests, never bodies -- see this module's Redaction note.
    """

    function: str
    status: str
    committed_lines: tuple[int, ...]
    committed_digests: tuple[str, ...]
    deployed_digests: tuple[str, ...]
    #: ``init-db.sql`` line of the committed variant the deployed body matched,
    #: or ``None`` when it matched none (or when the function is not deployed).
    matched_line: int | None


@dataclass(frozen=True)
class StoredFunctionDriftReport:
    """Result of one stored-function drift pass."""

    checked_at: datetime
    entries: tuple[StoredFunctionEntry, ...]
    #: Non-None when the check itself failed (source unreadable, catalog query
    #: refused).  A degraded check is never rendered as a truthful all-clear.
    check_error: str | None = None

    @property
    def is_available(self) -> bool:
        return self.check_error is None

    @property
    def drifted(self) -> tuple[StoredFunctionEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status == DRIFTED)

    @property
    def not_deployed(self) -> tuple[StoredFunctionEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status == NOT_DEPLOYED)

    @property
    def matched(self) -> tuple[StoredFunctionEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status == MATCHED)

    @property
    def is_drifted(self) -> bool:
        return bool(self.drifted)

    def entry(self, function: str) -> StoredFunctionEntry | None:
        """Return the entry for *function*, or ``None`` when it is out of scope."""
        for candidate in self.entries:
            if candidate.function == function:
                return candidate
        return None


def parse_function_definitions(sql: str) -> tuple[FunctionDefinition, ...]:
    """Extract every ``CREATE [OR REPLACE] FUNCTION`` body from *sql*.

    Nested definitions are found too: an installer's own body is one match and
    each function it emits is another, because dollar-quote tags are unique
    within ``init-db.sql`` and each ``CREATE`` is located independently.

    Also usable on a single ``pg_get_functiondef()`` statement, which has the
    same shape.
    """
    creates = list(_CREATE_FUNCTION_RE.finditer(sql))
    definitions: list[FunctionDefinition] = []
    for index, create in enumerate(creates):
        name = create.group("name").lower()
        # A body opener belongs to this CREATE only if it precedes the next one:
        # an installer's own ``AS $tag$`` comes before the CREATEs it emits, and
        # each emitted CREATE's opener comes before the one after it.  Bounding
        # the search this way turns a definition this parser cannot read into a
        # loud failure instead of a body silently sliced from the wrong span.
        limit = creates[index + 1].start() if index + 1 < len(creates) else len(sql)
        opener = _BODY_TAG_RE.search(sql, create.end(), limit)
        if opener is None:
            raise StoredFunctionParseError(f"no dollar-quoted body found for {name}")
        tag = opener.group("tag")
        end = sql.find(tag, opener.end())
        if end < 0:
            raise StoredFunctionParseError(f"unterminated {tag} body for {name}")
        definitions.append(
            FunctionDefinition(
                function=name,
                line=sql.count("\n", 0, create.start()) + 1,
                body=sql[opener.end() : end],
            )
        )
    return tuple(definitions)


def normalize_function_body(body: str) -> str:
    """Return *body* with insignificant whitespace removed.

    Four rules, applied in order.  Each is whitespace-only, so none of them can
    hide a changed literal, statement, or comment:

    1. line endings -- ``\\r\\n`` and bare ``\\r`` become ``\\n``, so a checkout
       with different line endings is not drift;
    2. trailing spaces and tabs are dropped from every line;
    3. uniform indentation is removed, so the same body emitted from a more (or
       less) deeply nested installer is not drift;
    4. leading and trailing blank lines are dropped.
    """
    unified = body.replace("\r\n", "\n").replace("\r", "\n")
    trimmed = "\n".join(line.rstrip(" \t") for line in unified.split("\n"))
    dedented = textwrap.dedent(trimmed)
    return dedented.strip("\n")


def body_digest(body: str) -> str:
    """Return a short, non-reversible digest of *body*'s normalised form."""
    normalized = normalize_function_body(body)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def compare_stored_functions(
    definitions: Sequence[FunctionDefinition],
    deployed: Mapping[str, Sequence[str]],
) -> tuple[StoredFunctionEntry, ...]:
    """Compare committed *definitions* against *deployed* bodies by function name.

    *deployed* maps a qualified function name to every body the catalog holds
    under it (more than one when the name is overloaded).  A function is
    ``matched`` only when **every** deployed body normalises to one of that
    name's committed variants, so an overload the committed source does not
    describe is reported rather than averaged away.
    """
    committed: dict[str, list[FunctionDefinition]] = {}
    for definition in definitions:
        committed.setdefault(definition.function, []).append(definition)

    entries: list[StoredFunctionEntry] = []
    for function in sorted(committed):
        variants = committed[function]
        line_by_digest: dict[str, int] = {}
        for variant in variants:
            line_by_digest.setdefault(body_digest(variant.body), variant.line)
        committed_digests = tuple(body_digest(variant.body) for variant in variants)
        committed_lines = tuple(variant.line for variant in variants)

        bodies = tuple(deployed.get(function) or ())
        if not bodies:
            entries.append(
                StoredFunctionEntry(
                    function=function,
                    status=NOT_DEPLOYED,
                    committed_lines=committed_lines,
                    committed_digests=committed_digests,
                    deployed_digests=(),
                    matched_line=None,
                )
            )
            continue

        deployed_digests = tuple(body_digest(body) for body in bodies)
        matched_lines = [
            line_by_digest[digest] for digest in deployed_digests if digest in line_by_digest
        ]
        all_matched = len(matched_lines) == len(deployed_digests)
        entries.append(
            StoredFunctionEntry(
                function=function,
                status=MATCHED if all_matched else DRIFTED,
                committed_lines=committed_lines,
                committed_digests=committed_digests,
                deployed_digests=deployed_digests,
                matched_line=min(matched_lines) if all_matched else None,
            )
        )
    return tuple(entries)


async def compute_stored_function_drift(
    pool: asyncpg.Pool, *, init_db_path: Path | None = None
) -> StoredFunctionDriftReport:
    """Compare every committed stored-function body against its deployed body.

    Never raises: any failure (unreadable source, refused catalog query) is
    captured into ``check_error`` instead of crashing the caller or producing a
    false all-clear.
    """
    checked_at = datetime.now(UTC)
    source = init_db_path or INIT_DB_SQL_PATH

    def _degraded(reason: str) -> StoredFunctionDriftReport:
        return StoredFunctionDriftReport(checked_at=checked_at, entries=(), check_error=reason)

    try:
        sql = source.read_text(encoding="utf-8")
    except OSError as exc:
        return _degraded(f"cannot read {source.name}: {type(exc).__name__}")

    try:
        definitions = parse_function_definitions(sql)
    except StoredFunctionParseError as exc:
        return _degraded(f"cannot parse {source.name}: {exc}")
    if not definitions:
        return _degraded(f"{source.name} defines no stored functions, which cannot be right")

    names = sorted({definition.function for definition in definitions})
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_DEPLOYED_BODIES_SQL, names)
    except Exception as exc:  # noqa: BLE001 - a degraded check must never crash its caller
        return _degraded(f"cannot read pg_proc: {type(exc).__name__}")

    deployed: dict[str, list[str]] = {}
    for row in rows:
        deployed.setdefault(row["function_name"], []).append(row["body"])

    return StoredFunctionDriftReport(
        checked_at=checked_at, entries=compare_stored_functions(definitions, deployed)
    )


def log_stored_function_drift(report: StoredFunctionDriftReport) -> None:
    """Emit one log line summarising *report*.  Names only -- never bodies."""
    if not report.is_available:
        logger.warning(
            "Stored-function drift check unavailable: %s. "
            "Deployed function bodies were NOT compared against scripts/init-db.sql.",
            report.check_error,
        )
        return

    if report.is_drifted:
        logger.warning(
            "Stored-function body drift: %d of %d functions defined by scripts/init-db.sql "
            "differ from the bodies deployed in this database (%s). "
            "Re-run scripts/init-db.sql against this database to converge. "
            "Bodies are never logged; the digests identify the mismatch.",
            len(report.drifted),
            len(report.entries),
            ", ".join(
                f"{entry.function} committed={'/'.join(entry.committed_digests)} "
                f"deployed={'/'.join(entry.deployed_digests)}"
                for entry in report.drifted
            ),
        )
        return

    logger.info(
        "Stored-function bodies match scripts/init-db.sql (%d matched, %d not deployed).",
        len(report.matched),
        len(report.not_deployed),
    )


__all__ = [
    "DRIFTED",
    "INIT_DB_SQL_PATH",
    "MATCHED",
    "NOT_DEPLOYED",
    "FunctionDefinition",
    "StoredFunctionDriftReport",
    "StoredFunctionEntry",
    "StoredFunctionParseError",
    "body_digest",
    "compare_stored_functions",
    "compute_stored_function_drift",
    "log_stored_function_drift",
    "normalize_function_body",
    "parse_function_definitions",
]
