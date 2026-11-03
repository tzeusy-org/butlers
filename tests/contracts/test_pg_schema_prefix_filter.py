"""Schema discovery must not filter system schemas with ``LIKE 'pg_%'``.

``_`` is a LIKE wildcard, so ``NOT LIKE 'pg_%'`` also drops supported butler
schemas such as ``pgpeer`` or ``pga``. Spell the system-schema filter as
``left(<name>, 3) <> 'pg_'`` (the core_291 pattern), which has no wildcard and
no ``%`` for drivers to escape.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCANNED_ROOTS = ("src", "alembic", "roster", "scripts")
_SCANNED_SUFFIXES = frozenset({".py", ".sql", ".sh"})

# A pattern-match operator (LIKE, ILIKE, their ``~~`` spellings, SIMILAR TO)
# whose literal starts with an unescaped ``pg_``: ``_`` is a wildcard in all
# of them, so ``'pg_%'``, ``'pg_toast%'`` and friends also match ``pgpeer``-style
# names. The operator and literal may sit on different lines or in adjacent
# Python string literals. ``pg\_`` escapes the underscore and stays allowed.
_WILDCARD_PREFIX = re.compile(
    r"""(?:\bI?LIKE|~~\*?|\bSIMILAR\s+TO)(?:\s|"\s*[rRfFbBuU]*")*E?'pg_""",
    re.IGNORECASE,
)


def _offenders() -> list[str]:
    hits = []
    for root in _SCANNED_ROOTS:
        for path in sorted((_REPO_ROOT / root).rglob("*")):
            if path.suffix not in _SCANNED_SUFFIXES or not path.is_file():
                continue
            text = path.read_text()
            lines = text.splitlines()
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for match in _WILDCARD_PREFIX.finditer(text):
                lineno = text.count("\n", 0, match.start()) + 1
                hits.append(f"{rel}:{lineno}: {lines[lineno - 1].strip()}")
    return hits


def test_wildcard_detector_matches_both_driver_spellings() -> None:
    assert _WILDCARD_PREFIX.search("AND nspname NOT LIKE 'pg_%'")
    assert _WILDCARD_PREFIX.search("AND namespace.nspname NOT LIKE 'pg_%%'")
    assert not _WILDCARD_PREFIX.search(r"AND nspname NOT LIKE 'pg\_%'")
    assert not _WILDCARD_PREFIX.search("AND left(nspname, 3) <> 'pg_'")


def test_wildcard_detector_covers_every_spelling_of_the_trap() -> None:
    caught = (
        "AND nspname NOT ILIKE 'pg_%'",
        "AND nspname NOT LIKE 'pg_toast%'",
        "AND nspname LIKE E'pg_temp%'",
        "AND nspname !~~ 'pg_%'",
        "AND nspname NOT SIMILAR TO 'pg_%'",
        "AND nspname NOT LIKE\n      'pg_%'",
        "AND nspname NOT LIKE \"\n        \"'pg_%'",
    )
    for sql in caught:
        assert _WILDCARD_PREFIX.search(sql), sql
    assert not _WILDCARD_PREFIX.search("AND nspname NOT ILIKE 'pg\\_toast%'")
    assert not _WILDCARD_PREFIX.search("AND nspname LIKE 'pgpeer%'")


def test_no_like_wildcard_system_schema_filters() -> None:
    assert _offenders() == []
