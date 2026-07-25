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
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.background import dispatch_scheduled_task
from butlers.db import Database, register_jsonb_codec
from butlers.modules.memory import MemoryModule, MemoryModuleConfig
from butlers.modules.memory.storage import store_fact
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


async def _dispatch_chronicler_memory_job(
    *,
    pool: Any,
    spawner: Any,
    job_name: str,
    job_args: dict[str, Any] | None = None,
) -> Any:
    """Run a Chronicler memory job through the production scheduler boundary."""
    return await dispatch_scheduled_task(
        butler_name="chronicler",
        pool=pool,
        spawner=spawner,
        trigger_source=f"schedule:{job_name}",
        job_name=job_name,
        job_args=job_args,
    )


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
async def test_scheduled_maintenance_uses_chronicler_memory_runtime(
    isolated_db_url: str,
) -> None:
    """Every direct maintenance job uses ``chronicler_mem``, never the domain pool."""
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
    spawner = object()
    try:
        await module.on_startup(
            MemoryModuleConfig(memory_schema="chronicler_mem"),
            domain_db,
        )
        memory_pool = module._get_pool()
        assert await memory_pool.fetchval("SELECT current_schema()") == "chronicler_mem"

        # Decay reaches ``memory_policies``, ``facts``, and ``rules``. None of
        # those memory tables exist in the chronicler domain schema.
        decay_result = await _dispatch_chronicler_memory_job(
            pool=domain_db.pool,
            spawner=spawner,
            job_name="memory_decay_sweep",
        )
        assert {"facts_checked", "rules_checked"} <= set(decay_result)

        # Cleanup requires the memory table's ``expires_at`` column, which the
        # domain's independently-owned ``chronicler.episodes`` table lacks. The
        # episode is seeded ``consolidated`` so it is reapable: the sweep is
        # consolidation-aware and protects an expired-but-``pending`` episode
        # within its grace window (see run_episode_cleanup).
        await memory_pool.execute(
            "INSERT INTO episodes (butler, content, consolidation_status, expires_at) "
            "VALUES ('chronicler', 'expired scheduled maintenance episode', "
            "'consolidated', now() - interval '1 day')"
        )
        cleanup_result = await _dispatch_chronicler_memory_job(
            pool=domain_db.pool,
            spawner=spawner,
            job_name="memory_episode_cleanup",
        )
        assert cleanup_result["expired_deleted"] == 1
        assert (
            await memory_pool.fetchval(
                "SELECT count(*) FROM episodes "
                "WHERE content = 'expired scheduled maintenance episode'"
            )
            == 0
        )

        # The backfill must read unqualified facts from chronicler_mem and
        # stamp that private schema into public catalog provenance.
        catalog_fact = await store_fact(
            memory_pool,
            "scheduled-maintenance",
            "catalog_backfill",
            "A fact awaiting scheduled catalog backfill.",
            engine,
            source_butler="chronicler",
        )
        catalog_result = await _dispatch_chronicler_memory_job(
            pool=domain_db.pool,
            spawner=spawner,
            job_name="memory_catalog_backfill",
        )
        assert catalog_result["source_schema"] == "chronicler_mem"
        assert catalog_result["facts_backfilled"] >= 1
        assert (
            await memory_pool.fetchval(
                "SELECT count(*) FROM public.memory_catalog "
                "WHERE source_schema = 'chronicler_mem' "
                "AND source_table = 'facts' AND source_id = $1",
                catalog_fact["id"],
            )
            == 1
        )

        # Purge performs independent deletes from the memory ``facts`` table;
        # it catches per-delete errors, so assert the actual private-schema
        # mutation rather than only a non-error result.
        purge_fact = await store_fact(
            memory_pool,
            "scheduled-maintenance",
            "purge_superseded",
            "A superseded fact awaiting scheduled purge.",
            engine,
            source_butler="chronicler",
        )
        await memory_pool.execute(
            "UPDATE facts SET validity = 'superseded', "
            "created_at = now() - interval '8 days' WHERE id = $1",
            purge_fact["id"],
        )
        purge_result = await _dispatch_chronicler_memory_job(
            pool=domain_db.pool,
            spawner=spawner,
            job_name="memory_purge_superseded",
        )
        assert purge_result["deleted"] == 1
        assert (
            await memory_pool.fetchval("SELECT count(*) FROM facts WHERE id = $1", purge_fact["id"])
            == 0
        )
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

        result = await _dispatch_chronicler_memory_job(
            pool=domain_db.pool,
            spawner=spawner,
            job_name="memory_consolidation",
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
        await module.on_shutdown()
        await domain_db.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_session_hooks_keep_chronicler_private_pool_when_other_daemons_start(
    isolated_db_url: str,
    monkeypatch,
) -> None:
    """General and Travel startup cannot redirect Chronicler session memory.

    The Chronicler module owns a real ``chronicler_mem`` pool in this test.
    General and Travel use lightweight started modules so their later startup
    order reproduces the former process-global last-registration failure.
    """
    from butlers.core.memory_hooks import fetch_memory_context, store_session_episode

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

    chronicler = MemoryModule()
    general = MemoryModule()
    travel = MemoryModule()
    general_domain_pool = object()
    travel_domain_pool = object()
    general_memory_pool = object()
    travel_memory_pool = object()
    context_calls: list[tuple[str, str]] = []
    store_calls: list[tuple[str, str]] = []

    for module, memory_pool in (
        (general, general_memory_pool),
        (travel, travel_memory_pool),
    ):
        monkeypatch.setattr(module, "_ensure_memory_schema_pool", AsyncMock())
        monkeypatch.setattr(module, "_get_pool", lambda pool=memory_pool: pool)
        monkeypatch.setattr(module, "_register_default_maintenance_schedules", AsyncMock())
        module._get_embedding_engine = MagicMock(return_value=MagicMock())

    monkeypatch.setattr(chronicler, "_register_default_maintenance_schedules", AsyncMock())
    chronicler._get_embedding_engine = MagicMock(return_value=MagicMock())

    pool_labels = {
        id(general_memory_pool): "general",
        id(travel_memory_pool): "travel",
    }

    async def _memory_context(pool, _engine, _prompt, butler_name, **_kwargs):
        label = pool_labels[id(pool)]
        context_calls.append((label, butler_name))
        return f"{label} context"

    async def _store_episode(pool, _content, butler_name, **_kwargs):
        label = pool_labels[id(pool)]
        store_calls.append((label, butler_name))
        return {"id": "episode-id"}

    monkeypatch.setattr(
        "butlers.modules.memory.tools.context.memory_context",
        _memory_context,
    )
    monkeypatch.setattr(
        "butlers.modules.memory.tools.writing.memory_store_episode",
        _store_episode,
    )

    started: list[MemoryModule] = []
    try:
        await chronicler.on_startup(
            MemoryModuleConfig(memory_schema="chronicler_mem"),
            domain_db,
        )
        started.append(chronicler)
        chronicler_memory_pool = chronicler._get_pool()
        assert await chronicler_memory_pool.fetchval("SELECT current_schema()") == "chronicler_mem"
        pool_labels[id(chronicler_memory_pool)] = "chronicler_mem"

        await general.on_startup(
            config=None,
            db=SimpleNamespace(schema="general", pool=general_domain_pool),
        )
        started.append(general)
        await travel.on_startup(
            config=None,
            db=SimpleNamespace(schema="travel", pool=travel_domain_pool),
        )
        started.append(travel)

        for owner, domain_pool, expected_context in (
            ("general", general_domain_pool, "general context"),
            ("travel", travel_domain_pool, "travel context"),
            ("chronicler", domain_db.pool, "chronicler_mem context"),
        ):
            assert (
                await fetch_memory_context(domain_pool, owner, f"{owner} prompt")
                == expected_context
            )
            assert await store_session_episode(domain_pool, owner, f"{owner} output") is True

        assert await fetch_memory_context(object(), "stopped", "prompt") is None
        assert await store_session_episode(object(), "stopped", "output") is False
    finally:
        for module in reversed(started):
            await module.on_shutdown()
        await domain_db.close()

    assert context_calls == [
        ("general", "general"),
        ("travel", "travel"),
        ("chronicler_mem", "chronicler"),
    ]
    assert store_calls == [
        ("general", "general"),
        ("travel", "travel"),
        ("chronicler_mem", "chronicler"),
    ]
