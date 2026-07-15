"""Real-Postgres proof that chronicler memory is isolated in ``chronicler_mem``.

bu-93y4rt / bu-w6jca (owner decision, option 1): the chronicler enables the
shared memory module but routes it to a dedicated private schema
``chronicler_mem`` so the memory module's own ``episodes`` table never collides
with the chronicler's domain ``chronicler.episodes`` table.

These tests migrate the ``core`` + ``chronicler`` (domain) + ``memory`` chains
with the memory chain targeted at ``chronicler_mem`` (exactly what
``lifecycle.py`` step 8 does for chronicler once ``[modules.memory] memory_schema
= "chronicler_mem"`` is set), then prove:

1. Coexistence: ``chronicler.episodes`` (domain) and ``chronicler_mem.episodes``
   (memory) both exist as distinct tables (only the memory one has a ``butler``
   column), and the memory-only tables live in ``chronicler_mem``, never in
   ``chronicler``.
2. Write-path: a fact written through a ``chronicler_mem``-search_path pool
   lands in ``chronicler_mem.facts`` and there is no ``chronicler.facts`` table
   for it to leak into.
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.core.spawn_hooks import clear_spawner, register_spawner
from butlers.db import Database, register_jsonb_codec
from butlers.modules.memory import MemoryModule, MemoryModuleConfig
from butlers.modules.memory.storage import store_fact
from butlers.scheduled_jobs import (
    _run_memory_consolidation_job,
    _run_memory_decay_sweep_job,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.modules.memory._test_helpers import make_embedding_engine_mock

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]

_MEMORY_TABLES = {"episodes", "facts", "rules", "memory_links", "memory_events"}
_MEMORY_ONLY_TABLES = {"facts", "rules", "memory_links", "memory_events"}


@pytest.fixture(scope="module")
def isolated_db_url(postgres_container) -> str:
    """core + chronicler(domain) into ``chronicler``; memory into ``chronicler_mem``."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "chronicler", "memory"],
        schemas={
            "core": "chronicler",
            "chronicler": "chronicler",
            "memory": "chronicler_mem",
        },
    )


def _tables_in_schema(db_url: str, schema: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = :s"),
                {"s": schema},
            )
            return {str(r[0]) for r in rows}
    finally:
        engine.dispose()


def _columns(db_url: str, schema: str, table: str) -> set[str]:
    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": schema, "t": table},
            )
            return {str(r[0]) for r in rows}
    finally:
        engine.dispose()


def test_memory_tables_land_in_chronicler_mem_not_chronicler(isolated_db_url: str) -> None:
    mem = _tables_in_schema(isolated_db_url, "chronicler_mem")
    dom = _tables_in_schema(isolated_db_url, "chronicler")

    # Memory tables exist in chronicler_mem.
    assert _MEMORY_TABLES <= mem, (
        f"memory tables missing from chronicler_mem: {_MEMORY_TABLES - mem}"
    )

    # The chronicler domain schema keeps its own episodes table...
    assert "episodes" in dom
    # ...but NONE of the memory-only tables leaked into it.
    leaked = _MEMORY_ONLY_TABLES & dom
    assert leaked == set(), f"memory-only tables leaked into chronicler schema: {leaked}"


def test_episodes_tables_coexist_as_distinct_tables(isolated_db_url: str) -> None:
    """Both episodes tables exist; only the memory one has a ``butler`` column."""
    dom_cols = _columns(isolated_db_url, "chronicler", "episodes")
    mem_cols = _columns(isolated_db_url, "chronicler_mem", "episodes")

    assert dom_cols, "chronicler.episodes (domain) should exist"
    assert mem_cols, "chronicler_mem.episodes (memory) should exist"
    # The memory episodes table is keyed by butler; the domain one is not. This
    # is the exact column that made CREATE INDEX ... ON episodes (butler, ...)
    # fail when both shared one schema (bu-w6jca root cause).
    assert "butler" in mem_cols
    assert "butler" not in dom_cols


@pytest.mark.asyncio(loop_scope="session")
async def test_fact_write_lands_in_chronicler_mem(isolated_db_url: str) -> None:
    """A memory fact written through a chronicler_mem-search_path pool lands there."""
    engine = make_embedding_engine_mock()
    pool = await asyncpg.create_pool(
        isolated_db_url,
        min_size=1,
        max_size=3,
        server_settings={"search_path": "chronicler_mem,public"},
        init=register_jsonb_codec,
    )
    try:
        result = await store_fact(
            pool,
            "day-2026-07-09",
            "sleep_debt_building",
            "Accumulating sleep debt over the trailing window.",
            engine,
            source_butler="chronicler",
        )
        # store_fact returns {"id": <uuid>, "supersedes_id": ...}.
        fact_id = result["id"]
        assert fact_id is not None

        # The row is in chronicler_mem.facts (schema-qualified read bypasses search_path).
        in_mem = await pool.fetchval(
            "SELECT count(*) FROM chronicler_mem.facts WHERE id = $1", fact_id
        )
        assert in_mem == 1

        # There is no chronicler.facts table for the write to have leaked into.
        chronicler_facts = await pool.fetchval("SELECT to_regclass('chronicler.facts')")
        assert chronicler_facts is None
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_scheduled_decay_sweep_uses_chronicler_memory_runtime(
    isolated_db_url: str,
) -> None:
    """Decay reads memory policies from chronicler_mem, not the domain schema."""
    parsed = urlparse(isolated_db_url)
    domain_db = Database(
        db_name=parsed.path.lstrip("/"),
        schema="chronicler",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        min_pool_size=1,
        max_pool_size=3,
        strict_role_enforcement=False,
    )
    await domain_db.connect()
    module = MemoryModule()
    try:
        await module.on_startup(
            MemoryModuleConfig(memory_schema="chronicler_mem"),
            domain_db,
        )

        result = await _run_memory_decay_sweep_job(
            pool=domain_db.pool,
            job_args=None,
        )

        assert result["facts_checked"] >= 0
        assert result["rules_checked"] >= 0
        assert await module._get_pool().fetchval("SELECT current_schema()") == "chronicler_mem"
    finally:
        await module.on_shutdown()
        await domain_db.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_scheduled_consolidation_uses_chronicler_memory_runtime(
    isolated_db_url: str,
    monkeypatch,
) -> None:
    """The scheduled job uses chronicler_mem plus the configured module engine."""

    class _SuccessfulSpawner:
        def __init__(self) -> None:
            self.trigger_sources: list[str] = []

        async def trigger(self, *, prompt: str, trigger_source: str):
            self.trigger_sources.append(trigger_source)
            return SimpleNamespace(success=True, output="{}", error=None)

    parsed = urlparse(isolated_db_url)
    domain_db = Database(
        db_name=parsed.path.lstrip("/"),
        schema="chronicler",
        host=parsed.hostname or "localhost",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        min_pool_size=1,
        max_pool_size=3,
        strict_role_enforcement=False,
    )
    await domain_db.connect()
    module = MemoryModule()
    engine = make_embedding_engine_mock()
    requested_models: list[str | None] = []

    def _configured_engine(model_name: str | None = None):
        requested_models.append(model_name)
        return engine

    monkeypatch.setattr(
        "butlers.modules.memory.tools.get_embedding_engine",
        _configured_engine,
    )
    spawner = _SuccessfulSpawner()
    register_spawner(spawner)
    try:
        await module.on_startup(
            MemoryModuleConfig(
                memory_schema="chronicler_mem",
                embedding_model="custom-embedding-model",
            ),
            domain_db,
        )
        memory_pool = module._get_pool()
        await memory_pool.execute(
            "INSERT INTO episodes (butler, content) VALUES ('chronicler', 'pending memory')"
        )

        result = await _run_memory_consolidation_job(
            pool=domain_db.pool,
            job_args={"batch_size": 1},
        )

        assert result["episodes_processed"] == 1
        assert result["episodes_consolidated"] == 1
        assert requested_models == ["custom-embedding-model"]
        assert spawner.trigger_sources == ["schedule:consolidation"]
        assert (
            await memory_pool.fetchval(
                "SELECT consolidation_status FROM episodes WHERE content = 'pending memory'"
            )
            == "consolidated"
        )
    finally:
        clear_spawner()
        await module.on_shutdown()
        await domain_db.close()
