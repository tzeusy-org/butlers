"""Historical migration loader and explicitly unshipped temporal-cutover control.

Schema fixtures execute real owning chains. No copied evidence DDL remains here.
Dropping the old index is only a planted disposable future-layout control; no
repository migration has authorized that production cutover.
"""

from __future__ import annotations

import importlib.util
from functools import cache
from pathlib import Path
from types import ModuleType

import asyncpg

_REL_035_PATH = (
    Path(__file__).resolve().parents[1] / "migrations" / "035_entity_fact_effective_time_expand.py"
)

#: Mirrors ``fact_evidence.py::_MAX_TEXT_CHARS`` / the migration's CHECK bound.
MAX_TEXT_CHARS = 512


@cache
def rel_035() -> ModuleType:
    """The rel_035 migration module (its file name is not an importable name)."""
    spec = importlib.util.spec_from_file_location("_migration_rel_035", _REL_035_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def simulate_temporal_cutover(pool: asyncpg.Pool | asyncpg.Connection) -> None:
    """Drop the legacy active-SPO index in a DISPOSABLE test schema.

    This is the post-cutover layout the transition writer must already handle.
    The real cutover migration deliberately does not exist yet (it needs proof
    that no old writer is live); tests use this only to exercise the future
    code path, never as a model for a repository migration.
    """
    await pool.execute(f"DROP INDEX IF EXISTS relationship.{rel_035().LEGACY_SPO_INDEX}")
