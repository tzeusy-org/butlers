"""Tests for the internal scheduler loop added to ButlerDaemon.

Covers:
1. Scheduler loop task created on start, cleared after shutdown.
2. tick() called with correct params (stagger_key, butler_name).
3. Loop continues after tick() exceptions.
4. Custom interval configurable via [butler.scheduler].tick_interval_seconds.
5. Invalid interval (0 or negative) rejected at startup.
6. Loop returns immediately if DB or spawner not ready.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig, ConfigError, SchedulerConfig, load_config
from butlers.daemon import ButlerDaemon
from butlers.testing.daemon import write_butler_config

pytestmark = pytest.mark.unit

# Real sleep used by fast_sleep to yield control without blocking
_real_sleep = asyncio.sleep


async def _fast_sleep(delay: float) -> None:
    """Mock sleep that yields control to the event loop without real delay."""
    await _real_sleep(0)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_butler_toml(
    tmp_path: Path,
    *,
    tick_interval_seconds: int | None = None,
) -> Path:
    """Write a minimal butler.toml with optional scheduler config."""
    lines = [
        "[butler]",
        'name = "test-butler"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butlers"',
        'schema = "test_butler"',
    ]
    if tick_interval_seconds is not None:
        lines += [
            "",
            "[butler.scheduler]",
            f"tick_interval_seconds = {tick_interval_seconds}",
        ]
    (tmp_path / "butler.toml").write_text("\n".join(lines))
    return tmp_path


def _make_daemon_with_loop(butler_dir: Path, name: str, interval: int) -> ButlerDaemon:
    """Create a ButlerDaemon with scheduler config set up for loop tests."""
    daemon = ButlerDaemon(butler_dir)
    daemon.config = ButlerConfig(name=name, port=9100)
    daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=interval)
    mock_pool = AsyncMock()
    mock_db = MagicMock()
    mock_db.pool = mock_pool
    daemon.db = mock_db
    mock_spawner = MagicMock()
    mock_spawner.trigger = AsyncMock()
    daemon.spawner = mock_spawner
    return daemon


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSchedulerConfig:
    """Verify SchedulerConfig parsing from butler.toml."""

    def test_scheduler_config(self, tmp_path: Path) -> None:
        """Default 60s; custom interval parsed; zero and negative rejected."""
        _make_butler_toml(tmp_path)
        assert load_config(tmp_path).scheduler.tick_interval_seconds == 60
        assert SchedulerConfig().tick_interval_seconds == 60

        _make_butler_toml(tmp_path, tick_interval_seconds=120)
        assert load_config(tmp_path).scheduler.tick_interval_seconds == 120

        for invalid in (0, -10):
            _make_butler_toml(tmp_path, tick_interval_seconds=invalid)
            with pytest.raises(ConfigError, match="tick_interval_seconds"):
                load_config(tmp_path)


# ---------------------------------------------------------------------------
# Scheduler loop lifecycle and behavior tests
# ---------------------------------------------------------------------------


class TestSchedulerLoopBehavior:
    """Tests for the _scheduler_loop() coroutine behavior."""

    @pytest.mark.integration
    @pytest.mark.asyncio(loop_scope="session")
    async def test_task_is_created_on_start_and_cleared_on_shutdown(
        self, booted_daemon, tmp_path: Path
    ) -> None:
        """The real startup starts the scheduler loop; the real shutdown clears it."""
        booted = await booted_daemon(write_butler_config(tmp_path, "general"))
        daemon = booted.daemon
        assert isinstance(daemon._scheduler_loop_task, asyncio.Task)
        assert not daemon._scheduler_loop_task.done()

        await booted.shutdown()

        assert daemon._scheduler_loop_task is None

    async def test_tick_gets_the_runtime_pool_and_name(self, tmp_path: Path) -> None:
        """tick() gets the runtime pool and name.

        QA's policy read uses tick's primary pool, so it must be the daemon's own
        role-scoped pool rather than the audit pool passed as eligibility_pool.
        """
        # tick() called with correct stagger_key and butler_name
        tick_calls: list[tuple] = []

        async def capturing_tick(
            pool, dispatch_fn, *, stagger_key=None, butler_name=None, **kwargs
        ):
            tick_calls.append((stagger_key, butler_name))
            tick_pools.append(pool)
            return 0

        tick_pools: list[object] = []
        daemon2 = _make_daemon_with_loop(_make_butler_toml(tmp_path), name="health", interval=1)
        daemon2._audit_pool = object()
        with (
            patch("butlers.daemon._tick", side_effect=capturing_tick),
            patch("butlers.daemon.asyncio.sleep", side_effect=_fast_sleep),
        ):
            task = asyncio.create_task(daemon2._scheduler_loop())
            await _real_sleep(0)
            await _real_sleep(0)
            await _real_sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(tick_calls) >= 1
        assert all(key == "health" for key, _ in tick_calls)
        assert all(name == "health" for _, name in tick_calls)
        assert tick_pools and all(p is daemon2.db.pool for p in tick_pools)

    async def test_chronicler_scheduler_registers_day_close_hooks(self, tmp_path: Path) -> None:
        """Only Chronicler prepares and completes the owner-local day-close prompt."""
        daemon = _make_daemon_with_loop(
            _make_butler_toml(tmp_path),
            name="chronicler",
            interval=1,
        )
        tick_kwargs: list[dict] = []

        async def capture_tick(_pool, _dispatch_fn, **kwargs):
            tick_kwargs.append(kwargs)
            return 0

        with (
            patch(
                "butlers.core.general_settings.resolve_general_timezone",
                new_callable=AsyncMock,
                return_value="Asia/Singapore",
            ),
            patch("butlers.daemon._tick", side_effect=capture_tick),
            patch("butlers.daemon.asyncio.sleep", side_effect=_fast_sleep),
        ):
            task = asyncio.create_task(daemon._scheduler_loop())
            await _real_sleep(0)
            await _real_sleep(0)
            await _real_sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert tick_kwargs
        assert all(
            set(kwargs["prompt_hooks"]) == {"chronicler_day_close"} for kwargs in tick_kwargs
        )
        assert all(
            set(kwargs["completion_hooks"]) == {"chronicler_day_close"} for kwargs in tick_kwargs
        )
        assert all(kwargs["default_timezone"] == "Asia/Singapore" for kwargs in tick_kwargs)

    async def test_exception_tolerance_and_db_guard_and_custom_interval(
        self, tmp_path: Path
    ) -> None:
        """tick() exception is logged but loop continues; DB=None exits loop; custom interval used."""
        butler_dir = _make_butler_toml(tmp_path)

        # Tick exception: loop continues after failure
        tick_call_count = 0
        second_tick_seen = asyncio.Event()

        async def failing_then_ok_tick(
            pool, dispatch_fn, *, stagger_key=None, butler_name=None, **kwargs
        ):
            nonlocal tick_call_count
            tick_call_count += 1
            if tick_call_count == 1:
                raise RuntimeError("Simulated tick failure")
            second_tick_seen.set()
            return 0

        daemon = _make_daemon_with_loop(butler_dir, name="test", interval=1)
        with (
            patch("butlers.daemon._tick", side_effect=failing_then_ok_tick),
            patch("butlers.daemon.asyncio.sleep", side_effect=_fast_sleep),
        ):
            task = asyncio.create_task(daemon._scheduler_loop())
            await asyncio.wait_for(second_tick_seen.wait(), timeout=4.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert tick_call_count >= 2

        # DB is None → returns immediately
        daemon2 = ButlerDaemon(butler_dir)
        daemon2.config = ButlerConfig(name="test", port=9100)
        daemon2.config.scheduler = SchedulerConfig(tick_interval_seconds=60)
        daemon2.db = None
        daemon2.spawner = None
        tick_mock = AsyncMock()
        with patch("butlers.daemon._tick", tick_mock):
            await asyncio.wait_for(daemon2._scheduler_loop(), timeout=1.0)
        tick_mock.assert_not_called()

        # Custom interval used in sleep calls
        sleep_calls: list[float] = []

        async def recording_sleep(delay: float) -> None:
            sleep_calls.append(delay)
            await _real_sleep(0)

        daemon3 = _make_daemon_with_loop(butler_dir, name="test", interval=42)
        with (
            patch("butlers.daemon._tick", new_callable=AsyncMock, return_value=0),
            patch("butlers.daemon.asyncio.sleep", side_effect=recording_sleep),
        ):
            task3 = asyncio.create_task(daemon3._scheduler_loop())
            await _real_sleep(0)
            await _real_sleep(0)
            task3.cancel()
            try:
                await task3
            except asyncio.CancelledError:
                pass
        assert all(s == 42 for s in sleep_calls)


async def test_deferred_actual_delivery_rechecks_outage_and_recovery(monkeypatch):
    """REQ-context-bus-016: no direct deferred-send bypass of unavailable reads."""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from butlers import background

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.fetch = AsyncMock(return_value=[])
    client = MagicMock(call_tool=AsyncMock(return_value=SimpleNamespace(is_error=False, data={})))
    envelope = {
        "schema_version": "notify.v1",
        "delivery": {
            "intent": "send",
            "priority": "high",
            "message": "routine",
            "recipient": "owner",
        },
    }

    completed = []

    async def tick(pool, dispatch, *, notify_fn, **kwargs):
        from uuid import uuid4

        from butlers.core.scheduler.deferred_pass import _tick_deferred_notification_pass

        now = datetime.now(UTC)
        anchor = now - timedelta(minutes=1)
        state = {"status": "pending", "deliver_at": anchor}
        queued = MagicMock(fetchval=AsyncMock(return_value=True))
        row = {
            "id": uuid4(),
            "butler_name": "health",
            "channel": "telegram",
            "message": "routine",
            "priority": "high",
            "envelope": envelope,
        }

        async def fetch(*args):
            return [row] if state["status"] == "pending" else []

        async def execute(sql, *args):
            if "SET status = 'expired'" in sql and state["deliver_at"] < args[0]:
                state["status"] = "expired"
            elif "SET status = 'delivered'" in sql:
                state["status"] = "delivered"
            return "UPDATE 1"

        queued.fetch = AsyncMock(side_effect=fetch)
        queued.execute = AsyncMock(side_effect=execute)
        pool.fetchrow.side_effect = RuntimeError("policy unavailable")
        assert await _tick_deferred_notification_pass(queued, now, notify_fn=notify_fn) == 0
        assert state == {"status": "pending", "deliver_at": anchor}
        client.call_tool.assert_not_awaited()
        pool.fetchrow.side_effect = None
        pool.fetch.side_effect = RuntimeError("context unavailable")
        for _ in range(2):
            assert await _tick_deferred_notification_pass(queued, now, notify_fn=notify_fn) == 0
            assert state == {"status": "pending", "deliver_at": anchor}
            client.call_tool.assert_not_awaited()
        pool.fetch.side_effect = None
        assert await _tick_deferred_notification_pass(queued, now, notify_fn=notify_fn) == 1
        assert state["status"] == "delivered"
        assert await _tick_deferred_notification_pass(queued, now, notify_fn=notify_fn) == 0
        client.call_tool.assert_awaited_once()
        # Original queue expiry still retires stale work; it never becomes a
        # send permission while attention reads remain unavailable.
        state["status"] = "pending"
        pool.fetch.side_effect = RuntimeError("context unavailable")
        assert (
            await _tick_deferred_notification_pass(
                queued, now + timedelta(hours=25), notify_fn=notify_fn
            )
            == 0
        )
        assert state == {"status": "expired", "deliver_at": anchor}
        client.call_tool.assert_awaited_once()
        completed.append(True)
        raise asyncio.CancelledError

    monkeypatch.setattr(background.asyncio, "sleep", _fast_sleep)
    await asyncio.wait_for(
        background.scheduler_loop(
            pool=pool,
            dispatch_fn=AsyncMock(),
            interval=0,
            butler_name="health",
            tick_fn=tick,
            get_switchboard_client=lambda: client,
            get_db=lambda: SimpleNamespace(pool=pool),
        ),
        timeout=2,
    )
    # The production loop catches ordinary tick failures. Assert outside it so
    # neither a swallowed test assertion nor cancellation can manufacture PASS.
    assert completed == [True]
