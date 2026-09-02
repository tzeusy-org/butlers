"""Tests for butlers.core.scheduler — cron-driven task scheduler with TOML sync.

Covers:
- sync_schedules: insert, update (cron/prompt/mode), disable removed, re-enable restored
- tick: dispatch due prompt/job tasks, skip disabled, continue on failure, update timestamps
- schedule_create / update / delete: CRUD, validation, complexity, calendar fields
- schedule_list: field presence contract
- until_at: auto-disable when exceeded
- deadline task type: create with required fields
- cron staggering: determinism, cap, cadence preservation
- notify() validation: _check_notify_reference, sync_schedules warning behavior
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from butlers.core.model_routing import Complexity
from butlers.daemon import ButlerDaemon
from butlers.db import Database, register_jsonb_codec
from butlers.modules import memory as memory_module
from butlers.modules.memory import MemoryModule
from butlers.modules.registry import ModuleRegistry
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]
_asyncio_session = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    """Provision a DB with core migrations applied once per module."""
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(migrated_db_url: str):
    """Return an asyncpg pool with scheduler table cleared between tests."""
    p = await asyncpg.create_pool(
        migrated_db_url, min_size=1, max_size=3, init=register_jsonb_codec
    )
    await p.execute("TRUNCATE TABLE scheduled_tasks CASCADE")
    yield p
    await p.close()


class _Dispatch:
    def __init__(self, *, fail_on: set[str] | None = None, result=None):
        self.calls: list[dict] = []
        self._fail_on = fail_on or set()
        self._result = result

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        target = kwargs.get("prompt") or kwargs.get("job_name")
        if target in self._fail_on:
            raise RuntimeError(f"Simulated failure for: {target}")
        return self._result


def _past(minutes: int = 5) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes)


class _InterleavingPool:
    """Proxy a pool and invoke a callback immediately before one removal write."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        before_disable: Callable[[], Awaitable[None]],
    ) -> None:
        self._pool = pool
        self._before_disable = before_disable
        self._interleaved = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pool, name)

    async def execute(self, query: str, *args: object) -> str:
        if "UPDATE scheduled_tasks SET enabled = false" in query and not self._interleaved:
            self._interleaved = True
            await self._before_disable()
        return await self._pool.execute(query, *args)


class _LifecycleMemoryScheduleModule(MemoryModule):
    """Exercise the production memory schedule-registration branch in daemon startup."""

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        await self._register_default_maintenance_schedules(db)


class _NoopButlerDBLogHandler(logging.Handler):
    """Keep the lifecycle test focused on scheduler recovery, not log persistence."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()

    def emit(self, record: logging.LogRecord) -> None:
        return None


# ---------------------------------------------------------------------------
# sync_schedules
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_sync_inserts_and_sets_next_run_at(pool):
    """sync_schedules inserts toml tasks with source=toml and non-null next_run_at."""
    from butlers.core.scheduler import sync_schedules

    schedules = [
        {"name": "daily-report", "cron": "0 9 * * *", "prompt": "Generate daily report"},
        {"name": "weekly-digest", "cron": "0 8 * * 1", "prompt": "Send weekly digest"},
    ]
    await sync_schedules(pool, schedules)

    rows = await pool.fetch(
        "SELECT name, source, enabled, next_run_at FROM scheduled_tasks ORDER BY name"
    )
    assert len(rows) == 2
    assert all(r["source"] == "toml" for r in rows)
    assert all(r["enabled"] for r in rows)
    assert all(r["next_run_at"] is not None for r in rows)


@_asyncio_session
async def test_sync_updates_changed_fields_and_disables_removed(pool):
    """sync_schedules updates changed cron/prompt, disables removed tasks, re-enables restored."""
    from butlers.core.scheduler import sync_schedules

    base = [
        {"name": "keep-me", "cron": "0 9 * * *", "prompt": "keep"},
        {"name": "change-me", "cron": "0 9 * * *", "prompt": "old prompt"},
        {"name": "drop-me", "cron": "0 10 * * *", "prompt": "drop"},
    ]
    await sync_schedules(pool, base)

    # Update change-me, drop drop-me
    updated = [
        {"name": "keep-me", "cron": "0 9 * * *", "prompt": "keep"},
        {"name": "change-me", "cron": "0 10 * * *", "prompt": "new prompt"},
    ]
    await sync_schedules(pool, updated)

    all_rows = await pool.fetch("SELECT name, cron, prompt, enabled FROM scheduled_tasks")
    rows = {r["name"]: r for r in all_rows}
    assert rows["change-me"]["cron"] == "0 10 * * *"
    assert rows["change-me"]["prompt"] == "new prompt"
    assert rows["drop-me"]["enabled"] is False  # soft-disabled, not deleted

    # Re-sync with drop-me → re-enables
    await sync_schedules(pool, base)
    all_rows2 = await pool.fetch("SELECT name, enabled FROM scheduled_tasks")
    rows2 = {r["name"]: r for r in all_rows2}
    assert rows2["drop-me"]["enabled"] is True


@_asyncio_session
async def test_sync_removal_does_not_re_disable_a_concurrently_recovered_default(pool):
    """A stale TOML-removal snapshot cannot undo an atomic module-default recovery."""
    from butlers.core.scheduler import ensure_module_default_schedule, sync_schedules

    name = "memory_consolidation_stale_disable"
    await sync_schedules(
        pool,
        [
            {
                "name": name,
                "cron": "0 3 * * *",
                "dispatch_mode": "job",
                "job_name": "memory_consolidation",
            }
        ],
    )

    recovered = False

    async def _recover_after_stale_snapshot() -> None:
        nonlocal recovered
        assert recovered is False
        recovered = True
        await sync_schedules(pool, [])
        await ensure_module_default_schedule(
            pool,
            name=name,
            cron="0 3 * * *",
            job_name="memory_consolidation",
            owner_butler="legacy-general",
            owner_schema="public",
        )

    await sync_schedules(
        _InterleavingPool(pool, _recover_after_stale_snapshot),
        [],
    )

    assert recovered is True
    row = await pool.fetchrow("SELECT source, enabled FROM scheduled_tasks WHERE name = $1", name)
    assert row is not None
    assert row["source"] == "db"
    assert row["enabled"] is True

    # A fresh boot first synchronizes TOML again before its module registers
    # defaults. That sync must leave the recovered DB-owned row enabled.
    await sync_schedules(pool, [])
    row = await pool.fetchrow("SELECT source, enabled FROM scheduled_tasks WHERE name = $1", name)
    assert row is not None
    assert row["source"] == "db"
    assert row["enabled"] is True

    # The subsequent module registration is also a no-op with one audit event.
    await ensure_module_default_schedule(
        pool,
        name=name,
        cron="0 3 * * *",
        job_name="memory_consolidation",
        owner_butler="legacy-general",
        owner_schema="public",
    )
    assert (
        await pool.fetchval(
            """
            SELECT count(*) FROM public.audit_log
            WHERE action = 'scheduler.module_default_recovered' AND target = $1
            """,
            f"schedule:{name}",
        )
        == 1
    )


@_asyncio_session
async def test_legacy_lifecycle_recovery_derives_public_audit_identity(
    pool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A schema-less daemon boot derives the recovery audit identity from lifecycle config."""
    from butlers.core.scheduler import sync_schedules

    name = f"memory_consolidation_legacy_{uuid.uuid4().hex}"
    owner_butler = f"legacy_recovery_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(
        memory_module,
        "_DEFAULT_MAINTENANCE_SCHEDULES",
        (
            {
                "name": name,
                "cron": "0 */6 * * *",
                "job_name": "memory_consolidation",
            },
        ),
    )
    await sync_schedules(
        pool,
        [
            {
                "name": name,
                "cron": "0 */6 * * *",
                "dispatch_mode": "job",
                "job_name": "memory_consolidation",
            }
        ],
    )

    (tmp_path / "butler.toml").write_text(
        "\n".join(
            [
                "[butler]",
                f'name = "{owner_butler}"',
                "port = 9199",
                'description = "Legacy recovery test butler"',
                "",
                "[butler.db]",
                'name = "legacy_recovery_test"',
                "",
                "[modules.memory]",
            ]
        )
    )

    db = Database(db_name="legacy_recovery_test")
    db.pool = pool
    db.close = AsyncMock()
    daemon = ButlerDaemon(
        tmp_path,
        registry=ModuleRegistry(),
        db=db,
    )
    daemon._registry.register(_LifecycleMemoryScheduleModule)

    credential_store = SimpleNamespace(resolve=AsyncMock(return_value=None), shared_pool=pool)
    runtime_config = SimpleNamespace(seeded_at=None, updated_at=None)
    runtime_config_accessor = SimpleNamespace(
        seed_if_empty=AsyncMock(return_value=runtime_config),
        _cache=runtime_config,
    )
    runtime = SimpleNamespace(binary_name="claude")
    spawner = SimpleNamespace(stop_accepting=lambda: None, drain=AsyncMock())

    def _discard_background_task(coro: Any, *args: Any, **kwargs: Any) -> None:
        coro.close()

    lifecycle_patches = (
        patch("butlers.lifecycle.init_telemetry"),
        patch("butlers.lifecycle.init_metrics"),
        patch("butlers.lifecycle.validate_credentials"),
        patch(
            "butlers.lifecycle.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch("butlers.lifecycle.run_migrations", new_callable=AsyncMock),
        patch(
            "butlers.lifecycle.resolve_general_timezone", new_callable=AsyncMock, return_value="UTC"
        ),
        patch("butlers.lifecycle._ensure_owner_entity", new_callable=AsyncMock),
        patch("butlers.lifecycle.upsert_provider_feature_catalogue", new_callable=AsyncMock),
        patch(
            "butlers.cli_auth.persistence.restore_tokens", new_callable=AsyncMock, return_value={}
        ),
        patch(
            "butlers.core.runtime_config.RuntimeConfigAccessor",
            return_value=runtime_config_accessor,
        ),
        patch("butlers.lifecycle.get_adapter", return_value=lambda **kwargs: runtime),
        patch("butlers.lifecycle.shutil.which", return_value="/usr/bin/claude"),
        patch("butlers.lifecycle.Spawner", return_value=spawner),
        patch("butlers.lifecycle.FastMCP", return_value=MagicMock()),
        patch("butlers.lifecycle.asyncio.create_task", side_effect=_discard_background_task),
        patch("butlers.core.butler_logging.ButlerDBLogHandler", _NoopButlerDBLogHandler),
        patch.object(
            daemon, "_build_credential_store", new_callable=AsyncMock, return_value=credential_store
        ),
        patch.object(daemon, "_create_audit_pool", new_callable=AsyncMock, return_value=None),
        patch.object(daemon, "_connect_switchboard", new_callable=AsyncMock),
        patch.object(daemon, "_disconnect_switchboard", new_callable=AsyncMock),
        patch.object(daemon, "_recover_route_inbox", new_callable=AsyncMock),
        patch.object(daemon, "_register_core_tools"),
        patch.object(daemon, "_register_module_tools", new_callable=AsyncMock),
        patch.object(daemon, "_apply_approval_gates", new_callable=AsyncMock, return_value={}),
        patch.object(daemon, "_init_module_runtime_states", new_callable=AsyncMock),
        patch.object(daemon, "_start_mcp_server", new_callable=AsyncMock),
        patch.object(daemon, "_wire_pipelines"),
        patch.object(daemon, "_wire_calendar_approval_enqueuer"),
        patch.object(daemon, "_wire_module_runtime"),
    )
    with ExitStack() as stack:
        for lifecycle_patch in lifecycle_patches:
            stack.enter_context(lifecycle_patch)
        try:
            await daemon.start()

            row = await pool.fetchrow(
                "SELECT source, enabled FROM scheduled_tasks WHERE name = $1", name
            )
            audit_rows = await pool.fetch(
                """
                SELECT actor, metadata
                FROM public.audit_log
                WHERE action = 'scheduler.module_default_recovered' AND target = $1
                """,
                f"schedule:{name}",
            )
        finally:
            await daemon.shutdown()

    assert db.schema is None
    assert db.owner_butler == owner_butler
    assert row is not None
    assert row["source"] == "db"
    assert row["enabled"] is True
    assert len(audit_rows) == 1
    assert audit_rows[0]["actor"] == owner_butler
    assert audit_rows[0]["metadata"] == {
        "prior_source": "toml",
        "prior_enabled": False,
        "registered_default_name": name,
        "owner_butler": owner_butler,
        "owner_schema": "public",
    }


@_asyncio_session
async def test_module_default_recovery_reclaims_only_disabled_toml_orphans_and_audits(pool):
    """A post-sync TOML orphan is recovered once without rewriting its runtime payload."""
    from butlers.core.scheduler import ensure_module_default_schedule, sync_schedules

    name = "memory_consolidation_recovery"
    await sync_schedules(
        pool,
        [
            {
                "name": name,
                "cron": "17 4 * * *",
                "dispatch_mode": "job",
                "job_name": "memory_consolidation",
                "job_args": {"batch_size": 17},
                "complexity": "reasoning",
            }
        ],
    )
    await sync_schedules(pool, [])
    before = await pool.fetchrow(
        """
        SELECT cron, dispatch_mode, job_name, job_args, complexity, next_run_at, source, enabled
        FROM scheduled_tasks WHERE name = $1
        """,
        name,
    )
    assert before is not None
    assert before["source"] == "toml"
    assert before["enabled"] is False

    await ensure_module_default_schedule(
        pool,
        name=name,
        cron="0 3 * * *",
        job_name="memory_consolidation",
        job_args={"batch_size": 99},
        owner_butler="general",
        owner_schema="general",
    )

    recovered = await pool.fetchrow(
        """
        SELECT cron, dispatch_mode, job_name, job_args, complexity, next_run_at, source, enabled
        FROM scheduled_tasks WHERE name = $1
        """,
        name,
    )
    assert recovered is not None
    assert recovered["source"] == "db"
    assert recovered["enabled"] is True
    for field in ("cron", "dispatch_mode", "job_name", "job_args", "complexity", "next_run_at"):
        assert recovered[field] == before[field]

    audit_rows = await pool.fetch(
        """
        SELECT actor, action, target, metadata
        FROM public.audit_log
        WHERE action = 'scheduler.module_default_recovered' AND target = $1
        """,
        f"schedule:{name}",
    )
    assert len(audit_rows) == 1
    audit = audit_rows[0]
    assert audit["actor"] == "general"
    assert audit["metadata"] == {
        "prior_source": "toml",
        "prior_enabled": False,
        "registered_default_name": name,
        "owner_butler": "general",
        "owner_schema": "general",
    }

    # A committed recovery is idempotent: the DB-owned row is operator-owned
    # and no second transition can produce a second audit row.
    await ensure_module_default_schedule(
        pool,
        name=name,
        cron="0 3 * * *",
        job_name="memory_consolidation",
        owner_butler="general",
        owner_schema="general",
    )
    assert (
        await pool.fetchval(
            """
            SELECT count(*) FROM public.audit_log
            WHERE action = 'scheduler.module_default_recovered' AND target = $1
            """,
            f"schedule:{name}",
        )
        == 1
    )


@_asyncio_session
async def test_module_default_recovery_rolls_back_when_audit_append_fails(pool, monkeypatch):
    """A recovery has no observable state transition when its canonical audit cannot commit."""
    from butlers.core.scheduler import ensure_module_default_schedule, sync_schedules

    name = "memory_decay_sweep_audit_rollback"
    await sync_schedules(
        pool,
        [
            {
                "name": name,
                "cron": "15 3 * * *",
                "dispatch_mode": "job",
                "job_name": "memory_decay_sweep",
            }
        ],
    )
    await sync_schedules(pool, [])

    async def _audit_failure(*args, **kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("butlers.api.routers.audit.append", _audit_failure)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await ensure_module_default_schedule(
            pool,
            name=name,
            cron="0 3 * * *",
            job_name="memory_decay_sweep",
            owner_butler="general",
            owner_schema="general",
        )

    row = await pool.fetchrow("SELECT source, enabled FROM scheduled_tasks WHERE name = $1", name)
    assert row is not None
    assert row["source"] == "toml"
    assert row["enabled"] is False
    assert (
        await pool.fetchval(
            """
            SELECT count(*) FROM public.audit_log
            WHERE action = 'scheduler.module_default_recovered' AND target = $1
            """,
            f"schedule:{name}",
        )
        == 0
    )


@_asyncio_session
async def test_module_default_recovery_concurrently_commits_one_transition_and_audit(pool):
    """The returned conditional transition is the sole authority for the recovery audit."""
    import asyncio

    from butlers.core.scheduler import ensure_module_default_schedule, sync_schedules

    name = "memory_purge_superseded_concurrent_recovery"
    await sync_schedules(
        pool,
        [
            {
                "name": name,
                "cron": "30 3 * * *",
                "dispatch_mode": "job",
                "job_name": "memory_purge_superseded",
            }
        ],
    )
    await sync_schedules(pool, [])

    await asyncio.gather(
        *[
            ensure_module_default_schedule(
                pool,
                name=name,
                cron="0 3 * * *",
                job_name="memory_purge_superseded",
                owner_butler="relationship",
                owner_schema="relationship",
            )
            for _ in range(2)
        ]
    )

    row = await pool.fetchrow("SELECT source, enabled FROM scheduled_tasks WHERE name = $1", name)
    assert row is not None
    assert row["source"] == "db"
    assert row["enabled"] is True
    assert (
        await pool.fetchval(
            """
            SELECT count(*) FROM public.audit_log
            WHERE action = 'scheduler.module_default_recovered' AND target = $1
            """,
            f"schedule:{name}",
        )
        == 1
    )


@_asyncio_session
async def test_module_default_recovery_attributes_same_name_across_butler_schemas(
    migrated_db_url: str,
):
    """Same schedule names remain distinguishable by configured butler and schema metadata."""
    from butlers.core.scheduler import ensure_module_default_schedule, sync_schedules

    name = "memory_consolidation_cross_schema_recovery"
    schemas = ("scheduler_recovery_general", "scheduler_recovery_relationship")
    admin = await asyncpg.connect(migrated_db_url)
    pools: list[asyncpg.Pool] = []
    try:
        for schema in schemas:
            await admin.execute(f"CREATE SCHEMA {schema}")
            await admin.execute(
                f"CREATE TABLE {schema}.scheduled_tasks (LIKE public.scheduled_tasks INCLUDING ALL)"
            )
            pools.append(
                await asyncpg.create_pool(
                    migrated_db_url,
                    min_size=1,
                    max_size=1,
                    init=register_jsonb_codec,
                    server_settings={"search_path": f"{schema},public"},
                )
            )

        for pool, butler, schema in zip(
            pools,
            ("general", "relationship"),
            schemas,
            strict=True,
        ):
            await sync_schedules(
                pool,
                [
                    {
                        "name": name,
                        "cron": "45 3 * * *",
                        "dispatch_mode": "job",
                        "job_name": "memory_consolidation",
                    }
                ],
            )
            await sync_schedules(pool, [])
            await ensure_module_default_schedule(
                pool,
                name=name,
                cron="0 3 * * *",
                job_name="memory_consolidation",
                owner_butler=butler,
                owner_schema=schema,
            )

        rows = await admin.fetch(
            """
            SELECT metadata
            FROM public.audit_log
            WHERE action = 'scheduler.module_default_recovered' AND target = $1
            """,
            f"schedule:{name}",
        )
        assert {
            (
                (
                    json.loads(row["metadata"])
                    if isinstance(row["metadata"], str)
                    else row["metadata"]
                )["owner_butler"],
                (
                    json.loads(row["metadata"])
                    if isinstance(row["metadata"], str)
                    else row["metadata"]
                )["owner_schema"],
            )
            for row in rows
        } == {
            ("general", "scheduler_recovery_general"),
            ("relationship", "scheduler_recovery_relationship"),
        }
    finally:
        for pool in pools:
            await pool.close()
        for schema in schemas:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        await admin.close()


@_asyncio_session
async def test_module_default_recovery_reclaims_episode_cleanup_but_fences_db_ownership(pool):
    """A disabled TOML orphan is recovered — including ``memory_episode_cleanup``,
    now that ``run_episode_cleanup`` is bounded + consolidation-aware and can no
    longer trigger a destructive catch-up. DB ownership remains a hard fence."""
    from butlers.core.scheduler import ensure_module_default_schedule, sync_schedules

    active_name = "memory_decay_sweep_active_toml"
    db_name = "memory_purge_superseded_operator_disabled"
    cleanup_name = "memory_episode_cleanup"

    await sync_schedules(
        pool,
        [
            {
                "name": active_name,
                "cron": "5 1 * * *",
                "dispatch_mode": "job",
                "job_name": "memory_decay_sweep",
            },
            {
                "name": cleanup_name,
                "cron": "5 2 * * *",
                "dispatch_mode": "job",
                "job_name": cleanup_name,
            },
        ],
    )
    await ensure_module_default_schedule(
        pool,
        name=db_name,
        cron="5 3 * * *",
        job_name="memory_purge_superseded",
        owner_butler="general",
        owner_schema="general",
    )
    await pool.execute(
        "UPDATE scheduled_tasks SET enabled = false WHERE name = $1",
        db_name,
    )
    # Drop the TOML block for cleanup_name → it becomes a disabled TOML orphan.
    await sync_schedules(
        pool,
        [
            {
                "name": active_name,
                "cron": "5 1 * * *",
                "dispatch_mode": "job",
                "job_name": "memory_decay_sweep",
            }
        ],
    )

    await ensure_module_default_schedule(
        pool,
        name=active_name,
        cron="0 0 * * *",
        job_name="memory_decay_sweep",
        owner_butler="general",
        owner_schema="general",
    )
    await ensure_module_default_schedule(
        pool,
        name=db_name,
        cron="0 0 * * *",
        job_name="memory_purge_superseded",
        owner_butler="general",
        owner_schema="general",
    )
    await ensure_module_default_schedule(
        pool,
        name=cleanup_name,
        cron="0 0 * * *",
        job_name=cleanup_name,
        owner_butler="general",
        owner_schema="general",
    )

    rows = {
        row["name"]: row
        for row in await pool.fetch(
            "SELECT name, source, enabled FROM scheduled_tasks WHERE name = ANY($1::text[])",
            [active_name, db_name, cleanup_name],
        )
    }
    # active TOML schedule was never disabled → recovery is a no-op, stays TOML.
    assert rows[active_name]["source"] == "toml"
    assert rows[active_name]["enabled"] is True
    # DB-owned (operator-disabled) schedule is fenced: never auto-recovered.
    assert rows[db_name]["source"] == "db"
    assert rows[db_name]["enabled"] is False
    # episode_cleanup, a disabled TOML orphan, is now reclaimed like any default.
    assert rows[cleanup_name]["source"] == "db"
    assert rows[cleanup_name]["enabled"] is True

    # Exactly one recovery audit row — for episode_cleanup. The active TOML row
    # and the fenced DB-owned row must not emit a recovery event.
    audited = await pool.fetch(
        """
        SELECT target FROM public.audit_log
        WHERE action = 'scheduler.module_default_recovered'
          AND target = ANY($1::text[])
        """,
        [f"schedule:{active_name}", f"schedule:{db_name}", f"schedule:{cleanup_name}"],
    )
    assert [r["target"] for r in audited] == [f"schedule:{cleanup_name}"]


@_asyncio_session
async def test_sync_default_timezone_pins_cron_to_local(pool):
    """sync_schedules computes next_run_at by evaluating cron in default_timezone."""
    from butlers.core.scheduler import sync_schedules

    schedules = [{"name": "day-close", "cron": "5 1 * * *", "prompt": "summary; call notify()"}]
    await sync_schedules(pool, schedules, default_timezone="Asia/Singapore")

    next_run_at = await pool.fetchval(
        "SELECT next_run_at FROM scheduled_tasks WHERE name = 'day-close'"
    )
    # 01:05 Asia/Singapore lands at 17:05 UTC. The minute is exact; the hour is
    # 17 regardless of which calendar day croniter picks relative to "now".
    assert next_run_at.astimezone(UTC).hour == 17
    assert next_run_at.astimezone(UTC).minute == 5


@_asyncio_session
async def test_tick_advance_honors_row_timezone(pool):
    """tick() advances next_run_at using the per-row timezone column."""
    from butlers.core.scheduler import sync_schedules, tick

    await sync_schedules(
        pool,
        [{"name": "tz-task", "cron": "5 1 * * *", "prompt": "do it; call notify()"}],
    )
    # Make it due now and pin an explicit timezone on the row.
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $1, timezone = 'Asia/Singapore' "
        "WHERE name = 'tz-task'",
        _past(),
    )

    dispatch = _Dispatch()
    await tick(pool, dispatch)

    next_run_at = await pool.fetchval(
        "SELECT next_run_at FROM scheduled_tasks WHERE name = 'tz-task'"
    )
    # Advanced to the next 01:05 SGT == 17:05 UTC.
    assert next_run_at.astimezone(UTC).hour == 17
    assert next_run_at.astimezone(UTC).minute == 5


@_asyncio_session
async def test_tick_advance_follows_owner_default_for_utc_rows(pool):
    """A default ('UTC') timezone column follows tick's default_timezone (the owner tz).

    This is the chronicler day_close scenario: a TOML schedule (timezone='UTC')
    must advance in the owner's local time, not UTC.
    """
    from butlers.core.scheduler import sync_schedules, tick

    await sync_schedules(
        pool,
        [{"name": "owner-default", "cron": "5 1 * * *", "prompt": "summary; call notify()"}],
    )
    # TOML insert leaves the DEFAULT 'UTC' on the column; make it due now.
    stored_tz = await pool.fetchval(
        "SELECT timezone FROM scheduled_tasks WHERE name = 'owner-default'"
    )
    assert stored_tz == "UTC"  # confirms the sentinel we resolve against the default
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $1 WHERE name = 'owner-default'",
        _past(),
    )

    await tick(pool, _Dispatch(), default_timezone="Asia/Singapore")

    next_run_at = await pool.fetchval(
        "SELECT next_run_at FROM scheduled_tasks WHERE name = 'owner-default'"
    )
    # 01:05 Asia/Singapore == 17:05 UTC despite the column being 'UTC'.
    assert next_run_at.astimezone(UTC).hour == 17
    assert next_run_at.astimezone(UTC).minute == 5


# ---------------------------------------------------------------------------
# tick — prompt and job dispatch
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_tick_dispatch_prompt_and_job(pool):
    """tick() dispatches prompt tasks with trigger_source; job tasks via job_name/job_args without prompt/complexity."""
    from butlers.core.scheduler import schedule_create, tick

    # Prompt task
    t1 = await schedule_create(pool, "due-task", "*/1 * * * *", "run this")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t1, _past())
    dispatch = _Dispatch()
    count = await tick(pool, dispatch)
    assert count == 1
    assert dispatch.calls[0]["prompt"] == "run this"
    assert dispatch.calls[0]["trigger_source"] == "schedule:due-task"

    # Job task
    t2 = await schedule_create(
        pool,
        "due-job",
        "*/1 * * * *",
        dispatch_mode="job",
        job_name="eligibility_sweep",
        job_args={"batch_size": 25},
    )
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t2, _past())
    dispatch2 = _Dispatch()
    await tick(pool, dispatch2)
    call = dispatch2.calls[0]
    assert call["job_name"] == "eligibility_sweep" and call["job_args"] == {"batch_size": 25}
    assert "prompt" not in call and "complexity" not in call


@_asyncio_session
async def test_tick_prepares_prompt_with_shared_run_time_and_timezone(pool):
    """Prompt and completion hooks share one authoritative tick context."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "bound-task", "*/1 * * * *", "original prompt")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        _past(),
    )
    prompt_context: dict[str, Any] = {}
    completion_context: dict[str, Any] = {}

    def prepare_prompt(*, task_name, prompt, run_at, timezone):
        prompt_context.update(
            task_name=task_name,
            prompt=prompt,
            run_at=run_at,
            timezone=timezone,
        )
        return f"{prompt}\n\nBound target."

    async def complete(*, task_name, result, run_at, timezone):
        completion_context.update(
            task_name=task_name,
            result=result,
            run_at=run_at,
            timezone=timezone,
        )

    dispatch = _Dispatch(result={"status": "ok"})
    count = await tick(
        pool,
        dispatch,
        prompt_hooks={"bound-task": prepare_prompt},
        completion_hooks={"bound-task": complete},
        default_timezone="Asia/Singapore",
    )

    assert count == 1
    assert dispatch.calls[0]["prompt"] == "original prompt\n\nBound target."
    assert prompt_context["task_name"] == "bound-task"
    assert prompt_context["prompt"] == "original prompt"
    assert prompt_context["timezone"] == "Asia/Singapore"
    assert completion_context["task_name"] == "bound-task"
    assert completion_context["result"] == {"status": "ok"}
    assert completion_context["run_at"] == prompt_context["run_at"]
    assert completion_context["timezone"] == prompt_context["timezone"]


@_asyncio_session
async def test_tick_keeps_legacy_completion_hook_signature_compatible(pool):
    """Existing three-keyword completion hooks keep running after timezone wiring."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "legacy-hook-task", "*/1 * * * *", "prompt")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        _past(),
    )
    completed: dict[str, Any] = {}

    async def complete(*, task_name, result, run_at):
        completed.update(task_name=task_name, result=result, run_at=run_at)

    result = {"status": "ok"}
    count = await tick(
        pool,
        _Dispatch(result=result),
        completion_hooks={"legacy-hook-task": complete},
    )

    assert count == 1
    assert completed["task_name"] == "legacy-hook-task"
    assert completed["result"] == result
    assert completed["run_at"].tzinfo is not None


@_asyncio_session
async def test_tick_prompt_hook_failure_prevents_unbound_dispatch(pool):
    """A failed deterministic binding must not send the stored prompt to the LLM."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "unbound-task", "*/1 * * * *", "unsafe prompt")
    original_next_run_at = _past()
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1",
        task_id,
        original_next_run_at,
    )

    def fail_preparation(**_kwargs):
        raise RuntimeError("target binding failed")

    dispatch = _Dispatch()
    count = await tick(
        pool,
        dispatch,
        prompt_hooks={"unbound-task": fail_preparation},
    )

    row = await pool.fetchrow(
        "SELECT next_run_at, last_run_at, last_result FROM scheduled_tasks WHERE id = $1",
        task_id,
    )
    assert count == 0
    assert dispatch.calls == []
    assert row["next_run_at"] > original_next_run_at
    assert row["last_run_at"] is not None
    assert "target binding failed" in row["last_result"]["error"]


@pytest.mark.parametrize(
    "stored,expected",
    [
        (None, Complexity.WORKHORSE),  # missing → default
        ("trivial", Complexity.CHEAP),
        ("medium", Complexity.WORKHORSE),
        ("high", Complexity.REASONING),
        ("extra_high", Complexity.REASONING),
        ("discretion", Complexity.SPECIALTY),
        ("self_healing", Complexity.SPECIALTY),
        ("reasoning", Complexity.REASONING),  # canonical passes through
        ("bogus-tier", Complexity.WORKHORSE),  # junk fails open
    ],
)
@_asyncio_session
async def test_tick_dispatches_legacy_complexity_at_canonical_tier(pool, stored, expected):
    """The REAL cron dispatch loop hands the canonical Complexity to dispatch_fn (which
    feeds resolve_model). bu-lq7m4: a schedule row stored with the retired 'high'/'extra_high'
    tier must dispatch at reasoning — previously _parse_complexity_from_db_row collapsed every
    legacy tier to workhorse, so those rows silently under-resolved their model."""
    from butlers.core.scheduler import schedule_create, tick

    task_id = await schedule_create(pool, "tiered-task", "*/1 * * * *", "run tiered")
    # schedule_create validates complexity on write; persist the stale/legacy value directly.
    await pool.execute(
        "UPDATE scheduled_tasks SET complexity = $2, next_run_at = $3 WHERE id = $1",
        task_id,
        stored,
        _past(),
    )

    dispatch = _Dispatch()
    await tick(pool, dispatch)

    assert len(dispatch.calls) == 1
    assert dispatch.calls[0]["complexity"] == expected


@_asyncio_session
async def test_tick_skips_disabled_continues_on_failure_and_timestamps(pool):
    """tick() skips disabled tasks; continues when dispatch raises; sets last_run_at; advances next_run_at; disables when until_at exceeded."""
    from butlers.core.scheduler import schedule_create, tick

    # Disabled task — skipped
    t1 = await schedule_create(pool, "disabled-task2", "*/1 * * * *", "skip me")
    await pool.execute(
        "UPDATE scheduled_tasks SET next_run_at = $2, enabled = false WHERE id = $1", t1, _past()
    )
    # Task that fails — attempted but not counted
    t2 = await schedule_create(pool, "fail-task2", "*/1 * * * *", "I will fail")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t2, _past())
    dispatch = _Dispatch(fail_on={"I will fail"})
    count = await tick(pool, dispatch)
    assert count == 0
    assert len(dispatch.calls) == 1  # only the fail-task was attempted

    # Timestamps advance after success
    t3 = await schedule_create(pool, "advance-task2", "*/5 * * * *", "advance")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t3, _past(10))
    await tick(pool, _Dispatch())
    row = await pool.fetchrow(
        "SELECT next_run_at, last_run_at FROM scheduled_tasks WHERE id = $1", t3
    )
    assert row["next_run_at"] > datetime.now(UTC) - timedelta(seconds=5)
    assert row["last_run_at"] is not None

    # until_at exceeded → task disabled
    past_until = datetime.now(UTC) - timedelta(hours=1)
    t4 = await schedule_create(pool, "until-task2", "*/1 * * * *", "expiring", until_at=past_until)
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t4, _past())
    await tick(pool, _Dispatch())
    row2 = await pool.fetchrow("SELECT enabled FROM scheduled_tasks WHERE id = $1", t4)
    assert row2["enabled"] is False


@_asyncio_session
async def test_tick_tolerates_legacy_schema_without_until_at(pool):
    """tick() does not crash on long-lived scheduled_tasks tables missing until_at."""
    from butlers.core.scheduler import tick

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "ALTER TABLE scheduled_tasks DROP CONSTRAINT IF EXISTS "
                "scheduled_tasks_until_bounds_check"
            )
            await conn.execute("ALTER TABLE scheduled_tasks DROP COLUMN IF EXISTS until_at")

            count = await tick(conn, _Dispatch())

            assert count == 0
        finally:
            await tx.rollback()


# ---------------------------------------------------------------------------
# tick — eligibility gating (paused/quarantined butlers must not dispatch)
# ---------------------------------------------------------------------------


class _FakeEligibilityPool:
    """Minimal asyncpg-pool stand-in for the Switchboard butler_registry.

    ``resolve_routing_target`` issues a single ``fetchrow`` against
    ``butler_registry``; when the stored eligibility_state already matches the
    derived state (which it does for an explicit 'quarantined' row and for an
    'active' row with a fresh last_seen_at), no reconcile UPDATE/INSERT runs, so
    only ``fetchrow`` needs a real implementation here.
    """

    def __init__(self, *, name: str, eligibility_state: str):
        self._name = name
        self._eligibility_state = eligibility_state

    async def fetchrow(self, _query, name):  # noqa: ANN001
        if name != self._name:
            return None
        quarantined = self._eligibility_state == "quarantined"
        return {
            "name": name,
            "endpoint_url": f"http://localhost:4200/{name}/sse",
            "description": None,
            "modules": json.dumps([]),
            # Fresh heartbeat so an 'active' row derives to 'active', not 'stale'.
            "last_seen_at": datetime.now(UTC),
            "registered_at": datetime.now(UTC),
            "eligibility_state": self._eligibility_state,
            "liveness_ttl_seconds": 300,
            "quarantined_at": datetime.now(UTC) if quarantined else None,
            "quarantine_reason": "paused via dashboard" if quarantined else None,
            "route_contract_min": 1,
            "route_contract_max": 1,
            "capabilities": json.dumps(["trigger"]),
            "eligibility_updated_at": datetime.now(UTC),
            "agent_type": "butler",
        }


@_asyncio_session
async def test_tick_skips_dispatch_when_butler_quarantined(pool):
    """A paused/quarantined butler must NOT dispatch its due cron tick."""
    from butlers.core.scheduler import schedule_create, tick

    t1 = await schedule_create(pool, "gated-task", "*/1 * * * *", "should not run")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t1, _past())

    dispatch = _Dispatch()
    eligibility_pool = _FakeEligibilityPool(name="butlerA", eligibility_state="quarantined")
    count = await tick(
        pool,
        dispatch,
        butler_name="butlerA",
        eligibility_pool=eligibility_pool,
    )

    assert count == 0
    assert dispatch.calls == []  # no dispatch attempted

    # Skip (not defer): next_run_at must be preserved at the due time so the
    # task does not advance while paused, and resumes naturally once eligible.
    row = await pool.fetchrow(
        "SELECT next_run_at, last_run_at FROM scheduled_tasks WHERE id = $1", t1
    )
    assert row["next_run_at"] <= datetime.now(UTC)  # still due
    assert row["last_run_at"] is None  # never ran


@_asyncio_session
async def test_tick_dispatches_when_butler_active(pool):
    """An eligible (active) butler dispatches its due cron tick normally."""
    from butlers.core.scheduler import schedule_create, tick

    t1 = await schedule_create(pool, "active-task", "*/1 * * * *", "run me")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t1, _past())

    dispatch = _Dispatch()
    eligibility_pool = _FakeEligibilityPool(name="butlerA", eligibility_state="active")
    count = await tick(
        pool,
        dispatch,
        butler_name="butlerA",
        eligibility_pool=eligibility_pool,
    )

    assert count == 1
    assert dispatch.calls[0]["prompt"] == "run me"
    assert dispatch.calls[0]["trigger_source"] == "schedule:active-task"


@_asyncio_session
async def test_tick_dispatches_when_no_eligibility_pool(pool):
    """Without an eligibility_pool, ticks dispatch as before (backward compatible)."""
    from butlers.core.scheduler import schedule_create, tick

    t1 = await schedule_create(pool, "ungated-task", "*/1 * * * *", "run me")
    await pool.execute("UPDATE scheduled_tasks SET next_run_at = $2 WHERE id = $1", t1, _past())

    dispatch = _Dispatch()
    count = await tick(pool, dispatch, butler_name="butlerA")

    assert count == 1
    assert dispatch.calls[0]["prompt"] == "run me"


# ---------------------------------------------------------------------------
# schedule_create / update / delete
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_schedule_create_and_list(pool):
    """schedule_create persists prompt and job tasks; schedule_list returns fields; invalid cron and dup name raise."""
    from butlers.core.scheduler import schedule_create, schedule_list

    # Prompt task with optional fields
    task_id = await schedule_create(
        pool,
        "list-task",
        "0 9 * * *",
        "list me",
        complexity="reasoning",
        timezone="America/New_York",
        display_title="List Task",
    )
    assert task_id is not None
    tasks = await schedule_list(pool)
    task = next((t for t in tasks if t["name"] == "list-task"), None)
    assert task is not None and task["complexity"] == "reasoning"
    assert task["timezone"] == "America/New_York" and task["dispatch_mode"] == "prompt"
    assert task["last_result"] is None

    # Job task
    await schedule_create(
        pool,
        "job-list-task",
        "*/10 * * * *",
        dispatch_mode="job",
        job_name="my_job",
        job_args={"dry_run": True},
    )
    tasks2 = await schedule_list(pool)
    jt = next(t for t in tasks2 if t["name"] == "job-list-task")
    assert jt["dispatch_mode"] == "job" and jt["job_name"] == "my_job" and jt["prompt"] is None

    # Invalid cron raises
    with pytest.raises((ValueError, Exception)):
        await schedule_create(pool, "bad-cron", "not-a-cron", "test")

    # Duplicate name raises
    await schedule_create(pool, "dup-name", "0 9 * * *", "first")
    with pytest.raises(Exception):
        await schedule_create(pool, "dup-name", "0 10 * * *", "second")


@pytest.mark.parametrize(
    "stored,expected",
    [
        (None, "workhorse"),  # missing/null column defaults to workhorse
        # Every retired pre-core_092 tier normalizes to its canonical successor
        # (bu-lq7m4: previously ALL legacy tiers collapsed to workhorse because
        # _parse_complexity_from_db_row did a bare Complexity(raw); high/extra_high
        # must remap to reasoning). Contract shared with model_routing._DEPRECATED_TIER_MAP.
        ("trivial", "cheap"),
        ("medium", "workhorse"),
        ("high", "reasoning"),
        ("extra_high", "reasoning"),
        ("discretion", "specialty"),
        ("self_healing", "specialty"),
        ("reasoning", "reasoning"),  # valid tiers pass through unchanged
        ("legacy", "legacy"),
        ("bogus-tier", "workhorse"),  # unknown junk fails open to workhorse
    ],
)
@_asyncio_session
async def test_schedule_list_coerces_null_and_legacy_complexity(pool, stored, expected):
    """schedule_list normalizes null/legacy stored complexity to a canonical tier for MCP
    callers; valid tiers pass through unchanged. Guards bu-e0d9x (schedule_list previously
    returned dict(row) uncoerced) and bu-lq7m4 (retired high/extra_high must remap to
    reasoning, not collapse to workhorse)."""
    from butlers.core.scheduler import schedule_create, schedule_list

    task_id = await schedule_create(pool, "coerce-task", "0 9 * * *", "coerce test")

    # schedule_create validates complexity on write, so simulate a stale/legacy
    # stored value (NULL or a retired tier) via a direct row update.
    await pool.execute("UPDATE scheduled_tasks SET complexity = $2 WHERE id = $1", task_id, stored)

    tasks = await schedule_list(pool)
    by_name = {t["name"]: t for t in tasks}
    assert by_name["coerce-task"]["complexity"] == expected


@_asyncio_session
async def test_schedule_update_and_delete(pool):
    """schedule_update changes fields; schedule_delete removes runtime tasks."""
    from butlers.core.scheduler import schedule_create, schedule_delete, schedule_update

    task_id = await schedule_create(pool, "updatable", "0 9 * * *", "original")

    await schedule_update(pool, task_id, cron="0 10 * * *", prompt="updated")
    row = await pool.fetchrow("SELECT cron, prompt FROM scheduled_tasks WHERE id = $1", task_id)
    assert row["cron"] == "0 10 * * *"
    assert row["prompt"] == "updated"

    await schedule_delete(pool, task_id)
    assert await pool.fetchrow("SELECT id FROM scheduled_tasks WHERE id = $1", task_id) is None


@_asyncio_session
async def test_schedule_validation(pool):
    """schedule_update raises for invalid cron and nonexistent ID; complexity enforced on create and update."""
    from butlers.core.scheduler import schedule_create, schedule_update

    # Invalid cron on update
    task_id = await schedule_create(pool, "cron-update-bad", "0 9 * * *", "prompt")
    with pytest.raises((ValueError, Exception)):
        await schedule_update(pool, task_id, cron="bad-cron")

    # Nonexistent ID raises
    with pytest.raises((ValueError, Exception)):
        await schedule_update(pool, uuid.uuid4(), prompt="does not exist")

    # Complexity: invalid on create
    with pytest.raises(ValueError, match="complexity"):
        await schedule_create(pool, "bad-complexity", "0 9 * * *", "work", complexity="ultra")

    # Complexity: valid accepted
    t2 = await schedule_create(pool, "good-complexity", "0 9 * * *", "work", complexity="reasoning")
    row = await pool.fetchrow("SELECT complexity FROM scheduled_tasks WHERE id = $1", t2)
    assert row["complexity"] == "reasoning"

    # Complexity: invalid on update
    with pytest.raises(ValueError, match="complexity"):
        await schedule_update(pool, t2, complexity="super_high")


# ---------------------------------------------------------------------------
# Deadline task type
# ---------------------------------------------------------------------------


@_asyncio_session
async def test_deadline_task_create(pool):
    """schedule_create with task_type=deadline requires target_date and alert_thresholds."""
    import datetime as _dt

    from butlers.core.scheduler import schedule_create

    future_date = (_dt.datetime.now(_dt.UTC) + _dt.timedelta(days=60)).date()
    task_id = await schedule_create(
        pool,
        "deadline-task",
        "0 9 * * *",
        "deadline prompt",
        task_type="deadline",
        target_date=future_date,
        lead_time_days=45,
        alert_thresholds=[
            {"days_before": 30, "severity": "info"},
            {"days_before": 14, "severity": "warning"},
            {"days_before": 7, "severity": "warning"},
            {"days_before": 1, "severity": "critical"},
        ],
    )
    assert task_id is not None

    # Missing required fields should raise
    with pytest.raises((ValueError, Exception)):
        await schedule_create(
            pool,
            "bad-deadline",
            "0 9 * * *",
            "missing",
            task_type="deadline",
        )


# ---------------------------------------------------------------------------
# Cron staggering (unit tests — no DB required)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stagger_determinism_cap_and_cadence() -> None:
    """Same key/cron always same offset; every-minute capped; cadences preserved."""
    from datetime import UTC, datetime, timedelta

    from butlers.core.scheduler import _next_run, _stagger_offset_seconds

    now = datetime(2026, 2, 20, 12, 0, tzinfo=UTC)

    # Determinism
    first = _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)
    assert first == _stagger_offset_seconds("0 * * * *", stagger_key="health", now=now)

    # Per-minute cron capped within 60s
    offset = _stagger_offset_seconds("* * * * *", stagger_key="switchboard", now=now)
    assert 0 <= offset <= 59

    # No stagger key same as None
    base = _next_run("0 * * * *", now=now)
    assert _next_run("0 * * * *", stagger_key=None, now=now) == base

    # 5-minute cadence preserved
    first_5 = _next_run("*/5 * * * *", stagger_key="general", now=now)
    second_5 = _next_run("*/5 * * * *", stagger_key="general", now=first_5)
    assert second_5 - first_5 == timedelta(minutes=5)

    # 1-minute cadence preserved
    first_1 = _next_run("* * * * *", stagger_key="messenger", now=now)
    second_1 = _next_run("* * * * *", stagger_key="messenger", now=first_1)
    assert second_1 - first_1 == timedelta(minutes=1)


@pytest.mark.unit
def test_next_run_interprets_cron_in_timezone() -> None:
    """Hour-pinned crons are evaluated in the given IANA zone, returned in UTC."""
    from datetime import UTC, datetime

    from butlers.core.scheduler import _next_run

    # 2026-06-14 02:00Z == 10:00 Asia/Singapore (UTC+8). The next "01:05" local
    # is 2026-06-15 01:05 SGT == 2026-06-14 17:05 UTC.
    now = datetime(2026, 6, 14, 2, 0, tzinfo=UTC)
    sgt = _next_run("5 1 * * *", timezone="Asia/Singapore", now=now)
    assert sgt == datetime(2026, 6, 14, 17, 5, tzinfo=UTC)

    # Back-compat: no timezone (or UTC) evaluates in UTC.
    utc = _next_run("5 1 * * *", now=now)
    assert utc == datetime(2026, 6, 15, 1, 5, tzinfo=UTC)
    assert _next_run("5 1 * * *", timezone="UTC", now=now) == utc


@pytest.mark.unit
def test_coerce_schedule_zone_fails_open_to_utc() -> None:
    """Unknown/empty timezone strings degrade to UTC rather than raising."""
    from zoneinfo import ZoneInfo

    from butlers.core.scheduler import _coerce_schedule_zone

    assert _coerce_schedule_zone("Not/AZone") == ZoneInfo("UTC")
    assert _coerce_schedule_zone(None) == ZoneInfo("UTC")
    assert _coerce_schedule_zone("") == ZoneInfo("UTC")
    assert _coerce_schedule_zone("Asia/Singapore") == ZoneInfo("Asia/Singapore")


@pytest.mark.unit
def test_effective_schedule_timezone_sentinels_follow_default() -> None:
    """'UTC'/NULL/empty follow the owner default; non-UTC values override it."""
    from butlers.core.scheduler import _effective_schedule_timezone

    default = "Asia/Singapore"
    assert _effective_schedule_timezone("UTC", default) == default
    assert _effective_schedule_timezone("utc", default) == default
    assert _effective_schedule_timezone(None, default) == default
    assert _effective_schedule_timezone("", default) == default
    assert _effective_schedule_timezone("America/New_York", default) == "America/New_York"


# ---------------------------------------------------------------------------
# notify() validation in _check_notify_reference and sync_schedules
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_check_notify_reference(tmp_path, caplog) -> None:
    """Warns when notify absent; silent when present or in skill; safe on missing dir."""
    import logging

    from butlers.core.scheduler import _check_notify_reference

    # Present: no warning
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="report", prompt="Call notify() to send.", skills_dir=None
        )
    assert "does not reference notify" not in caplog.text

    # Case-insensitive: no warning
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="task", prompt="Call NOTIFY() when done.", skills_dir=None
        )
    assert "does not reference notify" not in caplog.text

    # Absent: warning with task name
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="cleanup-task", prompt="Delete old temp files.", skills_dir=None
        )
    assert "does not reference notify" in caplog.text and "cleanup-task" in caplog.text

    # Skill with notify suppresses warning
    skill1 = tmp_path / "skills" / "daily-digest"
    skill1.mkdir(parents=True)
    (skill1 / "SKILL.md").write_text("# Daily Digest\nCall notify() to send it.", encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="digest", prompt="Run the daily-digest skill.", skills_dir=tmp_path / "skills"
        )
    assert "does not reference notify" not in caplog.text

    # Missing dir: safe
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        _check_notify_reference(
            task_name="task", prompt="Run some-skill.", skills_dir=tmp_path / "nonexistent"
        )
    assert "does not reference notify" in caplog.text


@_asyncio_session
async def test_sync_schedules_notify_validation(pool, caplog) -> None:
    """sync_schedules warns only for prompt tasks missing notify(); job tasks and notify-present tasks silenced."""
    import logging

    from butlers.core.scheduler import sync_schedules

    schedules = [
        {
            "name": "good-prompt",
            "cron": "0 8 * * *",
            "dispatch_mode": "prompt",
            "prompt": "Do something and notify(channel='telegram').",
        },
        {
            "name": "bad-prompt",
            "cron": "0 9 * * *",
            "dispatch_mode": "prompt",
            "prompt": "Do something quietly without alerting anyone.",
        },
        {"name": "job-task", "cron": "0 10 * * *", "dispatch_mode": "job", "job_name": "some-job"},
    ]
    with caplog.at_level(logging.WARNING, logger="butlers.core.scheduler"):
        await sync_schedules(pool, schedules)

    warning_records = [r for r in caplog.records if "does not reference notify" in r.message]
    assert len(warning_records) == 1
    assert "bad-prompt" in warning_records[0].message
