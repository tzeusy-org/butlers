"""Static privilege-boundary coverage for the restore-drill bootstrap path.

The checks intentionally inspect checked-in contracts only.  They do not run
``init-db.sql`` or contact PostgreSQL, so no role or database is modified.

Assertion style.  Everything here reads text, so every check is a string
comparison at some level.  The distinction that matters is whether the string
*is* the contract or merely stands in for one:

* Fixed vocabulary is pinned literally -- role names, environment-variable
  names, file paths, psql meta-command syntax, catalog predicates, and
  operator-facing ``RAISE`` messages.  Changing one of those strings changes
  what an operator types or what the catalog check accepts.
* Everything else is bound to the thing it is about before it is asserted.  A
  ``%I`` placeholder is resolved through its ``format()`` argument so the check
  can name the role it is talking about; a banned operator recipe is looked for
  inside fenced code blocks so prose may still describe it; "the migration must
  not do X" is expressed as "``upgrade()`` executes no mutating SQL at all"
  rather than as a ban on one spelling of X.

Blind spots, restated rather than assumed away:

* Nothing here executes SQL.  A statement present in the source may still be
  contradicted by a later statement this sweep does not model, and a privilege
  granted outside ``init-db.sql`` is invisible.
* ``format()`` argument binding is textual.  An argument built by concatenation,
  or routed through an intermediate variable, is reported as the expression text
  rather than the role it resolves to at run time.
* The Markdown scan recognises ``` fences only.  Four-space-indented code blocks
  and HTML ``<pre>`` blocks are invisible to it; these documents use neither.
* Role-attribute parsing requires an explicit attribute list.  ``ALTER ROLE x
  PASSWORD ...`` and ``GRANT <predefined role> TO x`` carry cluster capability
  without matching, and are not modelled.
"""

from __future__ import annotations

import ast
import base64
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from butlers.testing.migration import init_db_sql_for_dbapi

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INIT_DB = _REPO_ROOT / "scripts" / "init-db.sql"
_PROVISIONER = _REPO_ROOT / "scripts" / "provision_restore_drill_executor.sh"
_MIGRATION = (
    _REPO_ROOT / "alembic" / "versions" / "core" / "core_196_restore_drill_executor_boundary.py"
)
_DASHBOARD_APP = _REPO_ROOT / "src" / "butlers" / "api" / "app.py"
_EXECUTOR = _REPO_ROOT / "src" / "butlers" / "jobs" / "restore_drill_executor.py"
_OPERATIONS_DOC = _REPO_ROOT / "docs" / "operations" / "backup-restore.md"
_SCRIPTS_README = _REPO_ROOT / "scripts" / "README.md"
_SCHEMA_TOPOLOGY_DOC = _REPO_ROOT / "docs" / "data_and_storage" / "schema-topology.md"
_COMPOSE_FILE = _REPO_ROOT / "docker-compose.yml"

_EXECUTOR_ROLE = "restore_drill_executor"
_EXECUTOR_OWNER_ROLE = "restore_drill_executor_owner"
_LEDGER = "restore_drill_executor.restore_drill_results"


# --------------------------------------------------------------------------- #
# SQL source helpers
# --------------------------------------------------------------------------- #

_SQL_LINE_COMMENT = re.compile(r"--[^\n]*")
# ``'first half ' || 'second half'`` is one SQL statement split across two
# literals.  Collapsing the join lets a statement be matched whole instead of by
# whichever half happens to carry the interesting words.
_SQL_LITERAL_JOIN = re.compile(r"'\s*\|\|\s*'")
_SQL_STRING_LITERAL = re.compile(r"'[^']*'")


def _strip_sql_comments(sql: str) -> str:
    return _SQL_LINE_COMMENT.sub("", sql)


def _strip_sql_literals(sql: str) -> str:
    """``sql`` with single-quoted literals blanked.

    ``has_table_privilege(..., 'INSERT')`` names a privilege; it is not an
    ``INSERT`` statement.  Doubled-quote escapes and dollar quoting are not
    modelled -- neither appears in the SQL these checks read.
    """
    return _SQL_STRING_LITERAL.sub("''", sql)


def _joined_sql_literals(source: str) -> str:
    """``source`` with ``' ... ' || ' ... '`` concatenations spliced together."""
    return _SQL_LITERAL_JOIN.sub("", source)


def _split_top_level(text: str) -> tuple[str, ...]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return tuple(part.strip() for part in parts if part.strip())


def _format_call_arguments(source: str, start: int, end: int) -> tuple[str, ...] | None:
    """The ``format()`` arguments that follow the SQL literal spanning ``start:end``.

    ``None`` means the matched SQL is not the first argument of a ``format(...)``
    call -- a bare ``EXECUTE '<sql>'`` has no placeholder to bind.
    """
    literal_start = source.rfind("'", 0, start)
    literal_end = source.find("'", end)
    if literal_start == -1 or literal_end == -1:
        return None
    if not source[:literal_start].rstrip().endswith("format("):
        return None

    depth = 0
    index = literal_end + 1
    while index < len(source):
        char = source[index]
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return _split_top_level(source[literal_end + 1 : index])
            depth -= 1
        index += 1
    return None


def _line_of(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


@dataclass(frozen=True)
class _StatementSite:
    """One occurrence of a SQL statement, with its ``format()`` binding."""

    line: int
    arguments: tuple[str, ...] | None


def _statement_sites(source: str, pattern: re.Pattern[str]) -> list[_StatementSite]:
    return [
        _StatementSite(
            line=_line_of(source, match.start()),
            arguments=_format_call_arguments(source, match.start(), match.end()),
        )
        for match in pattern.finditer(source)
    ]


_ROLE_ATTRIBUTES = (
    "LOGIN",
    "NOLOGIN",
    "INHERIT",
    "NOINHERIT",
    "SUPERUSER",
    "NOSUPERUSER",
    "CREATEROLE",
    "NOCREATEROLE",
    "CREATEDB",
    "NOCREATEDB",
    "REPLICATION",
    "NOREPLICATION",
    "BYPASSRLS",
    "NOBYPASSRLS",
)
# Longest-first so ``NOLOGIN`` is never consumed as ``LOGIN``.
_ATTRIBUTE_ALTERNATION = "|".join(sorted(_ROLE_ATTRIBUTES, key=len, reverse=True))
_ROLE_STATEMENT = re.compile(
    r"\b(CREATE|ALTER)\s+ROLE\s+(%I|[a-z_][a-z0-9_]*)\s+(?:WITH\s+)?"
    rf"((?:(?:{_ATTRIBUTE_ALTERNATION})\b\s*)+)",
    re.IGNORECASE,
)

# Every capability that lets a role escape its own schema and reach the cluster.
_REQUIRED_ROLE_LOCKDOWN = frozenset({"NOSUPERUSER", "NOCREATEROLE", "NOREPLICATION", "NOCREATEDB"})
_FORBIDDEN_ROLE_CAPABILITY = frozenset(
    {"SUPERUSER", "CREATEROLE", "REPLICATION", "BYPASSRLS", "CREATEDB"}
)
# init-db.sql names the executor through this variable, not literally.
_EXECUTOR_ROLE_VAR = "_restore_drill_executor_role"


@dataclass(frozen=True)
class _RoleStatement:
    """One ``CREATE ROLE``/``ALTER ROLE`` with an explicit attribute list."""

    line: int
    verb: str
    role_token: str
    attributes: frozenset[str]
    arguments: tuple[str, ...] | None

    @property
    def role(self) -> str:
        """The role the statement acts on, resolving a ``%I`` placeholder."""
        if self.role_token != "%I":
            return self.role_token
        if self.arguments:
            return self.arguments[0]
        return "%I (unbound)"


def _role_attribute_statements(source: str) -> list[_RoleStatement]:
    statements: list[_RoleStatement] = []
    for match in _ROLE_STATEMENT.finditer(source):
        statements.append(
            _RoleStatement(
                line=_line_of(source, match.start()),
                verb=match.group(1).upper(),
                role_token=match.group(2),
                attributes=frozenset(match.group(3).upper().split()),
                arguments=_format_call_arguments(source, match.start(), match.end()),
            )
        )
    return statements


def _dollar_quoted_body(source: str, tag: str) -> str:
    """The body between the two ``$tag$`` delimiters."""
    delimiter = f"${tag}$"
    start = source.index(delimiter) + len(delimiter)
    return source[start : source.index(delimiter, start)]


# --------------------------------------------------------------------------- #
# init-db.sql
# --------------------------------------------------------------------------- #


# ---------------------------------------------------------------------------
# Restore-script invocation detection
#
# PR #3708 removed the hand-run ``scripts/pg_restore.sh`` escape hatch from the
# operations doc.  The ban that replaced it pinned the *substring*
# ``pg_restore.sh``, so the doc could not name the path it was describing.  What
# has to stay banned is the runnable form: a command line an operator can copy
# out of the doc.
#
# Caught:
#   * a path-executable reference -- ``./scripts/pg_restore.sh``,
#     ``../scripts/pg_restore.sh``, ``~/butlers/scripts/pg_restore.sh``,
#     ``/opt/butlers/scripts/pg_restore.sh``
#   * an interpreter prefix -- ``bash scripts/pg_restore.sh``,
#     ``sh -x pg_restore.sh``
#   * the name followed by an argument-shaped token rather than by the next word
#     of a sentence: a flag, a path, a shell variable, a quote, a
#     ``<placeholder>``, or a dotted filename
#   * any line naming the script inside a fenced code block, runnable or not
#
# NOT caught, deliberately -- a narrowed boundary whose holes go unrecorded reads
# as more precise than it is:
#   * a bare or repo-relative mention in prose (``pg_restore.sh``,
#     ``scripts/pg_restore.sh``); permitting this is the point of the narrowing
#   * a four-space-indented code block or an HTML ``<pre>`` block.  This doc uses
#     neither, and indented blocks are indistinguishable from nested list items
#     without a full Markdown parse
#   * indirection -- ``SCRIPT=pg_restore.sh`` followed by ``./$SCRIPT``, or a
#     name assembled from fragments
#   * an invocation split so the name and its argument never share a line
#   * ``source pg_restore.sh`` / ``. pg_restore.sh``
#   * prose urging a hand-run restore without showing the command, and any
#     invocation of a renamed copy of the script
# ---------------------------------------------------------------------------

_RESTORE_SCRIPT_NAME = "pg_restore.sh"

# A token that reads as an argument rather than as the next word of a sentence.
_ARGUMENT_TOKEN = r"""(?:-{1,2}\w|[<{$"']|[.~]{0,2}/|\w[\w.-]*\.\w)"""

_RESTORE_SCRIPT_INVOCATION_PATTERNS = (
    # Path-executable reference: ./x, ../x, ~/x, /abs/x.
    re.compile(rf"(?<![\w.~/-])(?:\.{{1,2}}|~)?/(?:[\w.~-]+/)*{re.escape(_RESTORE_SCRIPT_NAME)}"),
    # Interpreter prefix, with or without flags and a leading directory.
    re.compile(
        rf"\b(?:ba|da|k|z)?sh\b[ \t]+(?:-\S+[ \t]+)*(?:[\w.~/-]*/)?"
        rf"{re.escape(_RESTORE_SCRIPT_NAME)}"
    ),
    # The name followed by an argument instead of by prose.
    re.compile(rf"{re.escape(_RESTORE_SCRIPT_NAME)}[ \t]+{_ARGUMENT_TOKEN}"),
)


def _fenced_code_blocks(markdown: str) -> list[str]:
    """Return the body of every fenced code block in *markdown*."""
    blocks: list[str] = []
    fence: str | None = None
    body: list[str] = []
    for line in markdown.splitlines():
        stripped = line.lstrip()
        if fence is None:
            if stripped.startswith(("```", "~~~")):
                fence, body = stripped[:3], []
            continue
        if stripped.startswith(fence):
            blocks.append("\n".join(body))
            fence = None
            continue
        body.append(line)
    if fence is not None:  # Unterminated fence: treat the remainder as a block.
        blocks.append("\n".join(body))
    return blocks


def _restore_script_invocations(markdown: str) -> list[str]:
    """Return every runnable reference to the restore script in *markdown*.

    Naming the script in prose is not runnable and is not returned.  The comment
    above this helper enumerates both the caught and the uncaught shapes.
    """
    found: set[str] = set()
    for block in _fenced_code_blocks(markdown):
        found.update(line.strip() for line in block.splitlines() if _RESTORE_SCRIPT_NAME in line)
    for pattern in _RESTORE_SCRIPT_INVOCATION_PATTERNS:
        found.update(match.group(0) for match in pattern.finditer(markdown))
    return sorted(found)


_DESCRIPTIVE_RESTORE_SCRIPT_MENTIONS = (
    "The restore path is `pg_restore.sh`, and it is never driven by hand.",
    "`scripts/pg_restore.sh` audits ownership once the restore has completed.",
    "pg_restore.sh restores only to a named scratch database.",
    "Ownership is pinned by tests/scripts/test_pg_restore_definer_ownership.py.",
)

_RESTORE_SCRIPT_INVOCATIONS = (
    "./scripts/pg_restore.sh",
    "Run ./scripts/pg_restore.sh dump.sql.gz --target-db butlers_restore by hand.",
    "/opt/butlers/scripts/pg_restore.sh dump.sql.gz",
    "../scripts/pg_restore.sh",
    "~/butlers/scripts/pg_restore.sh",
    "bash scripts/pg_restore.sh dump.sql.gz",
    "sh -x pg_restore.sh /backups/latest.sql.gz",
    # Runnable with no argument and no leading ``./``: only the interpreter
    # prefix distinguishes these from prose.
    "bash scripts/pg_restore.sh",
    "sh -x scripts/pg_restore.sh",
    "pg_restore.sh <backup-file.sql.gz> --target-db <name>",
    "pg_restore.sh --env-file .env.production",
    "pg_restore.sh $BACKUP_FILE",
    "```bash\npg_restore.sh\n```",
)


# ---------------------------------------------------------------------------
# The doc set the invocation ban is evaluated over
#
# The ban started life doc-local: it ran against ``docs/operations/backup-restore.md``
# and nothing else, so the escape hatch PR #3708 removed could be reintroduced by
# putting the command line one file over.  The set below closes that, and it is a
# glob rather than an allowlist on purpose -- an allowlist has to be remembered,
# and the doc nobody remembers to add is exactly the hole this is here to close.
#
# In scope:
#   * every ``*.md`` under ``docs/operations/``.  That tree states its own scope
#     in its index -- "running Butlers in production and maintaining it" -- which
#     is the surface an operator reads for instructions to follow.  A runbook
#     added tomorrow is covered without anyone editing this test.
#   * ``scripts/README.md``.  It sits outside that tree but is the index of the
#     scripts directory, so it is the other place a runnable command line for a
#     script naturally lands.
#
# Out of scope, deliberately:
#   * the rest of ``docs/`` -- architecture, concepts, and the dated
#     ``docs/superpowers/plans/`` and ``docs/redesigns/`` records.  Those describe
#     and remember rather than instruct, and a live ban over a dated record forces
#     edits to history.
#   * ``docs/getting_started/`` -- first-run setup against a disposable local
#     database, not the production restore boundary this ban protects.
#
# That boundary has a real cost: an invocation pasted into an architecture page is
# not caught.  It is recorded here rather than papered over, and
# ``test_the_invocation_ban_stops_at_the_operator_facing_boundary`` pins the edge.
# ---------------------------------------------------------------------------


def _operator_facing_docs(repo_root: Path) -> list[Path]:
    """Return every doc the restore-script invocation ban is evaluated over.

    ``scripts/README.md`` is returned whether or not it exists, so a rename that
    drops it out of scope surfaces as a read error rather than as a doc set that
    silently shrank.
    """
    docs = sorted((repo_root / "docs" / "operations").rglob("*.md"))
    docs.append(repo_root / "scripts" / "README.md")
    return docs


def _restore_script_invocations_by_doc(repo_root: Path) -> dict[str, list[str]]:
    """Return the runnable restore-script references each in-scope doc shows.

    Keyed by repo-relative path; docs that show none are omitted, so an empty
    mapping is the passing state.
    """
    return {
        str(path.relative_to(repo_root)): invocations
        for path in _operator_facing_docs(repo_root)
        if (invocations := _restore_script_invocations(path.read_text(encoding="utf-8")))
    }


def _write_operator_facing_doc_tree(root: Path) -> None:
    """Materialize the minimum in-scope doc set with invocation-free content."""
    operations = root / "docs" / "operations"
    operations.mkdir(parents=True)
    (operations / "backup-restore.md").write_text(
        "The restore path is `scripts/pg_restore.sh`, and it is never driven by hand.\n",
        encoding="utf-8",
    )
    (operations / "index.md").write_text("# Operations\n", encoding="utf-8")
    (operations / "troubleshooting.md").write_text("# Troubleshooting\n", encoding="utf-8")
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "README.md").write_text("# Scripts\n", encoding="utf-8")


_PLANTED_INVOCATION_DOC = (
    "# Runbook\n\n```bash\n./scripts/pg_restore.sh dump.sql.gz --target-db butlers_restore\n```\n"
)

# A doc other than backup-restore.md, including one that does not exist yet, is
# where the doc-local ban was blind.
_DOCS_OUTSIDE_BACKUP_RESTORE = (
    "docs/operations/index.md",
    "docs/operations/troubleshooting.md",
    "docs/operations/runbook-added-after-this-test.md",
    "scripts/README.md",
)


def test_init_db_reserves_an_isolated_executor_without_widening_shared_roles() -> None:
    """REQ-database-security-006 keeps the executor out of every shared grant path."""
    source = _INIT_DB.read_text(encoding="utf-8")
    joined = _joined_sql_literals(source)

    # Fixed vocabulary: the provisioner, the executor job, and the migration all
    # name this role literally, so the declaration is the contract.
    assert f"_restore_drill_executor_role TEXT := '{_EXECUTOR_ROLE}'" in source

    # The executor is revoked from schema ``public`` rather than granted usage on
    # it.  Binding the revoke to its ``format()`` argument is what makes this a
    # statement about the executor instead of about whichever role a bare
    # ``%I`` happened to hold.
    public_revokes = _statement_sites(
        source, re.compile(r"REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I")
    )
    assert ["_restore_drill_executor_role"] == [
        site.arguments[0] for site in public_revokes if site.arguments
    ], f"unexpected public-schema revoke targets: {public_revokes}"

    # The migration user may SET ROLE into the runtime roles and only those.  The
    # loop variable and the array it iterates are asserted together; either half
    # alone permits the executor to be added to the other.
    set_true_grants = _statement_sites(source, re.compile(r"GRANT %I TO %I WITH SET TRUE"))
    assert [site.arguments for site in set_true_grants] == [("_role", "_migration_user")]
    loop_header = source.rindex("FOREACH _role IN ARRAY", 0, source.index("WITH SET TRUE"))
    assert source[loop_header:].startswith("FOREACH _role IN ARRAY _all_runtime_roles")

    runtime_roles_start = source.index("_all_runtime_roles TEXT[]")
    runtime_roles_end = source.index("];", runtime_roles_start)
    assert _EXECUTOR_ROLE not in source[runtime_roles_start:runtime_roles_end]

    # The ledger's owner is the point of the boundary, and it lives in the second
    # half of a two-literal concatenation the old prefix match never reached.
    assert f"ALTER TABLE {_LEDGER} OWNER TO {_EXECUTOR_OWNER_ROLE}" in joined

    # The shared migration login gets the read projection and nothing else.
    latest_result_grants = _statement_sites(
        source,
        re.compile(
            rf"GRANT EXECUTE ON FUNCTION {re.escape(_EXECUTOR_ROLE)}\.latest_result\(\) TO %I"
        ),
    )
    assert [site.arguments for site in latest_result_grants] == [("v_migration_role",)]

    ledger_revokes = _statement_sites(
        source, re.compile(rf"REVOKE ALL PRIVILEGES ON TABLE {re.escape(_LEDGER)} FROM %I")
    )
    assert {site.arguments for site in ledger_revokes} == {
        ("v_runtime_role",),
        ("v_optional_calendar_role",),
    }

    # The admin interface is installed by the fixed installer and finalized only
    # by the bootstrap; the migration login may call the former, never the latter.
    assert "CREATE OR REPLACE FUNCTION restore_drill_executor_admin.install_interface()" in source
    assert "restore-drill interface ownership is untrusted" in source
    assert "GRANT USAGE, CREATE ON SCHEMA restore_drill_executor TO %I" not in source
    assert (
        "GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() TO %I"
        not in source
    )
    assert (
        "GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.install_interface() TO %I" in source
    )
    assert (
        "REVOKE EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() FROM %I"
        in source
    )


def test_every_init_db_role_statement_denies_cluster_recovery_capability() -> None:
    """REQ-database-security-006 keeps every normal login NOCREATEDB.

    The previous form asserted that one ``NOSUPERUSER NOCREATEROLE NOREPLICATION
    NOCREATEDB`` string existed somewhere in the file, which any single surviving
    occurrence satisfies.  Dropping ``NOCREATEDB`` from a different role's
    statement -- the actual regression -- left it green.

    A role is created conditionally (``IF NOT EXISTS``) and repaired
    unconditionally, so the repairing ``ALTER ROLE`` is the statement that must
    carry the full lockdown -- a pre-existing role never reaches the ``CREATE``.
    Requiring the union of both statements instead lets the repair be weakened
    silently, which is how the first draft of this check passed its own mutation.
    """
    statements = _role_attribute_statements(_INIT_DB.read_text(encoding="utf-8"))
    assert len(statements) >= 10, "role-attribute parsing found nothing to check"

    conferred = [
        f"{statement.verb} ROLE {statement.role} at init-db.sql:{statement.line} confers "
        f"{sorted(_FORBIDDEN_ROLE_CAPABILITY & statement.attributes)}"
        for statement in statements
        if _FORBIDDEN_ROLE_CAPABILITY & statement.attributes
    ]
    assert not conferred, "init-db.sql grants cluster capability:\n" + "\n".join(conferred)

    by_role: dict[str, list[_RoleStatement]] = {}
    for statement in statements:
        by_role.setdefault(statement.role, []).append(statement)

    failures: list[str] = []
    for role, role_statements in by_role.items():
        # The repairing ALTER governs when there is one; a role with no repair
        # statement is governed by its CREATE.
        governing = [item for item in role_statements if item.verb == "ALTER"] or role_statements
        # One deliberate exemption: the executor's LOGIN and CREATEDB come from
        # the managed provisioner, and a re-run must repair the other attributes
        # without taking CREATEDB back.
        required = _REQUIRED_ROLE_LOCKDOWN - (
            {"NOCREATEDB"} if role == _EXECUTOR_ROLE_VAR else set()
        )
        failures.extend(
            f"{item.verb} ROLE {role} at init-db.sql:{item.line} does not deny "
            f"{sorted(required - item.attributes)}"
            for item in governing
            if not required <= item.attributes
        )
    assert not failures, "cluster capability left open:\n" + "\n".join(failures)

    # Pin the exemption itself.  Adding NOCREATEDB back to the repair statement
    # silently disables the executor on the next bootstrap re-run, and nothing
    # else in the suite would notice.
    repairs = [
        statement
        for statement in statements
        if statement.role == _EXECUTOR_ROLE_VAR and statement.verb == "ALTER"
    ]
    assert len(repairs) == 1, f"expected one executor repair statement, got {repairs}"
    assert "NOCREATEDB" not in repairs[0].attributes
    assert _REQUIRED_ROLE_LOCKDOWN - {"NOCREATEDB"} <= repairs[0].attributes


def test_record_result_ignores_every_compatibility_input_except_the_result() -> None:
    """The four-argument ABI survives, but only ``p_result`` reaches the ledger.

    Asserting that ``'table_count', p_table_count`` is absent bans one spelling of
    the leak.  Asserting the parameter is never referenced in the body bans all of
    them, and does not depend on the explanatory comment surviving a rewrite.
    """
    source = _INIT_DB.read_text(encoding="utf-8")
    body = _strip_sql_comments(_dollar_quoted_body(source, "record_result"))

    inert = [name for name in ("p_backup_name", "p_detail", "p_table_count") if name in body]
    assert inert == [], f"compatibility inputs reach the ledger body: {inert}"
    assert "p_result IS NULL OR p_result NOT IN ('pass', 'fail')" in body
    assert "PERFORM restore_drill_executor_admin.write_audit_projection(p_result)" in body


def test_init_db_projects_restore_results_through_a_purpose_bound_audit_writer() -> None:
    """A hostile ``public.audit_log`` trigger must not run as the ledger owner."""
    source = _INIT_DB.read_text(encoding="utf-8")

    assert "restore_drill_executor_audit_writer" in source
    assert "ALTER FUNCTION restore_drill_executor_admin.write_audit_projection(TEXT)" in source
    assert "OWNER TO restore_drill_executor_audit_writer" in source
    assert "GRANT INSERT ON TABLE public.audit_log TO restore_drill_executor_audit_writer" in source
    assert "GRANT INSERT ON TABLE public.audit_log TO restore_drill_executor_owner" not in source
    # The two search_path settings are catalog contracts: the migration's
    # provenance probes compare them literally against ``proconfig``.
    assert "SET search_path = pg_catalog, pg_temp" in source
    assert "SET search_path = pg_catalog, public, pg_temp" in source

    # Operator-facing RAISE messages.  The text is what an operator reads when the
    # bootstrap refuses, so the string is the contract.
    for message in (
        "restore-drill authority interface must be absent before fixed bootstrap installation",
        "restore-drill interface ownership is untrusted",
        "restore-drill admin schema is not owned by a trusted bootstrap superuser",
        "restore-drill admin interface function is not owned by the bootstrap role",
    ):
        assert message in source


def test_initial_bootstrap_preflight_precedes_every_ddl_or_dcl_mutation() -> None:
    """A future preflight mutation must fail static review before it can partially apply."""
    source = _INIT_DB.read_text(encoding="utf-8")
    marker = "INITIAL_READ_ONLY_BOOTSTRAP_PREFLIGHT"
    marker_offset = source.index(marker)
    assert source.count(marker) == 1

    preflight_start = source.index("DO $$", marker_offset)
    preflight_end = source.index("$$;", preflight_start) + len("$$;")
    preflight = source[preflight_start:preflight_end]
    assert "restore-drill admin bootstrap requires a cluster superuser" in preflight

    first_psql_directive = re.search(r"^\s*\\\S+.*$", source, re.MULTILINE)
    assert first_psql_directive is not None
    assert first_psql_directive.group().strip() == r"\set ON_ERROR_STOP on"
    assert first_psql_directive.start() < preflight_start

    mutator = re.compile(
        r"^\s*(?:CREATE|ALTER|GRANT|REVOKE|DROP|UPDATE|INSERT|DELETE|TRUNCATE)\b",
        re.MULTILINE,
    )
    first_mutator = mutator.search(source)
    assert first_mutator is not None
    assert first_mutator.start() > preflight_end


def test_dbapi_bootstrap_source_removes_only_the_psql_fail_fast_directive() -> None:
    """Testcontainers' direct cursor path cannot receive psql-only commands."""
    source = _INIT_DB.read_text(encoding="utf-8")

    assert init_db_sql_for_dbapi() == source.replace(r"\set ON_ERROR_STOP on" + "\n", "", 1)


# --------------------------------------------------------------------------- #
# provision_restore_drill_executor.sh
# --------------------------------------------------------------------------- #

# Recording psql: captures stdin and argv so a check can ask what the script
# actually sent rather than what its text happens to contain.
_RECORDING_PSQL = (
    "#!/bin/sh\n"
    'if [ -n "$PSQL_ARGV_CAPTURE" ]; then printf "%s\\n" "$@" > "$PSQL_ARGV_CAPTURE"; fi\n'
    'cat > "$PSQL_INPUT_CAPTURE"\n'
)


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _run_provisioner(tmp_path: Path, password: str, **extra_env: str):
    """Run the provisioner against a recording ``psql`` on PATH."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text(password, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "psql", _RECORDING_PSQL)

    completed = subprocess.run(
        [_PROVISIONER],
        check=False,
        env={
            **os.environ,
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(password_file),
            "PSQL_INPUT_CAPTURE": str(tmp_path / "psql-input"),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            **extra_env,
        },
        capture_output=True,
        text=True,
    )
    return completed, tmp_path / "psql-input"


def test_managed_provisioner_rejects_multiline_password_before_running_psql(
    tmp_path: Path,
) -> None:
    """A newline must fail before client input can reinterpret it as psql syntax."""
    password_file = tmp_path / "restore-drill-password"
    password_file.write_text("first-line\nsecond-line", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    psql_called = tmp_path / "psql-called"
    _write_executable(
        bin_dir / "psql",
        '#!/bin/sh\ntouch "$PSQL_CALLED_MARKER"\ncat >/dev/null\n',
    )

    completed = subprocess.run(
        [_PROVISIONER],
        check=False,
        env={
            **os.environ,
            "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE": str(password_file),
            "PSQL_CALLED_MARKER": str(psql_called),
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert not psql_called.exists()
    assert "single line" in completed.stderr
    # The rejection diagnostic must not become the leak it was added to prevent.
    assert "first-line" not in completed.stdout + completed.stderr
    assert "second-line" not in completed.stdout + completed.stderr


def test_managed_provisioner_passes_secret_to_psql_as_encoded_literal_data(tmp_path: Path) -> None:
    """The psql input must not interpolate the raw file secret into SQL/meta syntax."""
    password = "test quote' dollar$ punctuation!"
    completed, psql_input = _run_provisioner(tmp_path, password)

    assert completed.returncode == 0, completed.stderr
    input_text = psql_input.read_text(encoding="utf-8")
    assert password not in input_text
    assert base64.b64encode(password.encode()).decode() in input_text
    assert input_text.startswith("\\set restore_drill_executor_password_b64 ")
    assert "\n\\unset restore_drill_executor_password_b64\n" in input_text
    assert "decode(:'restore_drill_executor_password_b64', 'base64')" in input_text

    # Ordering, not mere presence: a COMMIT that lands before the role statements
    # satisfies "BEGIN; is in the input" while leaving the role half-enabled.
    assert (
        input_text.index("BEGIN;")
        < input_text.index("ALTER ROLE restore_drill_executor")
        < input_text.index("PASSWORD %L")
        < input_text.index("COMMIT;")
    )


def test_managed_provisioner_never_prints_the_secret_it_reads(tmp_path: Path) -> None:
    """Banning the word ``echo`` bans a token; this bans the leak in any spelling."""
    password = "test-provisioner-stdout-leak-canary"
    completed, _ = _run_provisioner(tmp_path, password)

    assert completed.returncode == 0, completed.stderr
    assert password not in completed.stdout
    assert password not in completed.stderr


def test_managed_provisioner_enables_only_the_scoped_login_attributes(tmp_path: Path) -> None:
    """The executor gains LOGIN and CREATEDB, and gains nothing else.

    Asserting ``"CREATEDB" in source`` says the token appears somewhere in the
    script.  Parsing the statement psql actually received says which role gets
    which capability, and an exact set makes an added SUPERUSER fail.
    """
    completed, psql_input = _run_provisioner(tmp_path, "test-attribute-canary")

    assert completed.returncode == 0, completed.stderr
    statements = _role_attribute_statements(psql_input.read_text(encoding="utf-8"))
    assert [statement.role for statement in statements] == [_EXECUTOR_ROLE]
    assert statements[0].attributes == frozenset(
        {"LOGIN", "CREATEDB", "NOINHERIT", "NOSUPERUSER", "NOCREATEROLE", "NOREPLICATION"}
    )


def test_managed_provisioner_never_forwards_shared_application_credentials(
    tmp_path: Path,
) -> None:
    """The bootstrap path must not carry a shared credential into the client.

    Connection selection is deliberately left to the operator's own psql
    configuration, so this pins what the script *passes* -- stdin and argv -- not
    what psql may read from an inherited environment.
    """
    sentinels = {
        "POSTGRES_PASSWORD": "canary-postgres-password-value",
        "DATABASE_URL": "postgresql://canary-user:canary-url-secret@example.invalid/db",
    }
    completed, psql_input = _run_provisioner(
        tmp_path,
        "test-shared-credential-canary",
        PSQL_ARGV_CAPTURE=str(tmp_path / "psql-argv"),
        **sentinels,
    )

    assert completed.returncode == 0, completed.stderr
    forwarded = psql_input.read_text(encoding="utf-8") + (tmp_path / "psql-argv").read_text(
        encoding="utf-8"
    )
    leaked = [name for name, value in sentinels.items() if value in forwarded]
    assert leaked == [], f"shared credentials forwarded to psql: {leaked}"


def test_managed_provisioner_accepts_one_terminal_lf_without_passing_it_to_psql(
    tmp_path: Path,
) -> None:
    """The provisioner and executor share one unambiguous file-secret contract."""
    password = "test-terminal-lf-secret"
    completed, psql_input = _run_provisioner(tmp_path, password + "\n")

    assert completed.returncode == 0, completed.stderr
    input_text = psql_input.read_text(encoding="utf-8")
    assert base64.b64encode(password.encode()).decode() in input_text
    assert base64.b64encode((password + "\n").encode()).decode() not in input_text


# --------------------------------------------------------------------------- #
# core_196 migration
# --------------------------------------------------------------------------- #

_SQL_EXECUTORS = frozenset({"execute", "exec_driver_sql"})
_MUTATING_SQL_VERB = re.compile(
    r"\b(CREATE|ALTER|DROP|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE|COMMENT)\b",
    re.IGNORECASE,
)
_INVOKED_FUNCTION = re.compile(r"\bSELECT\s+([a-z_][a-z0-9_.]*)\s*\(\s*\)", re.IGNORECASE)
# The provenance conditions that make a catalog probe proof of a trusted
# installer rather than proof that *some* matching function exists.  Each is a
# literal catalog comparison, so the string is the contract.
_REQUIRED_INSTALLER_PROVENANCE = (
    "bootstrap_owner.rolsuper",
    "installer.proowner = admin_schema.nspowner",
    "finalizer.proowner = admin_schema.nspowner",
    "installer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]",
    "finalizer.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]",
)


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        if not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = node.value.value
    return constants


def _resolve_sql(node: ast.expr, constants: dict[str, str]) -> str | None:
    """The SQL text of ``node``, or ``None`` when it cannot be resolved statically."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = _resolve_sql(value.value, constants)
                if resolved is None:
                    return None
                parts.append(resolved)
            else:
                return None
        return "".join(parts)
    return None


def _executed_sql(
    function: ast.FunctionDef, constants: dict[str, str]
) -> list[tuple[int, str | None]]:
    """Every SQL string the function hands to ``execute``/``exec_driver_sql``."""
    executed: list[tuple[int, str | None]] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _SQL_EXECUTORS or not node.args:
            continue
        executed.append((node.lineno, _resolve_sql(node.args[0], constants)))
    return executed


def _named_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"core_196 has no {name}() to inspect")


def test_migration_upgrade_issues_no_mutation_of_its_own() -> None:
    """The shared migration login must create no authority object itself.

    The previous form was a list of bans on specific spellings -- ``CREATE TABLE
    restore_drill_executor.restore_drill_results``, ``CREATE SCHEMA IF NOT
    EXISTS ...``, ``attention_ledger``.  ``CREATE TABLE IF NOT EXISTS`` defeats
    the first, and every ban is one rename from silent.  What ``upgrade()``
    actually promises is that it executes no mutating SQL at all.
    """
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    constants = _module_string_constants(tree)
    executed = _executed_sql(_named_function(tree, "upgrade"), constants)

    assert executed, "no executed SQL found in upgrade()"
    unresolved = [line for line, sql in executed if sql is None]
    assert not unresolved, f"upgrade() executes SQL this check cannot read, at lines {unresolved}"

    mutations = [
        (line, match.group(1).upper())
        for line, sql in executed
        if sql is not None
        for match in _MUTATING_SQL_VERB.finditer(_strip_sql_literals(_strip_sql_comments(sql)))
    ]
    assert not mutations, f"upgrade() issues its own DDL/DML: {mutations}"

    invoked = {
        match.group(1)
        for _, sql in executed
        if sql is not None
        for match in _INVOKED_FUNCTION.finditer(_strip_sql_comments(sql))
    }
    assert invoked == {constants["_ADMIN_INSTALLER"]}
    assert constants["_ADMIN_INSTALLER"] == "restore_drill_executor_admin.install_interface"


def test_migration_probes_each_demand_full_installer_provenance() -> None:
    """Both catalog probes, not merely one of them, must prove bootstrap ownership.

    ``"bootstrap_owner.rolsuper" in source`` is satisfied by a single surviving
    occurrence, so weakening one probe -- the exploitable half -- passed.
    """
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    constants = _module_string_constants(tree)
    probes = {
        name: sql
        for name, sql in constants.items()
        if name.endswith("_SQL") and "installer.proname = 'install_interface'" in sql
    }
    assert len(probes) == 2, f"expected two installer probes, found {sorted(probes)}"

    failures = [
        f"{name} is missing {predicate!r}"
        for name, sql in probes.items()
        for predicate in _REQUIRED_INSTALLER_PROVENANCE
        if predicate not in sql
    ]
    assert not failures, "installer provenance weakened:\n" + "\n".join(failures)

    # The audit projection's definer is a separate, ledger-less role; the
    # finalized-interface probe is the only one positioned to check it.
    finalized = next(sql for name, sql in probes.items() if "audit_projection" in sql)
    assert (
        "audit_projection.proconfig = ARRAY['search_path=pg_catalog, pg_temp']::text[]" in finalized
    )
    assert "audit_projection.proowner = audit_writer_owner.oid" in finalized

    # Operator-facing failure message from the fail-closed branch.
    assert "restore-drill bootstrap installer is missing or untrusted" in _MIGRATION.read_text(
        encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Dashboard / executor job
# --------------------------------------------------------------------------- #

# The executor job runs with the isolated credential.  Importing anything that
# resolves the shared application credential would reintroduce the boundary this
# whole file exists to hold, so its butlers-internal imports are an allowlist.
_EXECUTOR_ALLOWED_IMPORTS = frozenset({"butlers.jobs.backup_health"})


def test_dashboard_has_no_restore_drill_scheduler_or_shared_credential_launch_path() -> None:
    """REQ-database-security-006 keeps the privileged lifecycle out of the API."""
    dashboard_source = _DASHBOARD_APP.read_text(encoding="utf-8")

    # Banning ``run_restore_drill`` bans one symbol name; the API has no business
    # naming the subsystem at all, which no rename can satisfy.
    assert "restore_drill" not in dashboard_source
    assert "restore-drill" not in dashboard_source


def test_restore_drill_executor_job_imports_no_shared_credential_helper() -> None:
    """``db_params_from_env`` and ``DatabaseManager`` were two spellings of one leak."""
    tree = ast.parse(_EXECUTOR.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    butlers_imports = {name for name in imported if name.split(".")[0] == "butlers"}
    assert butlers_imports == _EXECUTOR_ALLOWED_IMPORTS, (
        "restore-drill executor reaches into unexpected butlers modules: "
        f"{sorted(butlers_imports - _EXECUTOR_ALLOWED_IMPORTS)}"
    )


# --------------------------------------------------------------------------- #
# Operator documentation
# --------------------------------------------------------------------------- #

_FENCE = re.compile(r"^```[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
# A recipe an operator can copy out of the docs and run.  Prose that merely
# describes what these commands would do is allowed on purpose -- the previous
# blanket substring bans pushed the operations doc toward vagueness exactly where
# precision matters.
#
# Not caught, enumerated so the narrowed boundary does not read as more precise
# than it is: four-space-indented and HTML ``<pre>`` blocks (neither document
# uses them); indirection such as ``CMD=createdb`` then ``$CMD``; a command split
# so the verb and its object never share a fenced block; and prose urging a
# hand-run recovery without showing the command.  One known false positive, which
# fails safe: a fenced block showing the ``CREATEDB`` role attribute trips the
# restore-database rule, because ``CREATEDB`` is a ``\bcreatedb\b`` match.
_ROLE_WIDENING_RECIPE = re.compile(r"\bALTER\s+ROLE\b", re.IGNORECASE)
_MANUAL_RESTORE_DB_RECIPE = re.compile(r"\b(?:CREATE\s+DATABASE|createdb|dropdb)\b", re.IGNORECASE)
# The historical escape hatch offered the database owner as an acceptable
# bootstrap identity.  Ban the offer, not the six words.
_DATABASE_OWNER_ALLOWANCE = re.compile(
    r"(?:superuser|bootstrap|privileged)[^.\n]{0,60}\bor the database owner\b",
    re.IGNORECASE,
)

# Fixed vocabulary an operator types or a deployment resolves.
_REQUIRED_OPERATIONS_TOKENS = (
    "restore-drill-executor",
    "RESTORE_DRILL_EXECUTOR_PASSWORD_FILE",
    "RESTORE_DRILL_EXECUTOR_SSLROOTCERT_SOURCE_FILE",
    "restore_drill_executor_ca.pem",
    "restore_drill_executor_audit_writer",
    "sslmode=require",
    "verify-full",
    "butlers deploy",
)
# Deliberate wording fences.  These pin prose, not behaviour, and there is no
# behavioural check available for "the document still explains this".  They are
# the only thing between a rewrite and the silent loss of four explanations an
# operator needs.  A rewording that keeps the explanation should update this
# tuple; it should never delete the entry.
_REQUIRED_OPERATIONS_EXPLANATIONS = (
    "single-executor",
    "live application database",
    "cluster-superuser bootstrap",
    "hostile `public.audit_log` trigger",
    "rolls back its ledger insert",
)


def _fenced_code_blocks(markdown: str) -> str:
    """Every ``` fenced block's body, joined.  Indented and ``<pre>`` blocks are not seen."""
    return "\n".join(match.group(1) for match in _FENCE.finditer(markdown))


def _operator_recipe_violations(markdown: str) -> list[str]:
    """Runnable commands that would hand an operator a shared-role escape hatch."""
    runnable = _fenced_code_blocks(markdown)
    return [
        match.group(0)
        for pattern in (_ROLE_WIDENING_RECIPE, _MANUAL_RESTORE_DB_RECIPE)
        for match in pattern.finditer(runnable)
    ]


@pytest.mark.parametrize(
    "body",
    [
        # Only the fixtures carrying an attribute other than CREATEDB pin the
        # ALTER rule on its own; see the false positive noted above.
        "```bash\nALTER ROLE butlers CREATEDB;\n```",
        "```sql\nalter role restore_drill_executor SUPERUSER;\n```",
        "```\nALTER\n  ROLE butlers REPLICATION;\n```",
        "```bash\ncreatedb butlers_restore_drill\n```",
        "```sql\nCREATE DATABASE butlers_restore_drill;\n```",
        "```bash\ndropdb butlers_restore_drill\n```",
    ],
)
def test_operator_recipe_scan_rejects_runnable_escape_hatches(body: str) -> None:
    """Each rule is pinned by a fixture only that rule rejects."""
    assert _operator_recipe_violations(body)


@pytest.mark.parametrize(
    "body",
    [
        "The bootstrap runs ALTER ROLE on every runtime role; do not run it yourself.\n",
        "The executor issues CREATE DATABASE for `butlers_restore_drill` and nothing else.\n",
        "Never invoke `createdb` against the live application database.\n",
        "```dotenv\nRESTORE_DRILL_EXECUTOR_PASSWORD_FILE=/secure/path\n```",
    ],
)
def test_operator_recipe_scan_allows_descriptive_prose(body: str) -> None:
    """Naming the banned operation in prose is what the narrowing exists to permit."""
    assert _operator_recipe_violations(body) == []


def test_operations_document_the_managed_boundary_without_a_live_workaround() -> None:
    """REQ-database-security-006: operators get no shared-role escape hatch."""
    source = _OPERATIONS_DOC.read_text(encoding="utf-8")

    missing = [token for token in _REQUIRED_OPERATIONS_TOKENS if token not in source]
    assert missing == [], f"operations doc no longer names: {missing}"

    unexplained = [phrase for phrase in _REQUIRED_OPERATIONS_EXPLANATIONS if phrase not in source]
    assert unexplained == [], f"operations doc dropped an explanation: {unexplained}"

    assert _operator_recipe_violations(source) == []


@pytest.mark.parametrize("mention", _DESCRIPTIVE_RESTORE_SCRIPT_MENTIONS)
def test_ops_docs_may_name_the_restore_script_descriptively(mention: str) -> None:
    """Naming the restore path is precision, not an escape hatch."""
    assert _restore_script_invocations(mention) == []


@pytest.mark.parametrize("invocation", _RESTORE_SCRIPT_INVOCATIONS)
def test_ops_docs_may_not_show_a_runnable_restore_script_invocation(invocation: str) -> None:
    """REQ-database-security-006 keeps the hand-run escape hatch unreachable.

    This is the load-bearing half of the narrowing: it fails if the invocation
    ban is dropped or weakened back toward allowing a runnable command line.
    """
    assert _restore_script_invocations(invocation), f"invocation not caught: {invocation!r}"


def test_operations_doc_names_the_restore_script_it_describes() -> None:
    """The doc has to be able to say which path it is imposing a precondition on."""
    source = _OPERATIONS_DOC.read_text(encoding="utf-8")

    assert _RESTORE_SCRIPT_NAME in source
    assert _restore_script_invocations(source) == []


def test_no_operator_facing_doc_shows_a_runnable_restore_script_invocation() -> None:
    """REQ-database-security-006: the hand-run escape hatch has nowhere to land.

    Evaluating this over one file left the rest of the operator-facing tree free
    to reintroduce the command line the ban exists to keep out.
    """
    assert _restore_script_invocations_by_doc(_REPO_ROOT) == {}


def test_the_scanned_doc_set_is_the_whole_operator_facing_tree() -> None:
    """A glob narrowed back to an allowlist would reopen the hole silently."""
    covered = {str(path.relative_to(_REPO_ROOT)) for path in _operator_facing_docs(_REPO_ROOT)}

    assert covered >= {
        f"docs/operations/{path.name}" for path in (_REPO_ROOT / "docs" / "operations").glob("*.md")
    }
    assert "docs/operations/backup-restore.md" in covered
    assert "docs/operations/index.md" in covered
    assert "scripts/README.md" in covered


def test_the_synthetic_doc_tree_is_clean_before_anything_is_planted(tmp_path: Path) -> None:
    """Without this, a planted-invocation failure could be the fixture's own noise."""
    _write_operator_facing_doc_tree(tmp_path)

    assert _restore_script_invocations_by_doc(tmp_path) == {}


@pytest.mark.parametrize("relative_path", _DOCS_OUTSIDE_BACKUP_RESTORE)
def test_an_invocation_planted_outside_backup_restore_is_rejected(
    tmp_path: Path, relative_path: str
) -> None:
    """The load-bearing half: the ban is a tree boundary, not a one-file boundary."""
    _write_operator_facing_doc_tree(tmp_path)
    planted = tmp_path / relative_path
    planted.write_text(_PLANTED_INVOCATION_DOC, encoding="utf-8")

    found = _restore_script_invocations_by_doc(tmp_path)

    assert set(found) == {relative_path}, f"invocation not caught in {relative_path}"
    assert found[relative_path]


def test_the_invocation_ban_stops_at_the_operator_facing_boundary(tmp_path: Path) -> None:
    """Record the edge honestly: prose outside the operator tree is not scanned."""
    _write_operator_facing_doc_tree(tmp_path)
    outside = tmp_path / "docs" / "architecture" / "restore-drill.md"
    outside.parent.mkdir(parents=True)
    outside.write_text(_PLANTED_INVOCATION_DOC, encoding="utf-8")

    assert _restore_script_invocations_by_doc(tmp_path) == {}


@pytest.mark.parametrize(
    "body",
    [
        "Run as a privileged cluster superuser or the database owner.\n",
        "The bootstrap identity may be a superuser, or the database owner, either way.\n",
    ],
)
def test_database_owner_allowance_scan_rejects_the_historical_escape_hatch(body: str) -> None:
    assert _DATABASE_OWNER_ALLOWANCE.search(body)


@pytest.mark.parametrize(
    "body",
    [
        # "n|or the database owner": the blunt substring ban rejects this
        # sentence, which says the opposite of the escape hatch.
        "Neither the shared migration login nor the database owner may install it.\n",
        # Same "n|or" overlap, but inside an allowance-keyword sentence, so only
        # the word boundary keeps this correct sentence out of the ban.
        "Only a cluster superuser may bootstrap; neither the migration login nor"
        " the database owner qualifies.\n",
        # A different sentence from the one naming the bootstrap identity.
        "The bootstrap must be a cluster superuser.\nAn operator, or the database"
        " owner, may read this document.\n",
        "The database owner cannot satisfy this provenance condition.\n",
    ],
)
def test_database_owner_allowance_scan_allows_descriptive_prose(body: str) -> None:
    """Each entry is rejected by the blunt ban this predicate replaced."""
    assert _DATABASE_OWNER_ALLOWANCE.search(body) is None


def test_bootstrap_docs_require_a_cluster_superuser_distinct_from_the_migration_user() -> None:
    """Operator docs cannot resurrect the shared-owner bootstrap escape hatch."""
    for path in (_SCRIPTS_README, _SCHEMA_TOPOLOGY_DOC, _COMPOSE_FILE):
        source = path.read_text(encoding="utf-8")
        normalized_source = " ".join(source.split())
        # Fixed vocabulary: the GUC an operator sets.
        assert "butlers.connecting_user" in source, path
        # Deliberate wording fences, as above.
        assert "privileged cluster superuser" in normalized_source, path
        assert "must not be the active bootstrap identity" in normalized_source, path
        assert _DATABASE_OWNER_ALLOWANCE.search(normalized_source) is None, path
