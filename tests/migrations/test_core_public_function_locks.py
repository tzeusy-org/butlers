"""A core migration that writes a ``public`` function takes an advisory lock (bu-op5402).

The core chain runs once per butler schema, and the runs overlap. Every ``public`` function is one
``pg_proc`` row shared by all of them, and two transactions updating it fail the loser with "tuple
concurrently updated". Transactions also end at ``autocommit_block()``, so a lock taken early does
not cover a later revision; each writer takes the key that its sibling revisions use.

The eighteen revisions below predate the rule. The list only shrinks: a new revision that creates,
replaces or alters a ``public`` function must lock, and one that gains a lock leaves the list.
"""

from __future__ import annotations

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "alembic/versions/core"

# ``{`` is a name held in a constant or f-string, which is how newer revisions spell
# ``public.<function>``; treating it as a write keeps the guard from missing them.
_WRITES_FUNCTION = re.compile(
    r"(?:CREATE\s+(?:OR\s+REPLACE\s+)?|ALTER\s+|DROP\s+)FUNCTION\s+"
    r"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?(?:public\.|\{)",
    re.IGNORECASE,
)
# A call, not the word: docstrings and comments mention advisory locks without taking one.
_TAKES_ADVISORY_LOCK = re.compile(r"pg_advisory_(?:xact_)?lock\w*\s*\(", re.IGNORECASE)

_UNLOCKED_BEFORE_THE_RULE = frozenset(
    f"core_{number}"
    for number in (
        "101 129 145 148 193 201 205 212 226 233 255 256 279 282 287 288 307 310".split()
    )
)


def writes_function_unlocked(source: str) -> bool:
    return bool(_WRITES_FUNCTION.search(source)) and not _TAKES_ADVISORY_LOCK.search(source)


def _unlocked_writers() -> set[str]:
    return {
        path.name[:8]
        for path in sorted(CORE.glob("core_*.py"))
        if writes_function_unlocked(path.read_text())
    }


def test_a_constant_named_function_writer_without_a_lock_is_caught() -> None:
    source = 'op.execute(f"CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger ...")'
    assert writes_function_unlocked(source)
    assert writes_function_unlocked('op.execute("DROP FUNCTION IF EXISTS public.f()")')


def test_a_docstring_mention_of_advisory_locks_is_not_a_lock() -> None:
    source = (
        '"""Serialized by an advisory lock elsewhere."""\n'
        'op.execute("ALTER FUNCTION public.f() SET search_path = pg_catalog")'
    )
    assert writes_function_unlocked(source)
    locked = source + "\nop.execute(\"SELECT pg_advisory_xact_lock(hashtext('k'))\")"
    assert not writes_function_unlocked(locked)


def test_no_new_core_migration_writes_a_public_function_without_an_advisory_lock() -> None:
    new = _unlocked_writers() - _UNLOCKED_BEFORE_THE_RULE
    assert not new, f"take pg_advisory_xact_lock before writing public functions: {sorted(new)}"


def test_the_unlocked_list_only_shrinks() -> None:
    fixed = _UNLOCKED_BEFORE_THE_RULE - _unlocked_writers()
    assert not fixed, f"remove from _UNLOCKED_BEFORE_THE_RULE: {sorted(fixed)}"
