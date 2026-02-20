"""Tests for the internal scheduler loop added to ButlerDaemon.

Covers all acceptance criteria:
1. Daemon starts scheduler loop after server is ready.
2. tick() called approximately every tick_interval_seconds (default 60).
3. Loop continues after tick() exceptions (logged, not fatal).
4. Custom interval configurable via [butler.scheduler].tick_interval_seconds.
5. Invalid interval (0 or negative) rejected at startup.
6. Loop cancelled cleanly during shutdown; in-progress tick() allowed to finish.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.config import ButlerConfig, ConfigError, SchedulerConfig, load_config
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit


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
        'name = "butler_test"',
    ]
    if tick_interval_seconds is not None:
        lines += [
            "",
            "[butler.scheduler]",
            f"tick_interval_seconds = {tick_interval_seconds}",
        ]
    (tmp_path / "butler.toml").write_text("\n".join(lines))
    return tmp_path


def _patch_infra():
    """Return patches for all infrastructure dependencies used by ButlerDaemon.start()."""
    mock_pool = AsyncMock()

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butler_test"

    mock_audit_db = MagicMock()
    mock_audit_db.connect = AsyncMock()
    mock_audit_db.close = AsyncMock()
    mock_audit_db.pool = AsyncMock()

    def _db_from_env_factory(db_name: str) -> MagicMock:
        if db_name == "butler_switchboard":
            return mock_audit_db
        return mock_db

    mock_spawner = MagicMock()
    mock_spawner.stop_accepting = MagicMock()
    mock_spawner.drain = AsyncMock()

    return {
        "db_from_env": patch("butlers.daemon.Database.from_env", side_effect=_db_from_env_factory),
        "run_migrations": patch("butlers.daemon.run_migrations", new_callable=AsyncMock),
        "validate_credentials": patch("butlers.daemon.validate_credentials"),
        "validate_module_credentials": patch(
            "butlers.daemon.validate_module_credentials_async",
            new_callable=AsyncMock,
            return_value={},
        ),
        "validate_core_credentials": patch(
            "butlers.daemon.validate_core_credentials_async",
            new_callable=AsyncMock,
        ),
        "init_telemetry": patch("butlers.daemon.init_telemetry"),
        "sync_schedules": patch("butlers.daemon.sync_schedules", new_callable=AsyncMock),
        "FastMCP": patch("butlers.daemon.FastMCP"),
        "Spawner": patch("butlers.daemon.Spawner", return_value=mock_spawner),
        "get_adapter": patch(
            "butlers.daemon.get_adapter",
            return_value=type(
                "MockAdapter",
                (),
                {
                    "binary_name": "claude",
                    "__init__": lambda self, **kwargs: None,
                },
            ),
        ),
        "shutil_which": patch("butlers.daemon.shutil.which", return_value="/usr/bin/claude"),
        "start_mcp_server": patch.object(ButlerDaemon, "_start_mcp_server", new_callable=AsyncMock),
        "connect_switchboard": patch.object(
            ButlerDaemon, "_connect_switchboard", new_callable=AsyncMock
        ),
        "recover_route_inbox": patch.object(
            ButlerDaemon, "_recover_route_inbox", new_callable=AsyncMock
        ),
        "mock_db": mock_db,
        "mock_audit_db": mock_audit_db,
        "mock_pool": mock_pool,
        "mock_spawner": mock_spawner,
    }


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestSchedulerConfig:
    """Verify SchedulerConfig parsing from butler.toml."""

    def test_default_tick_interval(self, tmp_path: Path) -> None:
        """tick_interval_seconds defaults to 60 when not configured."""
        _make_butler_toml(tmp_path)
        config = load_config(tmp_path)
        assert config.scheduler.tick_interval_seconds == 60

    def test_custom_tick_interval(self, tmp_path: Path) -> None:
        """Custom tick_interval_seconds is parsed from [butler.scheduler]."""
        _make_butler_toml(tmp_path, tick_interval_seconds=120)
        config = load_config(tmp_path)
        assert config.scheduler.tick_interval_seconds == 120

    def test_invalid_zero_interval_rejected(self, tmp_path: Path) -> None:
        """tick_interval_seconds=0 is rejected with ConfigError."""
        _make_butler_toml(tmp_path, tick_interval_seconds=0)
        with pytest.raises(ConfigError, match="tick_interval_seconds"):
            load_config(tmp_path)

    def test_invalid_negative_interval_rejected(self, tmp_path: Path) -> None:
        """Negative tick_interval_seconds is rejected with ConfigError."""
        _make_butler_toml(tmp_path, tick_interval_seconds=-10)
        with pytest.raises(ConfigError, match="tick_interval_seconds"):
            load_config(tmp_path)

    def test_scheduler_config_dataclass_defaults(self) -> None:
        """SchedulerConfig defaults are correct."""
        cfg = SchedulerConfig()
        assert cfg.tick_interval_seconds == 60


# ---------------------------------------------------------------------------
# Scheduler loop lifecycle tests
# ---------------------------------------------------------------------------


class TestSchedulerLoopStartup:
    """Verify the scheduler loop task is created during daemon startup."""

    async def test_scheduler_loop_task_created_on_start(self, tmp_path: Path) -> None:
        """Daemon should create _scheduler_loop_task after start()."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        try:
            assert daemon._scheduler_loop_task is not None
            assert isinstance(daemon._scheduler_loop_task, asyncio.Task)
        finally:
            daemon._scheduler_loop_task.cancel()
            try:
                await daemon._scheduler_loop_task
            except asyncio.CancelledError:
                pass
            await daemon.shutdown()

    async def test_scheduler_loop_task_cleared_on_shutdown(self, tmp_path: Path) -> None:
        """After shutdown(), _scheduler_loop_task should be None."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["recover_route_inbox"],
        ):
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()

        await daemon.shutdown()

        assert daemon._scheduler_loop_task is None


# ---------------------------------------------------------------------------
# Scheduler loop behavior tests (unit: directly test _scheduler_loop)
# ---------------------------------------------------------------------------


class TestSchedulerLoopBehavior:
    """Unit tests for the _scheduler_loop() coroutine behavior."""

    async def test_tick_called_after_interval(self, tmp_path: Path) -> None:
        """tick() should be called after sleeping for tick_interval_seconds."""
        tick_calls: list[int] = []

        async def mock_tick(pool, dispatch_fn, *, stagger_key=None):
            tick_calls.append(1)
            return 0

        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)

        # Set up minimal state needed by _scheduler_loop
        daemon.config = ButlerConfig(name="test", port=9100)
        daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=1)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        with patch("butlers.daemon._tick", side_effect=mock_tick):
            task = asyncio.create_task(daemon._scheduler_loop())
            # Allow two tick intervals to pass
            await asyncio.sleep(2.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have been called at least once (probably twice in 2.5s with 1s interval)
        assert len(tick_calls) >= 1

    async def test_tick_uses_butler_name_stagger_key(self, tmp_path: Path) -> None:
        """_scheduler_loop should pass butler name as the scheduler stagger key."""
        seen_stagger_keys: list[str | None] = []

        async def mock_tick(pool, dispatch_fn, *, stagger_key=None):
            seen_stagger_keys.append(stagger_key)
            return 0

        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)
        daemon.config = ButlerConfig(name="health", port=9100)
        daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=1)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        with patch("butlers.daemon._tick", side_effect=mock_tick):
            task = asyncio.create_task(daemon._scheduler_loop())
            await asyncio.sleep(1.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert seen_stagger_keys
        assert all(key == "health" for key in seen_stagger_keys)

    async def test_tick_exception_does_not_break_loop(self, tmp_path: Path) -> None:
        """tick() exception should be logged but the loop should continue."""
        tick_call_count = 0
        second_tick_seen = asyncio.Event()

        async def failing_then_ok_tick(pool, dispatch_fn, *, stagger_key=None):
            nonlocal tick_call_count
            tick_call_count += 1
            if tick_call_count == 1:
                raise RuntimeError("Simulated tick failure")
            second_tick_seen.set()
            return 0

        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)
        daemon.config = ButlerConfig(name="test", port=9100)
        daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=1)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        with patch("butlers.daemon._tick", side_effect=failing_then_ok_tick):
            task = asyncio.create_task(daemon._scheduler_loop())
            await asyncio.wait_for(second_tick_seen.wait(), timeout=4.0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Loop continued after first failure — second tick was called
        assert tick_call_count >= 2

    async def test_loop_cancelled_on_shutdown(self, tmp_path: Path) -> None:
        """Cancelling _scheduler_loop_task should terminate the loop cleanly."""
        tick_started = asyncio.Event()
        tick_unblocked = asyncio.Event()

        async def blocking_tick(pool, dispatch_fn, *, stagger_key=None):
            tick_started.set()
            await tick_unblocked.wait()
            return 0

        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)
        daemon.config = ButlerConfig(name="test", port=9100)
        # Short interval so tick starts quickly
        daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=1)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        with patch("butlers.daemon._tick", side_effect=blocking_tick):
            task = asyncio.create_task(daemon._scheduler_loop())

            # Wait for tick to actually start
            await asyncio.wait_for(tick_started.wait(), timeout=3.0)

            # Now cancel the task
            task.cancel()
            # Unblock tick so it can complete (simulates in-progress protection)
            tick_unblocked.set()

            try:
                await task
            except asyncio.CancelledError:
                pass

        # Task should be done
        assert task.done()

    async def test_loop_returns_when_db_not_ready(self, tmp_path: Path) -> None:
        """_scheduler_loop should return immediately if DB or spawner is None."""
        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)
        daemon.config = ButlerConfig(name="test", port=9100)
        daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=60)

        # db is None (not ready)
        daemon.db = None
        daemon.spawner = None

        tick_mock = AsyncMock()
        with patch("butlers.daemon._tick", tick_mock):
            # Should return immediately without error
            await asyncio.wait_for(daemon._scheduler_loop(), timeout=1.0)

        tick_mock.assert_not_called()

    async def test_custom_interval_used_in_loop(self, tmp_path: Path) -> None:
        """Loop should use the configured tick_interval_seconds."""
        tick_times: list[float] = []

        import time

        async def recording_tick(pool, dispatch_fn, *, stagger_key=None):
            tick_times.append(time.monotonic())
            return 0

        butler_dir = _make_butler_toml(tmp_path)
        daemon = ButlerDaemon(butler_dir)
        daemon.config = ButlerConfig(name="test", port=9100)
        daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=1)

        mock_pool = AsyncMock()
        mock_db = MagicMock()
        mock_db.pool = mock_pool
        daemon.db = mock_db

        mock_spawner = MagicMock()
        mock_spawner.trigger = AsyncMock()
        daemon.spawner = mock_spawner

        start_time = time.monotonic()
        with patch("butlers.daemon._tick", side_effect=recording_tick):
            task = asyncio.create_task(daemon._scheduler_loop())
            # Wait just over 1 interval
            await asyncio.sleep(1.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # At least one tick should have fired
        assert len(tick_times) >= 1
        # First tick should have fired after approximately 1 second
        assert tick_times[0] - start_time >= 0.9  # allow small tolerance

    async def test_shutdown_waits_for_tick_completion(self, tmp_path: Path) -> None:
        """Shutdown should allow an in-progress tick() to finish before cancelling."""
        tick_completed = asyncio.Event()
        tick_started = asyncio.Event()

        async def slow_tick(pool, dispatch_fn, *, stagger_key=None):
            tick_started.set()
            await asyncio.sleep(0.3)
            tick_completed.set()
            return 0

        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
            patches["validate_core_credentials"],
            patches["init_telemetry"],
            patches["sync_schedules"],
            patches["FastMCP"],
            patches["Spawner"],
            patches["get_adapter"],
            patches["shutil_which"],
            patches["start_mcp_server"],
            patches["connect_switchboard"],
            patches["recover_route_inbox"],
            patch("butlers.daemon._tick", side_effect=slow_tick),
        ):
            # Use very short interval so tick starts immediately
            daemon = ButlerDaemon(butler_dir)
            await daemon.start()
            # Override interval to trigger quickly
            daemon.config.scheduler = SchedulerConfig(tick_interval_seconds=1)

            # Restart the scheduler loop with short interval
            if daemon._scheduler_loop_task:
                daemon._scheduler_loop_task.cancel()
                try:
                    await daemon._scheduler_loop_task
                except asyncio.CancelledError:
                    pass

            daemon._scheduler_loop_task = asyncio.create_task(daemon._scheduler_loop())
            # Wait for tick to start
            await asyncio.wait_for(tick_started.wait(), timeout=3.0)

            # Initiate shutdown while tick is in progress
            await daemon.shutdown()

        # The tick should have completed (shutdown waited for it)
        assert tick_completed.is_set()
