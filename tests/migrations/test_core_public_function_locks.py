"""A core migration that writes a ``public.*`` function takes an advisory lock (bu-op5402).

The core chain runs once per butler schema, and the runs overlap. Every ``public`` function is one
``pg_proc`` row shared by all of them, and two transactions updating it fail the loser with "tuple
concurrently updated". Transactions also end at ``autocommit_block()``, so a lock taken early does
not cover a later revision; each writer takes the key that its sibling revisions use.

The sixteen revisions below predate the rule. The list only shrinks: a new revision that creates,
replaces or alters a ``public`` function must lock, and one that gains a lock leaves the list.
"""

from __future__ import annotations

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "alembic/versions/core"

_WRITES_PUBLIC_FUNCTION = re.compile(
    r"(?:CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION|ALTER\s+FUNCTION)\s+(?:IF NOT EXISTS\s+)?public\.",
    re.IGNORECASE,
)

_UNLOCKED_BEFORE_THE_RULE = frozenset(
    f"core_{number}"
    for number in ("101 129 145 148 193 201 205 226 233 255 256 279 282 287 288 307".split())
)


def _unlocked_writers() -> set[str]:
    found = set()
    for path in sorted(CORE.glob("core_*.py")):
        source = path.read_text()
        if _WRITES_PUBLIC_FUNCTION.search(source) and "advisory" not in source.lower():
            found.add(path.name[:8])
    return found


def test_no_new_core_migration_writes_a_public_function_without_an_advisory_lock() -> None:
    new = _unlocked_writers() - _UNLOCKED_BEFORE_THE_RULE
    assert not new, f"take pg_advisory_xact_lock before writing public functions: {sorted(new)}"


def test_the_unlocked_list_only_shrinks() -> None:
    fixed = _UNLOCKED_BEFORE_THE_RULE - _unlocked_writers()
    assert not fixed, f"remove from _UNLOCKED_BEFORE_THE_RULE: {sorted(fixed)}"
