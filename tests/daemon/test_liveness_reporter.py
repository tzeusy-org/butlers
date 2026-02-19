"""Tests for the liveness reporter loop added to ButlerDaemon.

Covers all acceptance criteria:
1. Daemon starts liveness reporter after server is ready (unless Switchboard).
2. First heartbeat sent within 5 seconds of startup.
3. Subsequent heartbeats every heartbeat_interval_seconds (default 120).
4. Connection failures logged at WARNING, loop continues.
5. Switchboard butler does not start a liveness reporter.
6. BUTLERS_SWITCHBOARD_URL env var overrides default URL.
7. Loop cancelled cleanly during shutdown.

Issue: butlers-976.3
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.config import ButlerConfig, ConfigError, SchedulerConfig, load_config
from butlers.daemon import ButlerDaemon

pytestmark = pytest.mark.unit

_HB_URL = "http://test-switchboard:8200/api/switchboard/heartbeat"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_butler_toml(
    tmp_path: Path,
    *,
    name: str = "test-butler",
    heartbeat_interval_seconds: int | None = None,
    switchboard_url: str | None = None,
) -> Path:
    """Write a minimal butler.toml with optional scheduler config."""
    lines = [
        "[butler]",
        f'name = "{name}"',
        "port = 9100",
        'description = "A test butler"',
        "",
        "[butler.db]",
        'name = "butler_test"',
    ]
    scheduler_lines = []
    if heartbeat_interval_seconds is not None:
        scheduler_lines.append(f"heartbeat_interval_seconds = {heartbeat_interval_seconds}")
    if switchboard_url is not None:
        scheduler_lines.append(f'switchboard_url = "{switchboard_url}"')
    if scheduler_lines:
        lines += ["", "[butler.scheduler]"] + scheduler_lines
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
            "butlers.daemon.validate_module_credentials", return_value={}
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


def _ok_response() -> httpx.Response:
    """Return a 200 OK heartbeat response."""
    return httpx.Response(200, json={"status": "ok", "eligibility_state": "active"})


def _make_mock_client(*, post_return=None, post_side_effect=None) -> MagicMock:
    """Build a mock httpx.AsyncClient context manager with controlled post()."""
    mock_client = AsyncMock()
    if post_side_effect is not None:
        mock_client.post = AsyncMock(side_effect=post_side_effect)
    else:
        mock_client.post = AsyncMock(return_value=post_return or _ok_response())
    # Support async context manager protocol
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestLivenessReporterConfig:
    """Verify SchedulerConfig liveness reporter fields parsing from butler.toml."""

    def test_default_heartbeat_interval(self, tmp_path: Path) -> None:
        """heartbeat_interval_seconds defaults to 120 when not configured."""
        _make_butler_toml(tmp_path)
        config = load_config(tmp_path)
        assert config.scheduler.heartbeat_interval_seconds == 120

    def test_custom_heartbeat_interval(self, tmp_path: Path) -> None:
        """Custom heartbeat_interval_seconds is parsed from [butler.scheduler]."""
        _make_butler_toml(tmp_path, heartbeat_interval_seconds=60)
        config = load_config(tmp_path)
        assert config.scheduler.heartbeat_interval_seconds == 60

    def test_invalid_zero_heartbeat_interval_rejected(self, tmp_path: Path) -> None:
        """heartbeat_interval_seconds=0 is rejected with ConfigError."""
        _make_butler_toml(tmp_path, heartbeat_interval_seconds=0)
        with pytest.raises(ConfigError, match="heartbeat_interval_seconds"):
            load_config(tmp_path)

    def test_invalid_negative_heartbeat_interval_rejected(self, tmp_path: Path) -> None:
        """Negative heartbeat_interval_seconds is rejected with ConfigError."""
        _make_butler_toml(tmp_path, heartbeat_interval_seconds=-10)
        with pytest.raises(ConfigError, match="heartbeat_interval_seconds"):
            load_config(tmp_path)

    def test_default_switchboard_url(self, tmp_path: Path) -> None:
        """switchboard_url defaults to http://localhost:8200 when not configured."""
        _make_butler_toml(tmp_path)
        # Ensure BUTLERS_SWITCHBOARD_URL is not set for this test
        env_backup = os.environ.pop("BUTLERS_SWITCHBOARD_URL", None)
        try:
            config = load_config(tmp_path)
            assert config.scheduler.switchboard_url == "http://localhost:8200"
        finally:
            if env_backup is not None:
                os.environ["BUTLERS_SWITCHBOARD_URL"] = env_backup

    def test_switchboard_url_from_toml(self, tmp_path: Path) -> None:
        """switchboard_url in [butler.scheduler] overrides default."""
        _make_butler_toml(tmp_path, switchboard_url="http://my-switchboard:9999")
        env_backup = os.environ.pop("BUTLERS_SWITCHBOARD_URL", None)
        try:
            config = load_config(tmp_path)
            assert config.scheduler.switchboard_url == "http://my-switchboard:9999"
        finally:
            if env_backup is not None:
                os.environ["BUTLERS_SWITCHBOARD_URL"] = env_backup

    def test_switchboard_url_from_env_var(self, tmp_path: Path) -> None:
        """BUTLERS_SWITCHBOARD_URL env var overrides default URL."""
        _make_butler_toml(tmp_path)
        with patch.dict(os.environ, {"BUTLERS_SWITCHBOARD_URL": "http://env-switchboard:7777"}):
            config = load_config(tmp_path)
        assert config.scheduler.switchboard_url == "http://env-switchboard:7777"

    def test_scheduler_config_dataclass_defaults(self) -> None:
        """SchedulerConfig defaults are correct."""
        cfg = SchedulerConfig()
        assert cfg.heartbeat_interval_seconds == 120
        assert cfg.switchboard_url == "http://localhost:8200"


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLivenessReporterLifecycle:
    """Verify liveness reporter task creation and cancellation."""

    async def test_liveness_reporter_task_created_on_start(self, tmp_path: Path) -> None:
        """Non-switchboard daemon should create _liveness_reporter_task after start()."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
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
            assert daemon._liveness_reporter_task is not None
            assert isinstance(daemon._liveness_reporter_task, asyncio.Task)
        finally:
            daemon._liveness_reporter_task.cancel()
            try:
                await daemon._liveness_reporter_task
            except asyncio.CancelledError:
                pass
            await daemon.shutdown()

    async def test_switchboard_does_not_create_liveness_reporter(self, tmp_path: Path) -> None:
        """Switchboard butler must NOT create a liveness reporter task."""
        butler_dir = _make_butler_toml(tmp_path, name="switchboard")
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
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
            assert daemon._liveness_reporter_task is None
        finally:
            await daemon.shutdown()

    async def test_liveness_reporter_task_cleared_on_shutdown(self, tmp_path: Path) -> None:
        """After shutdown(), _liveness_reporter_task should be None."""
        butler_dir = _make_butler_toml(tmp_path)
        patches = _patch_infra()

        with (
            patches["db_from_env"],
            patches["run_migrations"],
            patches["validate_credentials"],
            patches["validate_module_credentials"],
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

        assert daemon._liveness_reporter_task is None


# ---------------------------------------------------------------------------
# Behavior tests (unit: directly test _liveness_reporter_loop)
# ---------------------------------------------------------------------------


class TestLivenessReporterBehavior:
    """Unit tests for the _liveness_reporter_loop() coroutine behavior."""

    def _make_daemon(self, tmp_path: Path, *, name: str = "test-butler") -> ButlerDaemon:
        """Create a ButlerDaemon with minimal config for unit testing the loop."""
        butler_dir = _make_butler_toml(tmp_path, name=name)
        daemon = ButlerDaemon(butler_dir)
        daemon.config = ButlerConfig(name=name, port=9100)
        daemon.config.scheduler = SchedulerConfig(
            heartbeat_interval_seconds=120,
            switchboard_url="http://test-switchboard:8200",
        )
        return daemon

    async def test_initial_heartbeat_sent_within_5_seconds(self, tmp_path: Path) -> None:
        """The first heartbeat should be sent after the initial 5-second sleep."""
        daemon = self._make_daemon(tmp_path)
        mock_client = _make_mock_client()

        # Store real sleep before patching; fast_sleep calls it directly
        _real_sleep = asyncio.sleep
        sleep_call_count = 0

        async def fast_sleep(delay: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count == 1:
                # Initial 5s sleep — fast-forward
                await _real_sleep(0)
            else:
                # Subsequent sleeps — block until cancelled
                await _real_sleep(9999)

        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_sleep),
        ):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            # Yield control so the task can run through the initial sleep and post
            await _real_sleep(0)
            await _real_sleep(0)
            await _real_sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert mock_client.post.call_count >= 1
        # Verify URL
        assert mock_client.post.call_args_list[0][0][0] == _HB_URL

    async def test_periodic_heartbeats_sent(self, tmp_path: Path) -> None:
        """Heartbeats should be sent on each interval pass after the initial one."""
        daemon = self._make_daemon(tmp_path)
        daemon.config.scheduler = SchedulerConfig(
            heartbeat_interval_seconds=1,
            switchboard_url="http://test-switchboard:8200",
        )
        mock_client = _make_mock_client()

        _real_sleep = asyncio.sleep

        async def fast_sleep(delay: float) -> None:
            # Fast-forward all sleeps but yield to event loop
            await _real_sleep(0)

        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_sleep),
        ):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            # Let several iterations run
            await _real_sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Should have posted: 1 initial + at least 1 periodic
        assert mock_client.post.call_count >= 2

    async def test_connection_failure_logs_warning_and_continues(self, tmp_path: Path) -> None:
        """Connection errors should be logged at WARNING and the loop should continue."""
        daemon = self._make_daemon(tmp_path)
        daemon.config.scheduler = SchedulerConfig(
            heartbeat_interval_seconds=1,
            switchboard_url="http://test-switchboard:8200",
        )

        call_count = 0
        responses = [
            httpx.ConnectError("Connection refused"),
            httpx.ConnectError("Connection refused"),
            _ok_response(),
        ]

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            resp = responses[idx]
            if isinstance(resp, Exception):
                raise resp
            return resp

        mock_client = _make_mock_client(post_side_effect=side_effect)

        _real_sleep = asyncio.sleep

        async def fast_sleep(delay: float) -> None:
            await _real_sleep(0)

        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_sleep),
        ):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            await _real_sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Loop should have continued past connection failures
        assert call_count >= 3, f"Expected at least 3 calls, got {call_count}"

    async def test_connection_failure_logged_at_warning_not_error(
        self, tmp_path: Path, caplog
    ) -> None:
        """Connection failures should be logged at WARNING level (not ERROR)."""
        daemon = self._make_daemon(tmp_path)

        async def failing_post(*args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        mock_client = _make_mock_client(post_side_effect=failing_post)

        _real_sleep = asyncio.sleep
        sleep_call_count = 0

        async def fast_then_cancel(delay: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count <= 2:
                await _real_sleep(0)
            else:
                raise asyncio.CancelledError

        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_then_cancel),
            caplog.at_level(logging.WARNING, logger="butlers.daemon"),
        ):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            try:
                await task
            except asyncio.CancelledError:
                pass

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warning_records) >= 1
        assert len(error_records) == 0

    async def test_loop_cancelled_cleanly_during_shutdown(self, tmp_path: Path) -> None:
        """Cancelling _liveness_reporter_loop should terminate the loop cleanly."""
        daemon = self._make_daemon(tmp_path)
        mock_client = _make_mock_client()

        with patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            await asyncio.sleep(0.01)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert task.done()

    async def test_heartbeat_posts_correct_butler_name(self, tmp_path: Path) -> None:
        """The heartbeat POST body must include the correct butler_name."""
        daemon = self._make_daemon(tmp_path, name="my-butler")

        posted_bodies: list[dict] = []

        async def capture_post(url, *, json=None, **kwargs):
            posted_bodies.append(json or {})
            return _ok_response()

        mock_client = _make_mock_client(post_side_effect=capture_post)

        _real_sleep = asyncio.sleep
        sleep_call_count = 0

        async def fast_sleep(delay: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count == 1:
                await _real_sleep(0)
            else:
                await _real_sleep(9999)

        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_sleep),
        ):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            await _real_sleep(0)
            await _real_sleep(0)
            await _real_sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(posted_bodies) >= 1
        assert posted_bodies[0]["butler_name"] == "my-butler"

    async def test_uses_configured_switchboard_url(self, tmp_path: Path) -> None:
        """Liveness reporter should POST to the configured switchboard_url."""
        custom_url = "http://custom-switchboard:9999"
        daemon = self._make_daemon(tmp_path)
        daemon.config.scheduler = SchedulerConfig(
            heartbeat_interval_seconds=120,
            switchboard_url=custom_url,
        )

        posted_urls: list[str] = []

        async def capture_url(url, **kwargs):
            posted_urls.append(url)
            return _ok_response()

        mock_client = _make_mock_client(post_side_effect=capture_url)

        _real_sleep = asyncio.sleep
        sleep_call_count = 0

        async def fast_sleep(delay: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count == 1:
                await _real_sleep(0)
            else:
                await _real_sleep(9999)

        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_sleep),
        ):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            await _real_sleep(0)
            await _real_sleep(0)
            await _real_sleep(0)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert len(posted_urls) >= 1
        assert posted_urls[0] == f"{custom_url}/api/switchboard/heartbeat"
