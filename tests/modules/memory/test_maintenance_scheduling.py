"""Tests for the memory maintenance lifecycle wiring (bu-qvnce.3).

Covers what was previously entirely dormant (see
docs/redesigns/2026-07-04-jarvis-pursuit.md #3):

  (a) Unit — ``memory_decay_sweep`` is a registered deterministic-job handler,
      and every roster butler with the memory module enabled has all four
      memory maintenance job names dispatchable in
      ``get_deterministic_schedule_job_registry()``.
  (b) Unit — job_args validation for the decay-sweep and consolidation
      (batch_size override) handlers.
  (c) Unit — ``MemoryModule._register_default_maintenance_schedules`` calls
      ``ensure_module_default_schedule`` once per default schedule, and a
      failure on one entry does not raise (best-effort) or skip the rest.
  (d) Integration (real Postgres) — ``ensure_module_default_schedule``
      idempotency across repeated calls, and the "TOML overrides cadence, not
      existence" reclaim handshake with ``sync_schedules``.
  (e) Integration (real Postgres) — ``run_decay_sweep`` actually fades/expires
      facts and rules per ``memory_policies`` thresholds, writing the
      ``validity`` column (bu-5ud8p.1) rather than only ``metadata.status``.
  (f) Integration (real Postgres) — the ``memory_consolidation`` job_args
      batch_size override bounds how many pending episodes are claimed in one
      run, and dead_letter episodes are never reclaimed.
  (g) Integration (real Postgres) — the ``memory_stats`` MCP tool (a reader
      named in bu-5ud8p.1) surfaces the sweep's fading count via
      ``validity = 'fading'``, and a re-swept fact recovers to 'active'.
  (h) Integration (real Postgres) — ``store_fact`` supersession still finds
      and supersedes a fading fact for the same predicate (bu-5ud8p.1: the
      sweep must not fight the write path by making fading facts invisible
      to supersession lookups).
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import asyncpg
import pytest

from butlers.db import register_jsonb_codec
from butlers.modules.memory import MemoryModule
from butlers.scheduled_jobs import (
    _MEMORY_MAINTENANCE_JOB_HANDLERS,
    _run_memory_catalog_backfill_job,
    _run_memory_consolidation_job,
    _run_memory_decay_sweep_job,
    _run_memory_episode_cleanup_job,
    _run_memory_purge_superseded_job,
    get_deterministic_schedule_job_registry,
)
from butlers.testing.migration import create_migrated_test_db, migration_db_name

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Butlers whose butler.toml enables [modules.memory] (per roster grep).
_MEMORY_ENABLED_BUTLERS = (
    "general",
    "health",
    "lifestyle",
    "home",
    "switchboard",
    "education",
    "relationship",
    "travel",
    "finance",
    "chronicler",  # bu-93y4rt: day-close memory write-back loop
)

_EXPECTED_MEMORY_JOB_NAMES = {
    "memory_consolidation",
    "memory_episode_cleanup",
    "memory_purge_superseded",
    "memory_decay_sweep",
    "memory_catalog_backfill",
}


def _register_runtime_pool(monkeypatch, pool: Any) -> None:
    """Make a unit test's pool look like the started MemoryModule runtime."""
    monkeypatch.setattr(
        "butlers.core.memory_hooks.resolve_memory_runtime_pool",
        lambda: pool,
    )


# ---------------------------------------------------------------------------
# (a) Handler + registry coverage
# ---------------------------------------------------------------------------


def test_memory_decay_sweep_is_a_registered_handler() -> None:
    """memory_decay_sweep must be dispatchable — it previously had no handler at all."""
    assert _MEMORY_MAINTENANCE_JOB_HANDLERS["memory_decay_sweep"] is _run_memory_decay_sweep_job
    assert _EXPECTED_MEMORY_JOB_NAMES <= set(_MEMORY_MAINTENANCE_JOB_HANDLERS)


def test_memory_catalog_backfill_is_a_registered_handler() -> None:
    """memory_catalog_backfill (bu-qvnce.15) must be dispatchable."""
    assert (
        _MEMORY_MAINTENANCE_JOB_HANDLERS["memory_catalog_backfill"]
        is _run_memory_catalog_backfill_job
    )


@pytest.mark.parametrize("butler_name", _MEMORY_ENABLED_BUTLERS)
def test_every_memory_enabled_butler_has_all_four_maintenance_jobs(butler_name: str) -> None:
    """Every butler with [modules.memory] must resolve all four job names.

    Regression guard for the original gap: finance/travel/education had the
    memory module enabled in butler.toml but no memory maintenance handlers
    at all in the deterministic job registry (switchboard had handlers but no
    schedule). A module-registered default schedule dispatching to a butler
    missing from this registry would fail at dispatch time with "unknown
    deterministic job".
    """
    toml_path = _REPO_ROOT / "roster" / butler_name / "butler.toml"
    with toml_path.open("rb") as fh:
        config = tomllib.load(fh)
    assert "memory" in config.get("modules", {}), (
        f"test fixture drift: {butler_name!r} no longer enables [modules.memory]"
    )

    registry = get_deterministic_schedule_job_registry()
    assert butler_name in registry, f"{butler_name!r} missing from deterministic job registry"
    missing = _EXPECTED_MEMORY_JOB_NAMES - set(registry[butler_name])
    assert not missing, f"{butler_name!r} registry missing memory job(s): {sorted(missing)}"


def test_roster_memory_schedule_blocks_were_removed() -> None:
    """The copy-pasted [[butler.schedule]] memory blocks are gone.

    They are now module defaults (MemoryModule.on_startup); a butler.toml may
    still add one back to override cadence, but none should by default.
    """
    for butler_name in _MEMORY_ENABLED_BUTLERS:
        toml_path = _REPO_ROOT / "roster" / butler_name / "butler.toml"
        with toml_path.open("rb") as fh:
            config = tomllib.load(fh)
        schedule_names = {s.get("name") for s in config.get("butler", {}).get("schedule", [])}
        overlap = schedule_names & _EXPECTED_MEMORY_JOB_NAMES
        assert not overlap, (
            f"{butler_name!r} still declares copy-pasted memory schedule(s) {sorted(overlap)}; "
            "these are module defaults now — remove the toml block or this test needs updating "
            "if the override is intentional"
        )


# ---------------------------------------------------------------------------
# (b) job_args validation
# ---------------------------------------------------------------------------


class TestMemoryMaintenanceRuntimePool:
    async def test_every_direct_maintenance_job_uses_the_registered_runtime_pool(
        self, monkeypatch
    ) -> None:
        """Private memory schemas must never fall back to the daemon pool.

        The scheduler's deterministic-job interface always supplies the daemon
        pool.  The active MemoryModule owns the authoritative runtime pool,
        which may instead be scoped to a private schema such as
        ``chronicler_mem``.  Exercise every direct maintenance path here; the
        consolidation path has its own Spawner/runtime-hook coverage below.
        """
        daemon_pool = object()
        memory_pool = object()
        resolve_runtime_pool = MagicMock(return_value=memory_pool)
        fetch_policy = AsyncMock(return_value={})
        table_size = AsyncMock(return_value=None)
        infer_schema = AsyncMock(return_value="chronicler_mem")
        run_decay_sweep = AsyncMock(return_value={"facts_checked": 0})
        run_episode_cleanup = AsyncMock(return_value={"expired_deleted": 0, "capacity_deleted": 0})
        purge_superseded_facts = AsyncMock(return_value={"deleted": 0, "deleted_ha_state": 0})
        run_catalog_backfill = AsyncMock(return_value={"facts_backfilled": 0})

        monkeypatch.setattr(
            "butlers.core.memory_hooks.resolve_memory_runtime_pool",
            resolve_runtime_pool,
            raising=False,
        )
        monkeypatch.setattr("butlers.scheduled_jobs._fetch_retention_policy", fetch_policy)
        monkeypatch.setattr("butlers.scheduled_jobs._table_size_bytes", table_size)
        monkeypatch.setattr("butlers.scheduled_jobs._infer_current_schema", infer_schema)
        monkeypatch.setattr("butlers.modules.memory.storage.run_decay_sweep", run_decay_sweep)
        monkeypatch.setattr(
            "butlers.modules.memory.consolidation.run_episode_cleanup", run_episode_cleanup
        )
        monkeypatch.setattr(
            "butlers.modules.memory.storage.purge_superseded_facts", purge_superseded_facts
        )
        monkeypatch.setattr(
            "butlers.modules.memory.storage.run_memory_catalog_backfill", run_catalog_backfill
        )

        await _run_memory_decay_sweep_job(pool=daemon_pool, job_args=None)
        await _run_memory_episode_cleanup_job(pool=daemon_pool, job_args=None)
        await _run_memory_purge_superseded_job(pool=daemon_pool, job_args=None)
        await _run_memory_catalog_backfill_job(pool=daemon_pool, job_args=None)

        assert resolve_runtime_pool.call_args_list == [call(), call(), call(), call()]
        run_decay_sweep.assert_awaited_once_with(memory_pool)
        run_episode_cleanup.assert_awaited_once_with(pool=memory_pool, max_entries=10000)
        purge_superseded_facts.assert_awaited_once_with(memory_pool, older_than_days=7)
        infer_schema.assert_awaited_once_with(memory_pool)
        run_catalog_backfill.assert_awaited_once_with(
            memory_pool,
            source_schema="chronicler_mem",
            batch_size=200,
        )

    async def test_direct_maintenance_fails_closed_without_a_runtime_pool_hook(
        self, monkeypatch
    ) -> None:
        """A stopped or missing MemoryModule must surface a scheduler error."""
        import butlers.core.memory_hooks as memory_hooks

        monkeypatch.setattr(memory_hooks, "_memory_runtime_pool_hook", None, raising=False)
        monkeypatch.setattr(
            "butlers.modules.memory.storage.run_decay_sweep",
            AsyncMock(return_value={"facts_checked": 0}),
        )

        with pytest.raises(RuntimeError, match="memory module runtime pool hook"):
            await _run_memory_decay_sweep_job(pool=object(), job_args=None)


class TestJobArgsValidation:
    async def test_decay_sweep_rejects_any_job_args(self) -> None:
        with pytest.raises(RuntimeError, match="does not accept job_args"):
            await _run_memory_decay_sweep_job(pool=AsyncMock(), job_args={"foo": "bar"})

    async def test_decay_sweep_accepts_none_or_empty(self, monkeypatch) -> None:
        called = {}

        async def _fake_run_decay_sweep(pool):
            called["pool"] = pool
            return {"facts_checked": 0}

        monkeypatch.setattr("butlers.modules.memory.storage.run_decay_sweep", _fake_run_decay_sweep)
        pool = object()
        _register_runtime_pool(monkeypatch, pool)
        result = await _run_memory_decay_sweep_job(pool=pool, job_args=None)
        assert result == {"facts_checked": 0}
        assert called["pool"] is pool

        result = await _run_memory_decay_sweep_job(pool=pool, job_args={})
        assert result == {"facts_checked": 0}

    async def test_consolidation_batch_size_override_is_validated(self, monkeypatch) -> None:
        spawner = object()
        consolidate_memory = AsyncMock(return_value={"episodes_processed": 0})

        monkeypatch.setattr("butlers.core.memory_hooks.consolidate_memory", consolidate_memory)
        monkeypatch.setattr("butlers.core.spawn_hooks.get_spawner", lambda: spawner)

        # No job_args -> DEFAULT_BATCH_SIZE.
        from butlers.modules.memory.consolidation import DEFAULT_BATCH_SIZE

        await _run_memory_consolidation_job(pool=object(), job_args=None)
        consolidate_memory.assert_awaited_once_with(
            spawner=spawner,
            batch_size=DEFAULT_BATCH_SIZE,
            enable_shared_catalog=True,
        )

        # Valid override.
        consolidate_memory.reset_mock()
        await _run_memory_consolidation_job(pool=object(), job_args={"batch_size": 500})
        consolidate_memory.assert_awaited_once_with(
            spawner=spawner,
            batch_size=500,
            enable_shared_catalog=True,
        )

        # Invalid overrides raise.
        for bad_args in ({"batch_size": 0}, {"batch_size": -1}, {"batch_size": True}):
            with pytest.raises(RuntimeError, match="positive integer"):
                await _run_memory_consolidation_job(pool=object(), job_args=bad_args)

        with pytest.raises(RuntimeError, match="unsupported keys"):
            await _run_memory_consolidation_job(pool=object(), job_args={"unknown": 1})

    async def test_consolidation_requires_registered_runtime_hooks(self, monkeypatch) -> None:
        import butlers.core.memory_hooks as memory_hooks

        monkeypatch.setattr("butlers.core.spawn_hooks.get_spawner", lambda: object())
        monkeypatch.setattr(memory_hooks, "_memory_consolidation_hook", None)

        with pytest.raises(RuntimeError, match="memory module runtime hook"):
            await _run_memory_consolidation_job(pool=object(), job_args=None)


class TestCatalogBackfillJobArgsValidation:
    """job_args + source_schema-inference validation for memory_catalog_backfill."""

    async def test_defaults_use_inferred_schema_and_default_batch_size(self, monkeypatch) -> None:
        captured: dict[str, Any] = {}

        async def _fake_backfill(pool, *, source_schema, batch_size):
            captured["source_schema"] = source_schema
            captured["batch_size"] = batch_size
            return {"facts_backfilled": 0, "rules_backfilled": 0, "source_schema": source_schema}

        monkeypatch.setattr(
            "butlers.modules.memory.storage.run_memory_catalog_backfill", _fake_backfill
        )

        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="health")
        _register_runtime_pool(monkeypatch, pool)
        result = await _run_memory_catalog_backfill_job(pool=pool, job_args=None)

        assert captured["source_schema"] == "health"
        assert captured["batch_size"] == 200
        assert result["source_schema"] == "health"

    async def test_unresolved_schema_skips_without_raising(self, monkeypatch) -> None:
        pool = AsyncMock()
        # current_schema() resolves to 'public' -- treated as unresolved.
        pool.fetchval = AsyncMock(return_value="public")
        _register_runtime_pool(monkeypatch, pool)
        result = await _run_memory_catalog_backfill_job(pool=pool, job_args=None)
        assert result == {
            "facts_backfilled": 0,
            "rules_backfilled": 0,
            "facts_reconciled": 0,
            "rules_reconciled": 0,
            "skipped": "source_schema_not_resolved",
        }

    async def test_current_schema_query_failure_skips_without_raising(self, monkeypatch) -> None:
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("connection lost"))
        _register_runtime_pool(monkeypatch, pool)
        result = await _run_memory_catalog_backfill_job(pool=pool, job_args=None)
        assert result["skipped"] == "source_schema_not_resolved"

    async def test_explicit_source_schema_override_skips_inference(self, monkeypatch) -> None:
        captured: dict[str, Any] = {}

        async def _fake_backfill(pool, *, source_schema, batch_size):
            captured["source_schema"] = source_schema
            captured["batch_size"] = batch_size
            return {"facts_backfilled": 1, "rules_backfilled": 2, "source_schema": source_schema}

        monkeypatch.setattr(
            "butlers.modules.memory.storage.run_memory_catalog_backfill", _fake_backfill
        )

        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=AssertionError("should not be called"))
        _register_runtime_pool(monkeypatch, pool)
        result = await _run_memory_catalog_backfill_job(
            pool=pool, job_args={"source_schema": "finance", "batch_size": 50}
        )

        assert captured["source_schema"] == "finance"
        assert captured["batch_size"] == 50
        assert result["facts_backfilled"] == 1

    async def test_invalid_batch_size_raises(self) -> None:
        for bad_args in ({"batch_size": 0}, {"batch_size": -1}, {"batch_size": True}):
            with pytest.raises(RuntimeError, match="positive integer"):
                await _run_memory_catalog_backfill_job(pool=AsyncMock(), job_args=bad_args)

    async def test_invalid_source_schema_raises(self) -> None:
        for bad_args in ({"source_schema": ""}, {"source_schema": "   "}, {"source_schema": 1}):
            with pytest.raises(RuntimeError, match="non-empty string"):
                await _run_memory_catalog_backfill_job(pool=AsyncMock(), job_args=bad_args)

    async def test_unsupported_job_args_key_raises(self) -> None:
        with pytest.raises(RuntimeError, match="unsupported keys"):
            await _run_memory_catalog_backfill_job(pool=AsyncMock(), job_args={"unknown": 1})


# ---------------------------------------------------------------------------
# (c) MemoryModule._register_default_maintenance_schedules
# ---------------------------------------------------------------------------


class TestRegisterDefaultMaintenanceSchedules:
    async def test_calls_ensure_for_every_default_schedule(self, monkeypatch) -> None:
        from butlers.modules.memory import _DEFAULT_MAINTENANCE_SCHEDULES

        calls: list[dict[str, Any]] = []

        async def _fake_ensure(pool, **kwargs):
            calls.append(kwargs)

        monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _fake_ensure)

        module = MemoryModule()
        fake_db = AsyncMock()
        fake_db.pool = object()
        await module._register_default_maintenance_schedules(fake_db)

        assert len(calls) == len(_DEFAULT_MAINTENANCE_SCHEDULES)
        called_names = {c["name"] for c in calls}
        expected_names = {entry["name"] for entry in _DEFAULT_MAINTENANCE_SCHEDULES}
        assert called_names == expected_names
        backfill = next(c for c in calls if c["name"] == "memory_consolidation_backfill")
        assert backfill["job_name"] == "memory_consolidation"
        assert backfill["job_args"] == {"batch_size": 500}

        catalog_backfill = next(c for c in calls if c["name"] == "memory_catalog_backfill")
        assert catalog_backfill["job_name"] == "memory_catalog_backfill"

    async def test_none_db_is_a_noop(self) -> None:
        module = MemoryModule()
        # Should not raise even though db is None (e.g. some test harnesses).
        await module._register_default_maintenance_schedules(None)

    async def test_one_failure_does_not_block_the_rest(self, monkeypatch) -> None:
        calls: list[str] = []

        async def _flaky_ensure(pool, **kwargs):
            calls.append(kwargs["name"])
            if kwargs["name"] == "memory_decay_sweep":
                raise RuntimeError("scheduled_tasks not migrated yet")

        monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _flaky_ensure)

        module = MemoryModule()
        fake_db = AsyncMock()
        fake_db.pool = object()
        # Must not raise despite one entry failing.
        await module._register_default_maintenance_schedules(fake_db)

        from butlers.modules.memory import _DEFAULT_MAINTENANCE_SCHEDULES

        assert len(calls) == len(_DEFAULT_MAINTENANCE_SCHEDULES)


# ---------------------------------------------------------------------------
# Integration fixtures (real Postgres via testcontainers)
# ---------------------------------------------------------------------------

docker_available = shutil.which("docker") is not None
_integration = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def core_memory_db_url(postgres_container) -> str:
    """A fresh DB with the core + memory migration chains applied."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "memory"],
    )


async def _pool_for(db_url: str) -> asyncpg.Pool:
    """Create a pool with the JSONB codec registered (mirrors ``Database.connect()``).

    Without this, asyncpg returns/expects raw JSON strings for JSONB columns
    instead of Python dicts — ``run_decay_sweep`` (like all production code)
    relies on the codec being registered by ``Database.connect()``.
    """
    return await asyncpg.create_pool(dsn=db_url, min_size=1, max_size=3, init=register_jsonb_codec)


# ---------------------------------------------------------------------------
# (d) ensure_module_default_schedule idempotency + TOML reclaim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
async def test_ensure_module_default_schedule_idempotent_and_toml_overrides_cadence(
    core_memory_db_url: str,
) -> None:
    from butlers.core.scheduler import ensure_module_default_schedule, sync_schedules

    pool = await _pool_for(core_memory_db_url)
    try:
        # First boot: creates the row.
        await ensure_module_default_schedule(
            pool,
            name="memory_decay_sweep",
            cron="15 3 * * *",
            job_name="memory_decay_sweep",
        )
        row = await pool.fetchrow(
            "SELECT cron, job_name, source, enabled, next_run_at "
            "FROM scheduled_tasks WHERE name = 'memory_decay_sweep'"
        )
        assert row is not None
        assert row["cron"] == "15 3 * * *"
        assert row["source"] == "db"
        assert row["enabled"] is True
        assert row["next_run_at"] is not None

        # Simulate an operator customizing cadence directly via the DB path
        # (source stays 'db', mirroring schedule_update / schedule_create).
        await pool.execute(
            "UPDATE scheduled_tasks SET cron = '0 5 * * *' WHERE name = 'memory_decay_sweep'"
        )

        # Second boot: must be a no-op — the operator's custom cron survives.
        await ensure_module_default_schedule(
            pool,
            name="memory_decay_sweep",
            cron="15 3 * * *",
            job_name="memory_decay_sweep",
        )
        row = await pool.fetchrow(
            "SELECT cron, source FROM scheduled_tasks WHERE name = 'memory_decay_sweep'"
        )
        assert row["cron"] == "0 5 * * *", "existing operator cadence must not be clobbered"
        assert row["source"] == "db"

        # No duplicate row was created (name is UNIQUE, but assert count too).
        count = await pool.fetchval(
            "SELECT count(*) FROM scheduled_tasks WHERE name = 'memory_decay_sweep'"
        )
        assert count == 1

        # --- TOML override precedence: "cadence, not existence" ---
        # An operator adds a [[butler.schedule]] block for the same name.
        await sync_schedules(
            pool,
            [
                {
                    "name": "memory_decay_sweep",
                    "cron": "30 2 * * *",
                    "dispatch_mode": "job",
                    "job_name": "memory_decay_sweep",
                }
            ],
        )
        row = await pool.fetchrow(
            "SELECT cron, source, enabled FROM scheduled_tasks WHERE name = 'memory_decay_sweep'"
        )
        assert row["source"] == "toml"
        assert row["cron"] == "30 2 * * *", "TOML cron must win once declared"
        assert row["enabled"] is True

        # Operator removes the TOML block again. The module re-registers on
        # the next boot *before* sync_schedules runs (matching lifecycle.py
        # step ordering: module on_startup before TOML sync) — this must
        # reclaim the row so the subsequent (empty) TOML sync does not treat
        # it as an orphaned TOML schedule and disable it.
        await ensure_module_default_schedule(
            pool,
            name="memory_decay_sweep",
            cron="15 3 * * *",
            job_name="memory_decay_sweep",
        )
        await sync_schedules(pool, [])  # TOML no longer declares this schedule
        row = await pool.fetchrow(
            "SELECT cron, source, enabled FROM scheduled_tasks WHERE name = 'memory_decay_sweep'"
        )
        assert row["enabled"] is True, (
            "module-reclaimed schedule must survive TOML block removal, not be disabled"
        )
        assert row["source"] == "db"
        assert row["cron"] == "30 2 * * *", "reclaim preserves the last-known cron, not a reset"
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
async def test_ensure_module_default_schedule_rejects_invalid_cron(
    core_memory_db_url: str,
) -> None:
    from butlers.core.scheduler import ensure_module_default_schedule

    pool = await _pool_for(core_memory_db_url)
    try:
        with pytest.raises(ValueError, match="invalid cron"):
            await ensure_module_default_schedule(
                pool,
                name="memory_decay_sweep_bad",
                cron="not-a-cron",
                job_name="memory_decay_sweep",
            )
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# (e) run_decay_sweep real mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
@pytest.mark.pg_clock
async def test_run_decay_sweep_fades_and_expires_facts_and_rules(core_memory_db_url: str) -> None:
    from butlers.modules.memory.storage import run_decay_sweep

    pool = await _pool_for(core_memory_db_url)
    try:
        # Healthy fact: confidence/decay_rate defaults, just-confirmed -> untouched.
        await pool.execute(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, retention_class) "
            "VALUES ('owner', 'likes', 'coffee', 1.0, 0.008, now(), 'operational')"
        )
        # Fading fact: eff = 0.3 * exp(-0.5*1) ~= 0.182, in [0.05, 0.2).
        await pool.execute(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, retention_class) "
            "VALUES ('owner', 'prefers', 'tea', 0.3, 0.5, now() - interval '1 day', "
            "'operational')"
        )
        # Plain-expired fact (no archive): eff = 0.3 * exp(-2*2) ~= 0.0055, < 0.05.
        expired_id = await pool.fetchval(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, retention_class) "
            "VALUES ('owner', 'used_to_like', 'stale-fact', 0.3, 2.0, "
            "now() - interval '2 days', 'operational') RETURNING id"
        )
        # Archive-before-delete class (health_log): same math, must archive then expire.
        archived_id = await pool.fetchval(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, retention_class) "
            "VALUES ('owner', 'had_symptom', 'old-symptom', 0.3, 2.0, "
            "now() - interval '2 days', 'health_log') RETURNING id"
        )
        # Permanent fact (decay_rate=0.0) must be excluded entirely, even with
        # a confidence value that would otherwise expire.
        permanent_id = await pool.fetchval(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, retention_class) "
            "VALUES ('owner', 'is', 'permanent-fact', 0.01, 0.0, "
            "now() - interval '365 days', 'personal_profile') RETURNING id"
        )

        # Fading rule.
        await pool.execute(
            "INSERT INTO rules (content, confidence, decay_rate, last_confirmed_at, "
            "retention_class) VALUES ('always greet the owner', 0.3, 0.5, "
            "now() - interval '1 day', 'rule')"
        )
        # Expired rule -> forgotten=true.
        forgotten_rule_id = await pool.fetchval(
            "INSERT INTO rules (content, confidence, decay_rate, last_confirmed_at, "
            "retention_class) VALUES ('outdated heuristic', 0.3, 2.0, "
            "now() - interval '2 days', 'rule') RETURNING id"
        )

        stats = await run_decay_sweep(pool)

        assert stats["facts_checked"] == 4  # permanent fact excluded (decay_rate=0.0)
        assert stats["facts_fading"] == 1
        assert stats["facts_expired"] == 2  # plain + archive-before-delete
        assert stats["rules_checked"] == 2
        assert stats["rules_fading"] == 1
        assert stats["rules_expired"] == 1

        healthy_row = await pool.fetchrow(
            "SELECT validity, metadata->>'status' AS status FROM facts WHERE content = 'coffee'"
        )
        assert healthy_row["validity"] == "active"
        assert healthy_row["status"] is None

        # bu-5ud8p.1: the fading signal is the validity COLUMN, not
        # metadata.status — every reader (dashboard API, memory_stats MCP
        # tool) queries `validity = 'fading'`. The legacy metadata.status key
        # must also be gone (superseded by the column, not dual-written).
        fading_row = await pool.fetchrow(
            "SELECT validity, metadata->>'status' AS status FROM facts WHERE content = 'tea'"
        )
        assert fading_row["validity"] == "fading"
        assert fading_row["status"] is None

        expired_validity = await pool.fetchval(
            "SELECT validity FROM facts WHERE id = $1", expired_id
        )
        assert expired_validity == "expired"

        archived_row = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1", archived_id
        )
        assert archived_row["validity"] == "expired"
        assert archived_row["metadata"].get("archived_content") is True

        permanent_row = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1", permanent_id
        )
        assert permanent_row["validity"] == "active"
        assert not permanent_row["metadata"].get("status")

        forgotten = await pool.fetchval(
            "SELECT (metadata->>'forgotten')::boolean FROM rules WHERE id = $1",
            forgotten_rule_id,
        )
        assert forgotten is True
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
@pytest.mark.pg_clock
async def test_run_decay_sweep_recovers_fading_fact_to_active(core_memory_db_url: str) -> None:
    """A fading fact whose confidence clock is reset (memory_confirm) recovers
    to validity='active' on the next sweep, and the stale metadata.status key
    (if any) is cleared — proving the sweep re-selects 'fading' rows instead
    of only ever selecting 'active' ones (which would strand them forever).
    """
    from butlers.modules.memory.storage import run_decay_sweep

    pool = await _pool_for(core_memory_db_url)
    try:
        # Distinct (scope, subject, predicate) from the earlier fade test's
        # ('owner','prefers') row, which persists as 'fading' in this module-scoped
        # shared DB. The widened partial unique index (mem_009, bu-agj5a) covers
        # validity IN ('active','fading'), so reusing that key would collide.
        fact_id = await pool.fetchval(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, retention_class) "
            "VALUES ('owner', 'prefers_recovery', 'oolong', 0.3, 0.5, now() - interval '1 day', "
            "'operational') RETURNING id"
        )

        # NOTE: this file's core_memory_db_url fixture is module-scoped and
        # shared across every test in this file, so facts_fading is a running
        # total (earlier tests' fading facts persist) — assert this fact's
        # own row rather than an exact aggregate count.
        first = await run_decay_sweep(pool)
        assert first["facts_fading"] >= 1
        row = await pool.fetchrow("SELECT validity, metadata FROM facts WHERE id = $1", fact_id)
        assert row["validity"] == "fading"

        # memory_confirm(...) resets the decay clock — simulate directly.
        await pool.execute("UPDATE facts SET last_confirmed_at = now() WHERE id = $1", fact_id)

        second = await run_decay_sweep(pool)
        # The now-healthy fact must be re-checked (not skipped because it was
        # 'fading' rather than 'active') and recover.
        assert second["facts_checked"] >= 1
        recovered = await pool.fetchrow(
            "SELECT validity, metadata FROM facts WHERE id = $1", fact_id
        )
        assert recovered["validity"] == "active"
        assert not recovered["metadata"].get("status")
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
@pytest.mark.pg_clock
async def test_memory_stats_reader_surfaces_swept_fading_count(core_memory_db_url: str) -> None:
    """The memory_stats MCP tool — a reader named explicitly in bu-5ud8p.1 —
    must count a sweep-produced fading fact via validity='fading', matching
    the dashboard API's own query (src/butlers/api/routers/memory.py).
    """
    from butlers.modules.memory.storage import run_decay_sweep
    from butlers.modules.memory.tools.management import memory_stats

    pool = await _pool_for(core_memory_db_url)
    try:
        before = await memory_stats(pool)
        base_active = before["facts"]["active"]
        base_fading = before["facts"]["fading"]

        # Distinct (scope, subject, predicate) from other tests in this shared
        # module-scoped DB — the partial unique index on active property
        # facts would otherwise collide with an earlier test's row.
        await pool.execute(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, retention_class) "
            "VALUES ('owner', 'favors_drink', 'chamomile', 0.3, 0.5, "
            "now() - interval '1 day', 'operational')"
        )

        # NOTE: same shared-fixture caveat as above — assert the delta, not
        # an exact absolute count.
        stats = await run_decay_sweep(pool)
        assert stats["facts_fading"] >= 1

        after = await memory_stats(pool)
        assert after["facts"]["fading"] == base_fading + 1
        assert after["facts"]["active"] == base_active
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
@pytest.mark.pg_clock
async def test_store_fact_supersedes_a_fading_fact(core_memory_db_url: str) -> None:
    """store_fact's supersession lookup must still find a fading fact for the
    same (entity/subject, scope, predicate) — otherwise a fresh write leaves
    an orphaned fading row alongside a brand-new active one instead of
    superseding it (the sweep would be "fighting" the write path).
    """
    from unittest.mock import MagicMock

    from butlers.modules.memory.storage import run_decay_sweep, store_fact

    def _fake_embedding_engine() -> MagicMock:
        engine = MagicMock()
        engine.embed.return_value = [0.0] * 384
        engine.model_name = "test-model"
        return engine

    pool = await _pool_for(core_memory_db_url)
    try:
        engine = _fake_embedding_engine()

        # store_fact doesn't accept confidence/decay_rate overrides — insert
        # the "old" fact directly (same subject/predicate/tenant key that
        # store_fact's own no-entity supersession lookup uses) with a
        # confidence/decay_rate pair that the sweep will push into the
        # fading band: eff = 0.3 * exp(-0.5*1) ~= 0.182, in [0.05, 0.2).
        old_id = await pool.fetchval(
            "INSERT INTO facts (subject, predicate, content, confidence, decay_rate, "
            "last_confirmed_at, scope, tenant_id, retention_class) "
            "VALUES ('dave', 'favorite_drink', 'oolong', 0.3, 0.5, "
            "now() - interval '1 day', 'global', 'shared', 'operational') "
            "RETURNING id"
        )

        stats = await run_decay_sweep(pool)
        assert stats["facts_fading"] >= 1
        faded = await pool.fetchval("SELECT validity FROM facts WHERE id = $1", old_id)
        assert faded == "fading"

        new = await store_fact(
            pool,
            subject="dave",
            predicate="favorite_drink",
            content="green tea",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
        )
        assert new["supersedes_id"] == old_id

        old_validity = await pool.fetchval("SELECT validity FROM facts WHERE id = $1", old_id)
        assert old_validity == "superseded"
        new_validity = await pool.fetchval("SELECT validity FROM facts WHERE id = $1", new["id"])
        assert new_validity == "active"
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
async def test_run_decay_sweep_expiry_marks_catalog_entry_stale(core_memory_db_url: str) -> None:
    """Fact/rule expiry transitions cascade catalog disownment (bu-5ud8p.3).

    Without this cascade the just-enabled fleet catalog keeps serving an
    expired fact / forgotten rule to every butler indefinitely — fading
    transitions deliberately do NOT cascade (fading facts stay live per the
    memory-retention-policy spec), only the terminal expiry transition does.
    """
    from unittest.mock import MagicMock

    from butlers.modules.memory.storage import run_decay_sweep, store_fact, store_rule

    def _fake_embedding_engine() -> MagicMock:
        engine = MagicMock()
        engine.embed.return_value = [0.0] * 384
        engine.model_name = "test-model"
        return engine

    pool = await _pool_for(core_memory_db_url)
    try:
        engine = _fake_embedding_engine()

        fact = await store_fact(
            pool,
            subject="owner",
            predicate="used_to_expire",
            content="soon-to-expire-fact",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=True,
            source_schema="public",
        )
        fact_id = fact["id"]

        rule_id = await store_rule(
            pool,
            content="soon-to-expire heuristic",
            embedding_engine=engine,
            scope="global",
            tenant_id="shared",
            source_butler="health",
            enable_shared_catalog=True,
            source_schema="public",
        )

        # Push both into the expiry band directly — store_fact/store_rule
        # don't accept confidence/decay_rate overrides — using the same
        # eff ~= 0.0055 (< 0.05 expiry threshold) math as the other
        # decay-sweep integration tests in this file.
        await pool.execute(
            "UPDATE facts SET confidence = 0.3, decay_rate = 2.0,"
            " last_confirmed_at = now() - interval '2 days' WHERE id = $1",
            fact_id,
        )
        await pool.execute(
            "UPDATE rules SET confidence = 0.3, decay_rate = 2.0,"
            " last_confirmed_at = now() - interval '2 days' WHERE id = $1",
            rule_id,
        )

        await run_decay_sweep(pool)

        fact_validity = await pool.fetchval("SELECT validity FROM facts WHERE id = $1", fact_id)
        assert fact_validity == "expired"
        rule_forgotten = await pool.fetchval(
            "SELECT (metadata->>'forgotten')::boolean FROM rules WHERE id = $1", rule_id
        )
        assert rule_forgotten is True

        fact_catalog_row = await pool.fetchrow(
            "SELECT confidence, invalid_at FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'facts' AND source_id = $1",
            fact_id,
        )
        rule_catalog_row = await pool.fetchrow(
            "SELECT confidence, invalid_at FROM public.memory_catalog"
            " WHERE source_schema = 'public' AND source_table = 'rules' AND source_id = $1",
            rule_id,
        )
        assert fact_catalog_row is not None
        assert fact_catalog_row["confidence"] == 0
        assert fact_catalog_row["invalid_at"] is not None
        assert rule_catalog_row is not None
        assert rule_catalog_row["confidence"] == 0
        assert rule_catalog_row["invalid_at"] is not None
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# (f) Bounded backfill batch behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.skipif(not docker_available, reason="Docker not available")
@pytest.mark.integration
async def test_consolidation_backfill_batch_size_bounds_claim_and_skips_dead_letter(
    core_memory_db_url: str,
    monkeypatch,
) -> None:
    """The memory_consolidation_backfill schedule's larger batch_size only ever
    processes up to that many *pending* episodes per run, and never reclaims
    dead_letter'd ones — matching the "bounded batch per run, respect
    dead_letter states" requirement for the backlog-drain job.
    """

    class _SuccessfulSpawner:
        def __init__(self) -> None:
            self.trigger_sources: list[str] = []

        async def trigger(self, *, prompt: str, trigger_source: str):
            self.trigger_sources.append(trigger_source)
            return SimpleNamespace(success=True, output="{}", error=None)

    spawner = _SuccessfulSpawner()
    monkeypatch.setattr("butlers.core.spawn_hooks.get_spawner", lambda: spawner)

    pool = await _pool_for(core_memory_db_url)
    try:
        from butlers.core.memory_hooks import (
            clear_memory_consolidation,
            register_memory_consolidation,
        )
        from butlers.modules.memory.consolidation import run_consolidation

        async def _consolidate_memory(*, spawner, batch_size: int, enable_shared_catalog: bool):
            return await run_consolidation(
                pool=pool,
                embedding_engine=object(),
                cc_spawner=spawner,
                batch_size=batch_size,
                enable_shared_catalog=enable_shared_catalog,
            )

        register_memory_consolidation(_consolidate_memory)
        for i in range(12):
            await pool.execute(
                "INSERT INTO episodes (butler, content) VALUES ('switchboard', $1)",
                f"pending-episode-{i}",
            )
        # A dead-lettered episode must never be reclaimed by consolidation.
        await pool.execute(
            "INSERT INTO episodes (butler, content, consolidation_status, "
            "dead_letter_reason) VALUES ('switchboard', 'poison-episode', "
            "'dead_letter', 'exceeded max attempts')"
        )

        result = await _run_memory_consolidation_job(pool=pool, job_args={"batch_size": 5})
        assert result["episodes_processed"] == 5
        assert result["episodes_consolidated"] == 5
        assert spawner.trigger_sources == ["schedule:consolidation"]

        consolidated_count = await pool.fetchval(
            "SELECT count(*) FROM episodes WHERE consolidation_status = 'consolidated'"
        )
        assert consolidated_count == 5

        dead_letter_leased = await pool.fetchval(
            "SELECT leased_until FROM episodes WHERE consolidation_status = 'dead_letter'"
        )
        assert dead_letter_leased is None, "dead_letter episodes must never be (re)claimed"

        still_pending = await pool.fetchval(
            "SELECT count(*) FROM episodes WHERE consolidation_status = 'pending' "
            "AND leased_until IS NULL"
        )
        assert still_pending == 7  # 12 - 5 claimed this run
    finally:
        clear_memory_consolidation()
        await pool.close()
