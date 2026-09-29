"""The one predicate for a safely pinned ``SECURITY DEFINER`` search path (bu-mzm3su.3).

A ``SECURITY DEFINER`` function runs with its owner's privileges, so every name
it resolves through ``search_path`` is a place a less-privileged role can plant
code.  The migration login can ``CREATE`` in ``public``, and runtime roles such
as ``butler_switchboard_rw`` can ``CREATE`` in their own schema.  With either
schema on a definer's path, a better-matching overload (``format(text, text)``
beats the catalog's variadic ``format``) runs as the owner.  Leaving ``pg_temp``
implicit is no better: an implicit ``pg_temp`` is searched *first* for relation
and type names.

The rule is therefore exact: a definer's ``search_path`` entry must be
``pg_catalog, pg_temp`` and nothing else, and its body schema-qualifies every
relation and non-catalog function.  Other ``proconfig`` entries (for example
``row_security=on``) are allowed.

This module is the single implementation of that predicate.  The catalog-wide
guard (``tests/migrations/test_definer_search_path_pins.py``) and the runtime
health probe below both use it rather than restating the rule.

Runtime probe (bu-hefzis)
-------------------------
A database bootstrapped before the pins keeps its old paths until an operator
acts, and the stored-function body drift check compares ``prosrc`` only, so it
cannot see a search path.  :func:`compute_unpinned_definers` reports every
deployed definer that fails :func:`is_pinned`, with its owner, so the operator
can pick the remedy:

- owned by the connecting (migration) login: run migrations to head;
- owned by any other role (a fenced NOLOGIN owner or the bootstrap superuser):
  re-run ``scripts/init-db.sql`` as a cluster superuser.

Reported, never fatal, and never repaired here: the probe only reads
``pg_proc``/``pg_namespace``/``pg_depend``, which every role can read.  A failed
read is an unavailable report, never a false all-clear.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

logger = logging.getLogger(__name__)

PINNED_SEARCH_PATH = "pg_catalog, pg_temp"
"""The only acceptable definer search path, exactly as ``pg_proc`` stores it."""

_SEARCH_PATH_PREFIX = "search_path="

DEFINERS_SQL = """
SELECT format(
           '%I.%I(%s)', n.nspname, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid)
       ) AS signature,
       p.proconfig,
       pg_catalog.pg_get_userbyid(p.proowner) AS owner,
       pg_catalog.pg_get_userbyid(p.proowner) = session_user AS owned_by_connecting_login
FROM pg_catalog.pg_proc AS p
JOIN pg_catalog.pg_namespace AS n ON n.oid = p.pronamespace
WHERE p.prosecdef
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
  AND NOT EXISTS (
      SELECT 1
      FROM pg_catalog.pg_depend AS d
      WHERE d.classid = 'pg_catalog.pg_proc'::pg_catalog.regclass
        AND d.objid = p.oid
        AND d.deptype = 'e'
  )
ORDER BY 1
"""
"""Every application ``SECURITY DEFINER`` function with its ``proconfig`` and owner.

Extension members are excluded: their bodies and settings belong to the
extension, not to this repository.
"""


@dataclass(frozen=True)
class DefinerConfig:
    """One definer's identity and raw ``pg_proc.proconfig``."""

    signature: str
    proconfig: tuple[str, ...]


def search_path_entries(proconfig: Sequence[str] | None) -> list[str]:
    """Return every ``search_path`` value in ``proconfig``, in order."""
    return [
        setting[len(_SEARCH_PATH_PREFIX) :]
        for setting in proconfig or ()
        if setting.startswith(_SEARCH_PATH_PREFIX)
    ]


def is_pinned(proconfig: Sequence[str] | None) -> bool:
    """True when ``proconfig`` sets exactly one search path, ``pg_catalog, pg_temp``.

    A missing search path is unpinned: the definer would inherit the caller's.
    """
    return search_path_entries(proconfig) == [PINNED_SEARCH_PATH]


def unpinned(definers: Iterable[DefinerConfig]) -> list[DefinerConfig]:
    """Return the definers that fail :func:`is_pinned`, preserving order."""
    return [definer for definer in definers if not is_pinned(definer.proconfig)]


#: Remedy for a definer owned by the connecting (migration) login.
REMEDY_MIGRATIONS = "migrations"
#: Remedy for a definer owned by any other role: only the privileged bootstrap
#: can re-pin a function the migration login does not own.
REMEDY_INIT_DB = "init_db"


@dataclass(frozen=True)
class UnpinnedDefiner:
    """One deployed definer whose search path is not exactly ``pg_catalog, pg_temp``."""

    signature: str
    owner: str
    #: The deployed ``search_path`` value, or ``None`` when none is set.
    search_path: str | None
    remedy: str


@dataclass(frozen=True)
class DefinerSearchPathReport:
    """Result of one definer search-path pass."""

    checked_at: datetime
    entries: tuple[UnpinnedDefiner, ...]
    checked_count: int = 0
    #: Non-None when the catalog read itself failed.  Never an all-clear.
    check_error: str | None = None

    @property
    def is_available(self) -> bool:
        return self.check_error is None


async def compute_unpinned_definers(pool: asyncpg.Pool) -> DefinerSearchPathReport:
    """Report every deployed definer that fails :func:`is_pinned`.  Never raises."""
    checked_at = datetime.now(UTC)
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(DEFINERS_SQL)
    except Exception as exc:  # noqa: BLE001 - a degraded check must never crash its caller
        return DefinerSearchPathReport(
            checked_at=checked_at,
            entries=(),
            check_error=f"cannot read pg_proc: {type(exc).__name__}",
        )

    entries = []
    for row in rows:
        proconfig = tuple(row["proconfig"] or ())
        if is_pinned(proconfig):
            continue
        paths = search_path_entries(proconfig)
        entries.append(
            UnpinnedDefiner(
                signature=row["signature"],
                owner=row["owner"],
                search_path="; ".join(paths) if paths else None,
                remedy=REMEDY_MIGRATIONS if row["owned_by_connecting_login"] else REMEDY_INIT_DB,
            )
        )
    return DefinerSearchPathReport(
        checked_at=checked_at, entries=tuple(entries), checked_count=len(rows)
    )


def log_unpinned_definers(report: DefinerSearchPathReport) -> None:
    """Emit one log line summarising *report*.  Signatures, owners and paths only."""
    if not report.is_available:
        logger.warning(
            "Definer search-path check unavailable: %s. SECURITY DEFINER search paths "
            "were NOT checked; this is not a clean result.",
            report.check_error,
        )
        return

    if report.entries:
        logger.warning(
            "Definer search path: %d of %d SECURITY DEFINER functions are not pinned to "
            "'%s' (%s). Owned by the migration login: run migrations to head. Owned by "
            "any other role: re-run scripts/init-db.sql as a cluster superuser.",
            len(report.entries),
            report.checked_count,
            PINNED_SEARCH_PATH,
            ", ".join(
                f"{entry.signature} owner={entry.owner} "
                f"search_path={entry.search_path or '<unset>'}"
                for entry in report.entries
            ),
        )
        return

    logger.info(
        "Definer search path: all %d SECURITY DEFINER functions are pinned to '%s'.",
        report.checked_count,
        PINNED_SEARCH_PATH,
    )
