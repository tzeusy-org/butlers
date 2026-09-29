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
health surface (bu-hefzis) both import it rather than restating the rule.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

PINNED_SEARCH_PATH = "pg_catalog, pg_temp"
"""The only acceptable definer search path, exactly as ``pg_proc`` stores it."""

_SEARCH_PATH_PREFIX = "search_path="

DEFINERS_SQL = """
SELECT format(
           '%I.%I(%s)', n.nspname, p.proname, pg_catalog.pg_get_function_identity_arguments(p.oid)
       ) AS signature,
       p.proconfig
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
"""Every application ``SECURITY DEFINER`` function with its ``proconfig``.

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
