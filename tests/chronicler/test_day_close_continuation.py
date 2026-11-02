"""Private scheduler ownership and durable protocol controls; no database IO."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest

from butlers.chronicler.day_close_continuation import DayCloseContinuation
from butlers.chronicler.day_close_refresh import DayCloseRefreshService
from butlers.chronicler.day_close_writer import DayCloseCacheWriteOutcome

pytestmark = pytest.mark.unit
DAY = date(2026, 5, 1)


class StatePool:
    """Storage protocol with committed state, independently re-read by owners."""

    def __init__(self):
        self.state = {}
        self.discovery_cursors = []
        self.days = [DAY]
        self.cache = None
        self.witness = None
        self.budget = 1200
        from dataclasses import asdict
        from uuid import UUID

        from butlers.chronicler.models import CorrectedEpisode, Layer, Precision, Privacy

        instant = datetime(2026, 5, 1, 12, tzinfo=UTC)
        self.rows = [
            asdict(
                CorrectedEpisode(
                    id=UUID(int=1),
                    source_name="core.sessions",
                    source_ref="synthetic:visible",
                    episode_type="session_episode",
                    start_at=instant,
                    end_at=instant,
                    precision=Precision.EXACT,
                    title="Synthetic work",
                    payload={},
                    privacy=Privacy.NORMAL,
                    retention_days=None,
                    tombstone_at=None,
                    canonical_start_at=instant,
                    canonical_end_at=instant,
                    canonical_title="Synthetic work",
                    canonical_privacy=Privacy.NORMAL,
                    corrected_at=None,
                    correction_note=None,
                    created_at=instant,
                    updated_at=instant,
                    layer=Layer.ACTIVITY,
                )
            )
        ]

    async def fetchval(self, sql, key):
        return self.state.get(key)

    async def execute(self, sql, key, value):
        assert "INSERT INTO state" in sql
        self.state[key] = dict(value)

    async def fetch(self, sql, *args):
        if "WITH days" in sql:
            # The installed asyncpg date encoder calls toordinal(), rather
            # than parsing ISO strings passed to a PostgreSQL date argument.
            args[2].toordinal()
            self.discovery_cursors.append(args[2])
            return [{"local_date": day} for day in self.days]
        assert "v_episodes_corrected" in sql or "v_point_events_corrected" in sql
        return self.rows if "v_episodes_corrected" in sql else []

    async def fetchrow(self, sql, *args):
        if "SELECT value FROM state" in sql:
            return {"value": self.state[args[0]]} if args[0] in self.state else None
        if "tier2_cache" in sql:
            return self.cache
        if "covered_local_days" in sql:
            return self.witness
        assert "scheduled_tasks" in sql
        return {
            "prompt": "Close the day",
            "complexity": "workhorse",
            "max_token_budget": self.budget,
        }


def owner():
    pool = StatePool()
    daemon = SimpleNamespace(
        _dispatch_scheduled_task=AsyncMock(return_value={"output": "synthetic"})
    )
    service = DayCloseRefreshService(pool, daemon)

    async def writer(*args, **kwargs):
        now = datetime.now(UTC)
        pool.cache = {"cache_built_at": now}
        pool.witness = {"covered_at": now}
        return DayCloseCacheWriteOutcome(coverage_recorded=True)

    service.writer = writer
    return DayCloseContinuation(service, "UTC"), pool, daemon


async def test_actual_service_durable_revision_restart_and_shared_budget():
    continuation, pool, daemon = owner()
    result = await continuation.resume_once()
    assert result["status"] == "success"
    assert pool.discovery_cursors == [date.min]
    assert pool.state["day_close:cursor:UTC"] == {"date": DAY.isoformat()}
    assert daemon._dispatch_scheduled_task.await_count == 1
    assert daemon._dispatch_scheduled_task.call_args.kwargs["max_token_budget"] == 1200
    assert (
        daemon._dispatch_scheduled_task.call_args.kwargs["trigger_source"]
        == "api:day_close_refresh:2026-05-01"
    )
    saved = next(value for key, value in pool.state.items() if "pending:" in key)
    assert saved["processed"] == saved["revision"]
    assert set(saved) == {"date", "timezone", "revision", "processed", "outcome"}
    restarted = DayCloseContinuation(continuation.service, "UTC")
    assert await restarted.resume_once() == {"status": "unchanged"}
    assert pool.discovery_cursors == [date.min, DAY]
    assert daemon._dispatch_scheduled_task.await_count == 1
    restarted.day_close_ran = True
    assert await restarted.resume_once() == {"status": "skipped"}


@pytest.mark.parametrize(
    "cursor_state",
    [None, {}, [], {"date": None}, {"date": ""}, {"date": 42}, {"date": "2026-02-30"}],
)
async def test_malformed_discovery_cursor_preserves_pending_work(cursor_state):
    from copy import deepcopy

    continuation, pool, daemon = owner()
    pool.state["day_close:cursor:UTC"] = cursor_state
    pool.state["day_close:pending:day_close:2026-05-01:tz:UTC"] = {
        "date": DAY.isoformat(),
        "timezone": "UTC",
        "revision": "synthetic-pending-revision",
        "processed": None,
    }
    before = deepcopy(pool.state)
    try:
        with pytest.raises(ValueError, match="Invalid day-close discovery cursor"):
            await continuation.resume_once()
        assert pool.state == before
        assert pool.discovery_cursors == []
        daemon._dispatch_scheduled_task.assert_not_awaited()
    finally:
        await continuation.service.settle()
    assert not continuation.service.has_pending


async def test_rate_deferral_and_invalid_result_never_process_revision():
    continuation, pool, daemon = owner()
    pool.cache = {"cache_built_at": datetime.now(UTC)}
    pool.witness = {"covered_at": datetime.now(UTC)}
    result = await continuation.resume_once()
    assert result["code"] == "day_close_rate_limited"
    pending = next(value for key, value in pool.state.items() if "pending:" in key)
    assert pending["processed"] is None
    assert pending["retry_at"]
    await continuation.resume_once()
    daemon._dispatch_scheduled_task.assert_not_awaited()
    pool.state.clear()
    pool.cache = pool.witness = None

    async def invalid(*args, **kwargs):
        pool.cache = {"cache_built_at": datetime.now(UTC)}
        pool.witness = {"covered_at": datetime.now(UTC)}
        return DayCloseCacheWriteOutcome(
            invalid_reason="inadmissible_prose", coverage_recorded=True
        )

    continuation.service.writer = invalid
    assert (await continuation.resume_once())["invalid"] is True
    assert (
        next(value for key, value in pool.state.items() if "pending:" in key)["processed"] is None
    )


async def test_revision_move_during_refresh_stays_pending(monkeypatch):
    continuation, pool, daemon = owner()
    revisions = iter(["before", "after"])
    monkeypatch.setattr(continuation, "revision", AsyncMock(side_effect=lambda *_: next(revisions)))
    assert (await continuation.resume_once())["status"] == "success"
    assert (
        next(value for key, value in pool.state.items() if "pending:" in key)["processed"] is None
    )


@pytest.mark.parametrize("witness_write_fails", [True, False])
async def test_current_canonical_witness_write_not_old_row_controls_completion(witness_write_fails):
    """A late empty revision needs this writer's success, not an old row."""
    from contextlib import asynccontextmanager
    from datetime import timedelta

    from butlers.chronicler.day_close_writer import write_day_close_cache

    class WitnessPool(StatePool):
        def __init__(self):
            super().__init__()
            self.rows = []
            self.witness = {"covered_at": datetime.now(UTC) - timedelta(days=2)}
            self.witness_writes = 0

        @asynccontextmanager
        async def acquire(self):
            pool = self

            class Connection:
                async def execute(self, sql, *args):
                    assert "INSERT INTO covered_local_days" in sql
                    assert "WHERE covered_local_days.origin" in sql
                    assert args == (DAY, "UTC", "day_close_success")
                    pool.witness_writes += 1
                    if witness_write_fails:
                        raise OSError("synthetic witness storage unavailable")
                    # Real idempotent authoritative writes retain covered_at.
                    return "INSERT 0 0"

            yield Connection()

    pool = WitnessPool()
    old_covered_at = pool.witness["covered_at"]
    daemon = SimpleNamespace(
        _dispatch_scheduled_task=AsyncMock(
            return_value={
                "success": True,
                "output": "",
                "tool_calls": [
                    {
                        "name": "chronicler_day_close_bundle",
                        "outcome": "success",
                        "input": {"date_label": DAY.isoformat(), "timezone": "UTC"},
                        "result": {"date": DAY.isoformat(), "episodes": [], "events": []},
                    }
                ],
            }
        )
    )
    service = DayCloseRefreshService(pool, daemon)
    service.writer = write_day_close_cache
    continuation = DayCloseContinuation(service, "UTC")
    result = await continuation.resume_once()
    pending = next(value for key, value in pool.state.items() if "pending:" in key)
    assert pool.witness_writes == 1
    assert pool.witness["covered_at"] == old_covered_at
    if witness_write_fails:
        assert result["status"] == "error"
        assert result["code"] == "coverage_witness_write_failed"
        assert pending["processed"] is None
        assert not any("day_close:success:" in key for key in pool.state)
    else:
        assert result["status"] == "success"
        assert result["quiet"] is True
        assert pending["processed"] == pending["revision"]
        assert any("day_close:success:" in key for key in pool.state)


@pytest.mark.parametrize("phase", ["work", "release", "caller_cancel"])
async def test_native_holder_reset_remains_owned_after_deadline_and_repeatcancel(phase):
    """Exercise installed Pool.release/holder.reset, not a subclass recorder."""
    pool = await asyncpg.create_pool(min_size=0, max_size=1, max_inactive_connection_lifetime=0)
    assert type(pool) is asyncpg.Pool
    holder = pool._holders[0]
    reset_started, finish_reset = asyncio.Event(), asyncio.Event()

    class Connection:
        _protocol = SimpleNamespace(queries_count=0, _is_cancelling=lambda: False)

        def _set_proxy(self, proxy):
            self.proxy = proxy

        def _on_release(self):
            pass

        def is_closed(self):
            return False

        async def reset(self, *, timeout):  # noqa: ASYNC109 - installed asyncpg callback ABI
            reset_started.set()
            await finish_reset.wait()

        def terminate(self):
            holder._release_on_close()

        async def close(self):
            holder._release_on_close()

    holder._con = Connection()
    holder._generation = pool._generation
    service = DayCloseRefreshService(pool, SimpleNamespace())
    work_started = asyncio.Event()

    async def operation():
        async with pool.acquire():
            work_started.set()
            if phase != "release":
                await asyncio.Event().wait()

    caller = asyncio.create_task(service.run_owned(operation(), bound_s=0.02))
    try:
        await work_started.wait()
        if phase == "caller_cancel":
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            caller.cancel()
        else:
            result = await caller
            assert result["code"] == "dispatch_timeout"
        await reset_started.wait()
        assert service.has_pending
        assert await service.close(0.01) is False
        assert not pool._closed
        assert holder._in_use is not None and not holder._in_use.done()
        # A second caller cancellation cannot cancel the retained reset.
        cancelled = asyncio.create_task(service.settle())
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert service.has_pending
    finally:
        finish_reset.set()
        await service.settle()
        assert not service.has_pending
        await pool.close()
    assert pool._closed


async def test_real_database_shutdown_quarantines_native_release_until_settled(tmp_path):
    from butlers.config import ButlerConfig
    from butlers.daemon import ButlerDaemon
    from butlers.db import Database

    daemon = ButlerDaemon(tmp_path)
    daemon.config = ButlerConfig(name="chronicler", port=41111, shutdown_timeout_s=0.02)
    daemon.db = Database("synthetic_no_connections")
    native = await asyncpg.create_pool(min_size=0, max_size=1)
    daemon.db.pool = native
    service = DayCloseRefreshService(native, daemon)
    daemon._chronicler_refresh_service = service
    entered, released = asyncio.Event(), asyncio.Event()

    async def resistant():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            await released.wait()

    caller = asyncio.create_task(service.run_owned(resistant(), bound_s=100))
    try:
        await entered.wait()
        with pytest.raises(TimeoutError, match="resources retained"):
            await daemon.shutdown()
        assert service.has_pending
        assert daemon.db.pool is native
        assert not native._closed
        assert daemon._chronicler_cleanup_task is not None
        with pytest.raises(TimeoutError, match="resources retained"):
            await daemon.shutdown()
    finally:
        released.set()
        await asyncio.gather(caller, return_exceptions=True)
        await daemon._chronicler_cleanup_task
        await service.settle()
        if not native._closed:
            await native.close()
    assert daemon.db.pool is None
    assert native._closed


async def test_real_scheduler_tail_reaches_shared_canonical_owner(tmp_path, monkeypatch):
    from butlers.config import ButlerConfig
    from butlers.daemon import ButlerDaemon

    continuation, pool, _ = owner()
    daemon = ButlerDaemon(tmp_path)
    daemon.config = ButlerConfig(name="chronicler", port=41111)
    daemon.db = SimpleNamespace(pool=pool)
    daemon.spawner = SimpleNamespace(trigger=AsyncMock(return_value={"output": "synthetic"}))
    daemon._chronicler_refresh_service = continuation.service
    continuation.service.daemon = daemon
    daemon._chronicler_continuation = continuation
    monkeypatch.setattr(
        "butlers.core.general_settings.resolve_general_timezone", AsyncMock(return_value="UTC")
    )

    async def rollup(pool, job_args):
        return {"days_processed": 1}

    async def tick(pool, dispatch, **kwargs):
        await dispatch(
            trigger_source="schedule:chronicler_rollup_daily", job_name="chronicler_rollup_daily"
        )
        return 1

    async def stop(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr("butlers.chronicler.jobs.run_rollup_daily", rollup)
    monkeypatch.setattr("butlers.daemon._tick", tick)
    monkeypatch.setattr("butlers.background._sweep_domain_event_reactions", stop)
    # Only the loop interval is cold configuration; no new scheduling policy.
    daemon.config.scheduler.tick_interval_seconds = 0
    await daemon._scheduler_loop()
    assert daemon.spawner.trigger.await_count == 1
    assert (
        daemon.spawner.trigger.call_args.kwargs["trigger_source"]
        == "api:day_close_refresh:2026-05-01"
    )
    assert continuation.committed is True
    assert next(value for key, value in pool.state.items() if "pending:" in key)["processed"]


async def test_revision_uses_lawful_projection_not_private_payload_or_timestamp(monkeypatch):
    from dataclasses import replace
    from uuid import UUID

    from butlers.chronicler.models import CorrectedEpisode, Layer, Precision, Privacy

    continuation, _, _ = owner()
    start = datetime(2026, 5, 1, 12, tzinfo=UTC)
    row = CorrectedEpisode(
        id=UUID(int=1),
        source_name="core.sessions",
        source_ref="synthetic:private",
        episode_type="session_episode",
        start_at=start,
        end_at=start,
        precision=Precision.EXACT,
        title="PRIVATE_SYNTHETIC_TITLE",
        payload={"raw": "PRIVATE_SYNTHETIC_PAYLOAD"},
        privacy=Privacy.SENSITIVE,
        retention_days=None,
        tombstone_at=None,
        canonical_start_at=start,
        canonical_end_at=start,
        canonical_title="PRIVATE_SYNTHETIC_TITLE",
        canonical_privacy=Privacy.SENSITIVE,
        corrected_at=None,
        correction_note=None,
        created_at=start,
        updated_at=start,
        layer=Layer.ACTIVITY,
    )
    reader = AsyncMock(return_value=[row])
    monkeypatch.setattr("butlers.chronicler.day_close_continuation.list_episodes", reader)
    monkeypatch.setattr(
        "butlers.chronicler.day_close_continuation.list_point_events", AsyncMock(return_value=[])
    )
    first = await continuation.revision(DAY)
    reader.return_value = [
        replace(
            row,
            source_ref="OTHER_PRIVATE_REF",
            title="OTHER_PRIVATE_TITLE",
            payload={"raw": "OTHER_PRIVATE_BODY"},
            updated_at=datetime.now(UTC),
        )
    ]
    assert await continuation.revision(DAY) == first
    reader.return_value = [
        replace(row, privacy=Privacy.RESTRICTED, canonical_privacy=Privacy.RESTRICTED)
    ]
    restricted = await continuation.revision(DAY)
    reader.return_value = []
    assert await continuation.revision(DAY) == restricted
    reader.return_value = [replace(row, privacy=Privacy.NORMAL, canonical_privacy=Privacy.NORMAL)]
    assert await continuation.revision(DAY) != restricted


async def test_zero_fill_checkpoint_is_not_automatic_quiet_authority():
    continuation, pool, daemon = owner()
    pool.rows = []
    result = await continuation.resume_once()
    assert result["code"] == "day_close_material_unproven"
    daemon._dispatch_scheduled_task.assert_not_awaited()
    pending = next(value for key, value in pool.state.items() if "pending:" in key)
    assert pending["processed"] is None
    assert pool.witness is None
