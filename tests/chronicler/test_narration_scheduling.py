"""Unit tests for the chronicler_narrate_daily job wiring (bu-v9y18,
telemetry-distillation bead 6).

Covers:
(a) the job is dispatchable via the deterministic-schedule registry.
(b) job_args validation for run_narrate_daily (unsupported key, bad timezone).
(c) the job computes "yesterday" in the given timezone and delegates to
    narration.narrate_daily_rollup.
(d) ChroniclerModule's module-default schedule registration includes
    chronicler_narrate_daily, following the memory module's
    ``ensure_module_default_schedule`` contract.

Pure-unit tests — no Docker / PostgreSQL required.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.chronicler.jobs import run_narrate_daily
from butlers.modules.registry import default_registry
from butlers.scheduled_jobs import (
    _DETERMINISTIC_SCHEDULE_JOB_REGISTRY,
    _resolve_deterministic_schedule_job_name,
)

pytestmark = pytest.mark.unit
_CHRONICLER_CONFIG = Path(__file__).resolve().parents[2] / "roster/chronicler"


# ---------------------------------------------------------------------------
# (a) Registry coverage
# ---------------------------------------------------------------------------


def test_chronicler_narrate_daily_is_registered_callable_and_resolvable() -> None:
    jobs = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY.get("chronicler", {})
    assert "chronicler_narrate_daily" in jobs
    assert callable(jobs["chronicler_narrate_daily"])

    resolved = _resolve_deterministic_schedule_job_name(
        butler_name="chronicler",
        trigger_source="schedule:chronicler_narrate_daily",
        job_name="chronicler_narrate_daily",
    )
    assert resolved == "chronicler_narrate_daily"


# ---------------------------------------------------------------------------
# (b) job_args validation
# ---------------------------------------------------------------------------


async def test_run_narrate_daily_rejects_unsupported_job_args() -> None:
    with pytest.raises(RuntimeError, match="unsupported keys"):
        await run_narrate_daily(AsyncMock(), job_args={"unknown": 1})


async def test_run_narrate_daily_rejects_empty_timezone() -> None:
    with pytest.raises(RuntimeError):
        await run_narrate_daily(AsyncMock(), job_args={"timezone": ""})


async def test_run_narrate_daily_rejects_non_string_timezone() -> None:
    with pytest.raises(RuntimeError):
        await run_narrate_daily(AsyncMock(), job_args={"timezone": 7})


async def test_run_narrate_daily_rejects_unknown_timezone() -> None:
    with pytest.raises(RuntimeError, match="unknown timezone"):
        await run_narrate_daily(AsyncMock(), job_args={"timezone": "Not/AZone"})


# ---------------------------------------------------------------------------
# (c) yesterday-date computation + delegation
# ---------------------------------------------------------------------------


async def test_run_narrate_daily_accepts_no_job_args_and_computes_yesterday(monkeypatch) -> None:
    """No job_args -> default timezone (Asia/Singapore); the closed local
    date passed to narrate_daily_rollup is "yesterday" relative to _now()."""
    captured: dict[str, Any] = {}

    async def _fake_narrate(pool, *, local_date, timezone):
        captured["local_date"] = local_date
        captured["timezone"] = timezone
        return {"local_date": local_date.isoformat(), "status": "labeled"}

    monkeypatch.setattr("butlers.chronicler.narration.narrate_daily_rollup", _fake_narrate)
    # 2026-07-06 01:20 UTC is 2026-07-06 09:20 SGT -> "yesterday" is 07-05.
    monkeypatch.setattr(
        "butlers.chronicler.jobs._now", lambda: datetime(2026, 7, 6, 1, 20, tzinfo=UTC)
    )

    result = await run_narrate_daily(AsyncMock(), job_args=None)

    assert captured["timezone"] == "Asia/Singapore"
    assert captured["local_date"] == date(2026, 7, 5)
    assert result == {"local_date": "2026-07-05", "status": "labeled"}


async def test_run_narrate_daily_honors_explicit_timezone_override(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_narrate(pool, *, local_date, timezone):
        captured["local_date"] = local_date
        captured["timezone"] = timezone
        return {}

    monkeypatch.setattr("butlers.chronicler.narration.narrate_daily_rollup", _fake_narrate)
    monkeypatch.setattr(
        "butlers.chronicler.jobs._now", lambda: datetime(2026, 7, 6, 1, 20, tzinfo=UTC)
    )

    await run_narrate_daily(AsyncMock(), job_args={"timezone": "UTC"})

    assert captured["timezone"] == "UTC"
    assert captured["local_date"] == date(2026, 7, 5)


# ---------------------------------------------------------------------------
# (d) ChroniclerModule default schedule registration
# ---------------------------------------------------------------------------


def _load_chronicler_module():
    registry = default_registry()
    modules = registry.load_all({})
    return next(m for m in modules if m.name == "chronicler")


async def test_chronicler_module_registers_narrate_daily_default_schedule(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    async def _fake_ensure(pool, **kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("butlers.core.scheduler.ensure_module_default_schedule", _fake_ensure)

    module = _load_chronicler_module()
    fake_db = AsyncMock()
    fake_db.pool = object()

    await module._register_default_schedules(fake_db)

    # chronicler_rollup_daily/chronicler_routines_mine are also module-default
    # schedules; find this one by name so this test doesn't need updating
    # every time another default schedule is added.
    narrate_call = next(c for c in calls if c["name"] == "chronicler_narrate_daily")
    assert narrate_call["job_name"] == "chronicler_narrate_daily"


def _chronicler_source_pool():
    """Actual repository SQL flow, SOFTWARE only: no PG locking/durability proof."""
    import copy
    from dataclasses import asdict
    from uuid import UUID

    from butlers.chronicler.models import DailyRollup, DailyRollupFlag
    from tests.core.test_obligation_schedule_dispatch import OccurrenceConnection
    from tests.core.test_obligation_schedule_sources import RecordingPool

    class Connection(OccurrenceConnection):
        async def fetchrow(self, sql, *args):
            if "current_schema()" in sql:
                return {"schema": "chronicler", "role": self.role, "shadowed": self.shadow}
            return await super().fetchrow(sql, *args)

        async def fetch(self, sql, *args):
            if "chronicler.scheduled_tasks" in sql:
                self.calls.append(sql)
                return copy.deepcopy([row for row in self.rows.values() if row["name"] == args[0]])
            if "FROM daily_rollups" in sql:
                return copy.deepcopy(self.rollups)
            if "FROM daily_rollup_flags" in sql:
                return copy.deepcopy(self.flags)
            if "FROM v_episodes_corrected" in sql:
                return copy.deepcopy(self.episodes)
            return await super().fetch(sql, *args)

        async def fetchval(self, sql, *args):
            if "pg_my_temp_schema()" in sql:
                return getattr(self, "material_shadow", False)
            if sql == "SELECT clock_timestamp()":
                return datetime(2026, 7, 6, 1, 20, tzinfo=UTC)
            return await super().fetchval(sql, *args)

        async def execute(self, sql, *args):
            if sql.startswith("LOCK TABLE chronicler."):
                self.calls.append(sql)
                return "LOCK TABLE"
            return await super().execute(sql, *args)

    pool = RecordingPool()
    conn = pool.connection = Connection()
    conn.role = "butler_chronicler_rw"
    row = {
        "id": UUID(int=401),
        "name": "chronicler_narrate_daily",
        "cron": "20 1 * * *",
        "dispatch_mode": "job",
        "prompt": None,
        "job_name": "chronicler_narrate_daily",
        "job_args": None,
        "enabled": True,
        "timezone": None,
        "source": "db",
    }
    conn.rows = {row["id"]: row}
    conn.rollups = [
        asdict(
            DailyRollup(
                local_date=date(2026, 7, 5),
                lane="work",
                seconds=100,
                episode_count=1,
                id=UUID(int=402),
            )
        )
    ]
    conn.flags = [
        asdict(
            DailyRollupFlag(
                local_date=date(2026, 7, 5), flag_type="sleep_missing", id=UUID(int=403)
            )
        )
    ]
    conn.episodes = [
        {
            "source_name": "spotify.session_summary",
            "episode_type": "listening_episode",
            "title": "Selected title",
            "start_at": datetime(2026, 7, 5, 10, tzinfo=UTC),
            "end_at": datetime(2026, 7, 5, 11, tzinfo=UTC),
            "layer": "activity",
            "trigger_source": None,
        }
    ]
    pool.fetch = conn.fetch
    return pool


async def test_registered_narration_stages_real_reduced_material_before_legacy_call(monkeypatch):
    """Real registered caller→jobs→narration→repository, SOFTWARE, never admission."""
    import json

    import butlers.chronicler.narration as narration

    pool = _chronicler_source_pool()
    calls = []

    class Dispatcher:
        def __init__(self, *_args, **_kwargs):
            pass

        async def call(self, message, *, system_prompt):
            assert len(pool.connection.definitions) == 1 and pool.connection.depth == 0
            calls.append((message, system_prompt))
            return '{"day_summary":"Existing result","flag_labels":{}}'

    monkeypatch.setenv("BUTLERS_CHRONICLER_DARK_PREPARATION", "1")
    monkeypatch.setenv("CHRONICLER_NARRATION_ENABLED", "true")
    monkeypatch.setattr(narration, "DiscretionDispatcher", Dispatcher)
    writer = AsyncMock(return_value=1)
    monkeypatch.setattr(narration, "set_daily_rollup_day_narrative", writer)
    monkeypatch.setattr(
        "butlers.chronicler.jobs._now", lambda: datetime(2026, 7, 6, 1, 20, tzinfo=UTC)
    )
    handler = _DETERMINISTIC_SCHEDULE_JOB_REGISTRY["chronicler"]["chronicler_narrate_daily"]
    result = await handler(pool, None)
    assert result["status"] == "labeled" and writer.await_count == 1
    conn = pool.connection
    assert len(conn.definitions) == len(conn.occurrences) == 1
    body = next(iter(conn.revisions.values()))
    payload = body["payload"]
    assert payload["material"]["user_message"] == calls[0][0]
    assert payload["material"]["system_prompt"] == calls[0][1]
    assert json.loads(calls[0][0])["local_date"] == "2026-07-05"
    assert payload["admission"] == "unverified_unavailable"
    assert body["declaration"]["capabilities"] == [] and body["creator"] is None
    assert conn.depth == 0 and any("occurrence_captured" == item[3] for item in conn.outbox)


async def _source_inputs(pool):
    from zoneinfo import ZoneInfo

    from butlers.chronicler.narration import (
        _SYSTEM_PROMPT,
        _fetch_top_episode_titles,
        _local_day_bounds_utc,
        build_narration_prompt,
    )
    from butlers.chronicler.storage import list_daily_rollup_flags, list_daily_rollups

    day, timezone = date(2026, 7, 5), "Asia/Singapore"
    start, end = _local_day_bounds_utc(day, ZoneInfo(timezone))
    rows = await list_daily_rollups(pool, local_date=day)
    flags = await list_daily_rollup_flags(pool, local_date=day)
    titles = await _fetch_top_episode_titles(pool, day_start_utc=start, day_end_utc=end)
    return dict(
        local_date=day,
        timezone=timezone,
        rollup_rows=rows,
        flag_rows=flags,
        top_episode_titles=titles,
        user_message=build_narration_prompt(
            local_date=day, rollup_rows=rows, flag_rows=flags, top_episode_titles=titles
        ),
        system_prompt=_SYSTEM_PROMPT,
    )


async def test_chronicler_source_duplicate_currentness_rollback_and_unknown_commit(monkeypatch):
    import copy
    from contextlib import asynccontextmanager

    from butlers.core.obligation_admission import SourceUnavailable
    from butlers.core.obligation_chronicler_sources import ChroniclerPreparation, ChroniclerRuntime
    from butlers.core.obligation_sources import SourceConflict

    pool = _chronicler_source_pool()
    inputs = await _source_inputs(pool)
    prepare = ChroniclerPreparation(pool, None)
    first = await prepare.prepare_committed(**inputs)
    state = copy.deepcopy((pool.connection.definitions, pool.connection.occurrences))
    assert await prepare.prepare_committed(**inputs) == first
    assert (pool.connection.definitions, pool.connection.occurrences) == state
    async with pool.acquire() as connection, connection.transaction():
        assert await ChroniclerRuntime().read_submission_in_transaction(connection, first) == first
    pool.connection.rollups[0]["seconds"] += 1
    with pytest.raises(SourceConflict, match="material_changed"):
        await prepare.prepare_committed(**inputs)
    async with pool.acquire() as connection, connection.transaction():
        with pytest.raises(SourceConflict, match="material_changed"):
            await ChroniclerRuntime().read_submission_in_transaction(connection, first)
    assert (pool.connection.definitions, pool.connection.occurrences) == state

    failed = _chronicler_source_pool()
    execute = failed.connection.execute

    async def refuse_journal(sql, *args):
        if "INSERT INTO obligation_source_outbox" in sql:
            raise OSError("controlled source journal failure")
        return await execute(sql, *args)

    failed.connection.execute = refuse_journal
    with pytest.raises(SourceUnavailable):
        await ChroniclerPreparation(failed, None).prepare_committed(**await _source_inputs(failed))
    assert not failed.connection.definitions and not failed.connection.occurrences

    lost = _chronicler_source_pool()
    acquire, fail = lost.acquire, True

    @asynccontextmanager
    async def lost_reply():
        nonlocal fail
        async with acquire() as connection:
            yield connection
        if fail:
            fail = False
            raise OSError("controlled release acknowledgement loss")

    lost.acquire = lost_reply
    retry = ChroniclerPreparation(lost, None)
    captured = await _source_inputs(lost)
    with pytest.raises(SourceUnavailable):
        await retry.prepare_committed(**captured)
    original = copy.deepcopy(lost.connection.occurrences)
    reconciled = await retry.prepare_committed(**captured)
    assert lost.connection.occurrences == original and reconciled.definition == first.definition
    assert (
        reconciled.obligation.capabilities == () and reconciled.obligation.created_exposed is True
    )


@pytest.mark.parametrize(
    "fault",
    [
        "wrongrole",
        "shadow",
        "material_shadow",
        "disabled",
        "job",
        "source",
        "args",
        "id",
        "titles",
        "feeder",
        "prompt",
    ],
)
async def test_chronicler_source_refuses_incoherent_real_material(fault):
    from butlers.core.obligation_chronicler_sources import ChroniclerPreparation
    from butlers.core.obligation_sources import SourceConflict

    pool = _chronicler_source_pool()
    inputs = await _source_inputs(pool)
    conn = pool.connection
    row = next(iter(conn.rows.values()))
    if fault == "wrongrole":
        conn.role = "butler_home_rw"
    elif fault == "shadow":
        conn.shadow = True
    elif fault == "material_shadow":
        conn.material_shadow = True
    elif fault == "disabled":
        row["enabled"] = False
    elif fault == "job":
        row["job_name"] = "chronicler_rollup_daily"
    elif fault == "source":
        row["source"] = "invented"
    elif fault == "args":
        row["job_args"] = {"timezone": "UTC"}
    elif fault == "id":
        inputs["rollup_rows"][0].id = "00000000-0000-0000-0000-000000000001"
    elif fault == "titles":
        conn.episodes[0]["title"] = "Changed selected title"
    elif fault == "feeder":
        conn.flags[0]["flag_type"] = "feeder_dark"
    else:
        inputs["user_message"] = "forged prompt"
    with pytest.raises(SourceConflict):
        await ChroniclerPreparation(pool, None).prepare_committed(**inputs)
    assert not conn.definitions and not conn.occurrences


async def test_chronicler_source_selector_registration_and_reconciliation(monkeypatch):
    import copy
    import sys

    from butlers.config import load_config
    from butlers.core.obligation_chronicler_sources import (
        ChroniclerPreparation,
        _registration,
        configured_chronicler_preparer,
    )
    from butlers.core.obligation_git_sources import (
        capture_loaded_git_sources,
        reconcile_loaded_git_sources,
    )
    from butlers.core.obligation_sources import SourceConflict
    from butlers.core.runtime_config import RuntimeConfig
    from tests.contracts.test_tool_authorization_catalog import _complete_core_catalog

    pool = _chronicler_source_pool()
    monkeypatch.delenv("BUTLERS_CHRONICLER_DARK_PREPARATION", raising=False)
    assert configured_chronicler_preparer(pool, {}) is None and pool.connection.calls == []
    declaration, _ = _registration()
    assert declaration.capabilities == ()
    first = await ChroniclerPreparation(pool, None).prepare_committed(**await _source_inputs(pool))
    module = sys.modules[type(_load_chronicler_module()).__module__]
    original = copy.deepcopy(module._DEFAULT_SCHEDULES)
    wrong = copy.deepcopy(original)
    next(e for e in wrong if e["name"] == "chronicler_narrate_daily")["obligation"][
        "capabilities"
    ] = [{"capability": "chronicler__invented_writer"}]
    monkeypatch.setattr(module, "_DEFAULT_SCHEDULES", wrong)
    with pytest.raises(SourceConflict, match="writer_unavailable"):
        _registration()
    monkeypatch.setattr(module, "_DEFAULT_SCHEDULES", original)
    config = load_config(_CHRONICLER_CONFIG)
    snapshot = capture_loaded_git_sources(
        config, RuntimeConfig("chronicler"), await _complete_core_catalog("chronicler")
    )
    await reconcile_loaded_git_sources(pool, snapshot)
    assert pool.connection.definitions[first.definition.source_id]["state"] == "active"
    pool.connection.rollups[0]["seconds"] += 1
    await reconcile_loaded_git_sources(pool, snapshot)
    assert next(iter(pool.connection.definitions.values()))["state"] == "deleted"
    assert all(o["state"] == "cancelled" for o in pool.connection.occurrences.values())
    with pytest.raises(SourceConflict):
        await ChroniclerPreparation(pool, None).prepare_committed(**await _source_inputs(pool))


async def test_chronicler_source_cancellation_deadline_retains_inline_ownership(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager

    import butlers.core.obligation_chronicler_sources as source
    from butlers.core.obligation_admission import SourceUnavailable

    pool = _chronicler_source_pool()
    entered, released = asyncio.Event(), asyncio.Event()
    fetch, acquire = pool.connection.fetch, pool.acquire

    async def blocked(sql, *args):
        if "chronicler.scheduled_tasks" in sql:
            entered.set()
            await asyncio.Event().wait()
        return await fetch(sql, *args)

    @asynccontextmanager
    async def owned():
        try:
            async with acquire() as connection:
                yield connection
        finally:
            released.set()

    pool.connection.fetch, pool.acquire = blocked, owned
    inputs = await _source_inputs(pool)
    task = asyncio.create_task(source.ChroniclerPreparation(pool, None).prepare_committed(**inputs))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert released.is_set() and pool.connection.depth == 0 and not pool.connection.definitions
    monkeypatch.setattr(source, "PREPARATION_SECONDS", 0.01)
    released.clear()
    with pytest.raises((SourceUnavailable, TimeoutError)):
        await source.ChroniclerPreparation(pool, None).prepare_committed(**inputs)
    # The deadline may return before a ready cleanup task gets its next turn;
    # explicitly join the retained owner before checking eventual settlement.
    pending = set(source.chronicler_preparation_owner(pool)._pending)
    if pending:
        _, unsettled = await asyncio.wait(pending, timeout=1)
        assert not unsettled
    assert released.is_set() and pool.connection.depth == 0


@pytest.mark.parametrize(
    "phase", ["work", "release", "repeated_cancel", "shutdown", "shutdown_cancel", "healthy"]
)
async def test_chronicler_actual_asyncpg_release_is_owned_through_settlement(
    monkeypatch, tmp_path, phase
):
    """Installed release/holder code, gated physical reset; no PostgreSQL execution."""
    import asyncio
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from asyncpg.pool import Pool, PoolConnectionHolder, PoolConnectionProxy

    import butlers.core.obligation_chronicler_sources as source
    from butlers.core.obligation_admission import SourceUnavailable

    pool = _chronicler_source_pool()
    inputs = await _source_inputs(pool)
    # Resolve cold module imports before shortening only the operation bound.
    source._registration()
    entered, reset_entered, reset_finish, reset_done = (asyncio.Event() for _ in range(4))
    fetch, acquire = pool.connection.fetch, pool.acquire
    releases = []
    pool_events = []

    async def blocked_fetch(sql, *args):
        if phase == "work" and "chronicler.scheduled_tasks" in sql:
            entered.set()
            await asyncio.Event().wait()
        return await fetch(sql, *args)

    class Physical:
        _protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: False)

        def is_closed(self):
            return False

        def _on_release(self):
            pass

        def _set_proxy(self, proxy):
            pass

        async def reset(self, *, timeout):  # noqa: ASYNC109 - actual asyncpg reset interface
            reset_entered.set()
            await reset_finish.wait()
            releases.append("reset")
            reset_done.set()

        def terminate(self):
            assert reset_done.is_set(), "pool terminated beneath owned source release"
            pool_events.append("terminate")

        async def close(self):
            assert reset_done.is_set(), "holder closed beneath owned source release"
            pool_events.append("holder_close")

    class PhysicalPool(Pool):
        def __init__(self):
            self._generation, self._reset = 0, None
            self._loop = asyncio.get_running_loop()
            self._queue = asyncio.Queue()
            self._initialized, self._initializing = True, False
            self._closed, self._closing = False, False

        def acquire(self):
            return gated_acquire()

        async def close(self):
            assert reset_done.is_set(), "shared pool closed beneath owned source release"
            pool_events.append("pool_close")
            await super().close()

    physical_pool = PhysicalPool()
    holder = PoolConnectionHolder(physical_pool, max_queries=100, setup=None, max_inactive_time=0)
    physical = Physical()
    holder._con, holder._generation = physical, 0
    holder._in_use = asyncio.get_running_loop().create_future()
    proxy = PoolConnectionProxy(holder, physical)
    holder._proxy = proxy
    physical_pool._holders = [holder]

    @asynccontextmanager
    async def gated_acquire():
        try:
            async with acquire() as connection:
                yield connection
        finally:
            await Pool.release(physical_pool, proxy)

    physical_pool.connection = pool.connection
    pool = physical_pool
    pool.connection.fetch = blocked_fetch
    monkeypatch.setattr(source, "PREPARATION_SECONDS", 0.3 if phase == "healthy" else 0.03)
    before_tasks = asyncio.all_tasks()
    operation = asyncio.create_task(
        source.ChroniclerPreparation(pool, None).prepare_committed(**inputs)
    )
    daemon = None
    shutdown = None
    try:
        if phase == "work":
            await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(reset_entered.wait(), 1)
        if phase == "healthy":
            assert not operation.done() and holder._in_use is not None
            assert pool.connection.depth == 0 and pool.connection.definitions
            reset_finish.set()
            result = await asyncio.wait_for(operation, 1)
            assert result.obligation.capabilities == () and holder._in_use is None
            assert reset_done.is_set() and releases == ["reset"]
            return
        if phase == "repeated_cancel":
            operation.cancel()
            await asyncio.sleep(0)
            operation.cancel()
        done, pending = await asyncio.wait({operation}, timeout=0.15)
        assert done == {operation} and not pending
        with pytest.raises(
            asyncio.CancelledError if phase == "repeated_cancel" else SourceUnavailable
        ):
            operation.result()
        assert holder._in_use is not None and not reset_done.is_set()
        owner = source.chronicler_preparation_owner(pool)
        retained = set(owner._pending)
        assert retained and not any(task.done() for task in retained)
        with pytest.raises(SourceUnavailable):
            await source.ChroniclerPreparation(pool, None).prepare_committed(**inputs)
        with pytest.raises(RuntimeError, match="cleanup_incomplete"):
            await owner.aclose()
        assert set(owner._pending) == retained and releases == []
        if phase.startswith("shutdown"):
            from butlers.daemon import ButlerDaemon
            from butlers.db import Database

            daemon = ButlerDaemon(tmp_path)
            daemon.config = SimpleNamespace(name="chronicler", shutdown_timeout_s=0.03)
            daemon.db = Database("controlled_chronicler_shutdown")
            daemon.db.pool = pool
            independent = AsyncMock()
            daemon.blob_store = SimpleNamespace(close=independent)
            shutdown = asyncio.create_task(daemon.shutdown())
            if phase == "shutdown_cancel":
                await asyncio.sleep(0)
                shutdown.cancel()
                await asyncio.sleep(0)
                shutdown.cancel()
            done, pending = await asyncio.wait({shutdown}, timeout=1)
            assert done == {shutdown} and not pending
            with pytest.raises(
                asyncio.CancelledError if phase == "shutdown_cancel" else RuntimeError
            ):
                shutdown.result()
            independent.assert_awaited_once()
            assert pool_events == [] and daemon.db.pool is pool
            assert set(owner._pending) == retained and daemon._admission_cleanup_tasks
        reset_finish.set()
        done, pending = await asyncio.wait(retained, timeout=1)
        assert done == retained and not pending
        await owner.aclose()
        assert not owner._pending and holder._in_use is None
        assert reset_done.is_set() and releases == ["reset"] and pool.connection.depth == 0
        if daemon is not None:
            downstream = set(daemon._admission_cleanup_tasks)
            if downstream:
                _, unsettled = await asyncio.wait(downstream, timeout=1)
                assert not unsettled
            assert pool._closed and daemon.db.pool is None
            assert pool_events == ["pool_close", "holder_close"]
            lifetime = daemon._catalog_shutdown_task
            with pytest.raises(RuntimeError, match="cleanup_incomplete"):
                await daemon.shutdown()
            assert daemon._catalog_shutdown_task is lifetime
            assert pool_events == ["pool_close", "holder_close"]
    finally:
        reset_finish.set()
        if not operation.done():
            operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        if shutdown is not None:
            await asyncio.gather(shutdown, return_exceptions=True)
        children = asyncio.all_tasks() - before_tasks
        if children:
            _, unsettled = await asyncio.wait(children, timeout=1)
            assert not unsettled
        assert holder._in_use is None


async def test_chronicler_native_pool_constructor_lifecycle_and_registry_lifetime(tmp_path):
    """Exact native class, zero connections, actual daemon close and collectible owner."""
    import asyncio
    import gc
    import weakref
    from types import SimpleNamespace

    import asyncpg

    import butlers.core.obligation_chronicler_sources as source
    from butlers.core.obligation_admission import SourceUnavailable
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database

    async def exercise():
        connect = AsyncMock(side_effect=AssertionError("native pool must not connect"))
        pool = asyncpg.create_pool(min_size=0, max_size=1, connect=connect)
        assert type(pool) is asyncpg.pool.Pool
        assert source.chronicler_preparation_owner(pool) is None
        with pytest.raises(SourceUnavailable, match="pool_uninitialized"):
            source.ChroniclerPreparation(pool, None)
        await pool
        with pytest.raises(TypeError):
            weakref.ref(pool)
        assert source.chronicler_preparation_owner(pool) is None
        preparation = source.ChroniclerPreparation(pool, None)
        owner = preparation.owner
        assert source.chronicler_preparation_owner(pool) is owner
        queue_ref, owner_ref = weakref.ref(pool._queue), weakref.ref(owner)
        del preparation, owner
        gc.collect()
        assert owner_ref() is source.chronicler_preparation_owner(pool)

        entered, finish = asyncio.Event(), asyncio.Event()

        async def held():
            entered.set()
            await finish.wait()
            return "settled"

        owner = owner_ref()
        pending = asyncio.create_task(owner.run(held))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            gc.collect()
            assert source.chronicler_preparation_owner(pool) is owner
            assert owner._pending and not pending.done()
            finish.set()
            assert await asyncio.wait_for(pending, 1) == "settled"
        finally:
            finish.set()
            await asyncio.gather(pending, return_exceptions=True)
        daemon = ButlerDaemon(tmp_path)
        daemon.config = SimpleNamespace(name="chronicler", shutdown_timeout_s=0.1)
        daemon.db = Database("native_chronicler_lifetime")
        daemon.db.pool = pool
        await daemon.shutdown()
        assert daemon._catalog_shutdown_task.done()
        assert pool.is_closing() and daemon.db.pool is None and owner._closed
        assert source.chronicler_preparation_owner(pool) is owner
        with pytest.raises(SourceUnavailable):
            await source.ChroniclerPreparation(pool, None).prepare_committed()
        connect.assert_not_awaited()
        return queue_ref, owner_ref

    queue_ref, owner_ref = await exercise()
    gc.collect()
    assert queue_ref() is None and owner_ref() is None
