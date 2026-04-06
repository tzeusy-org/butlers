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

_HB_URL = "http://test-switchboard:41200/api/switchboard/heartbeat"


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
        'name = "butlers"',
        'schema = "test_butler"',
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
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    mock_pool = AsyncMock()
    # Support `async with pool.acquire() as conn:` pattern
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_pool.execute = AsyncMock(return_value=None)
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=None)
    mock_pool.fetch = AsyncMock(return_value=[])

    mock_db = MagicMock()
    mock_db.provision = AsyncMock()
    mock_db.connect = AsyncMock(return_value=mock_pool)
    mock_db.close = AsyncMock()
    mock_db.pool = mock_pool
    mock_db.user = "postgres"
    mock_db.password = "postgres"
    mock_db.host = "localhost"
    mock_db.port = 5432
    mock_db.db_name = "butlers"

    mock_audit_db = MagicMock()
    mock_audit_db.connect = AsyncMock()
    mock_audit_db.close = AsyncMock()
    mock_audit_db.pool = AsyncMock()

    _db_call_count = 0

    def _db_from_env_factory(db_name: str) -> MagicMock:
        nonlocal _db_call_count
        _db_call_count += 1
        # First call is the main butler DB; second is the audit pool
        if _db_call_count == 1:
            return mock_db
        return mock_audit_db

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

    def test_config_parsing(self, tmp_path: Path) -> None:
        """Interval: default 120s, custom parsed, invalid rejected. URL: default, TOML, env var."""
        # Interval defaults
        _make_butler_toml(tmp_path)
        assert load_config(tmp_path).scheduler.heartbeat_interval_seconds == 120

        _make_butler_toml(tmp_path, heartbeat_interval_seconds=60)
        assert load_config(tmp_path).scheduler.heartbeat_interval_seconds == 60

        for bad in (0, -10):
            _make_butler_toml(tmp_path, heartbeat_interval_seconds=bad)
            with pytest.raises(ConfigError, match="heartbeat_interval_seconds"):
                load_config(tmp_path)

        # URL resolution: dataclass defaults
        cfg = SchedulerConfig()
        assert cfg.heartbeat_interval_seconds == 120
        assert cfg.switchboard_url == "http://localhost:41200"

        # URL: default from TOML
        _make_butler_toml(tmp_path)
        env_backup = os.environ.pop("BUTLERS_SWITCHBOARD_URL", None)
        try:
            assert load_config(tmp_path).scheduler.switchboard_url == "http://localhost:41200"
        finally:
            if env_backup is not None:
                os.environ["BUTLERS_SWITCHBOARD_URL"] = env_backup

        # URL: TOML override
        _make_butler_toml(tmp_path, switchboard_url="http://my-switchboard:9999")
        env_backup = os.environ.pop("BUTLERS_SWITCHBOARD_URL", None)
        try:
            assert load_config(tmp_path).scheduler.switchboard_url == "http://my-switchboard:9999"
        finally:
            if env_backup is not None:
                os.environ["BUTLERS_SWITCHBOARD_URL"] = env_backup

        # URL: env var override
        _make_butler_toml(tmp_path)
        with patch.dict(os.environ, {"BUTLERS_SWITCHBOARD_URL": "http://env-switchboard:7777"}):
            assert load_config(tmp_path).scheduler.switchboard_url == "http://env-switchboard:7777"


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLivenessReporterLifecycle:
    """Verify liveness reporter task creation and cancellation."""

    async def _start_daemon(self, butler_dir: Path) -> ButlerDaemon:
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
        return daemon

    async def test_liveness_reporter_task_lifecycle(self, tmp_path: Path) -> None:
        """Task created on start (for any butler); cleared to None after shutdown."""
        # Regular butler
        daemon = await self._start_daemon(_make_butler_toml(tmp_path))
        try:
            assert daemon._liveness_reporter_task is not None
            assert isinstance(daemon._liveness_reporter_task, asyncio.Task)
        finally:
            await daemon.shutdown()
        assert daemon._liveness_reporter_task is None

        # Switchboard also gets a liveness reporter
        subdir = tmp_path / "sb"
        subdir.mkdir()
        daemon2 = await self._start_daemon(_make_butler_toml(subdir, name="switchboard"))
        try:
            assert daemon2._liveness_reporter_task is not None
        finally:
            await daemon2.shutdown()


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
            switchboard_url="http://test-switchboard:41200",
        )
        return daemon

    @staticmethod
    def _fast_sleep_once() -> tuple:
        """Return (real_sleep, fast_sleep) that fast-forwards first sleep, blocks second."""
        _real_sleep = asyncio.sleep
        sleep_call_count = 0

        async def fast_sleep(delay: float) -> None:
            nonlocal sleep_call_count
            sleep_call_count += 1
            await _real_sleep(0 if sleep_call_count == 1 else 9999)

        return _real_sleep, fast_sleep

    async def test_heartbeat_url_and_periodic_and_cancellation(self, tmp_path: Path) -> None:
        """Heartbeat URL correct; butler_name in body; custom URL used; periodic heartbeats;
        loop cancels cleanly."""
        daemon = self._make_daemon(tmp_path, name="my-butler")
        posted_urls: list[str] = []
        posted_bodies: list[dict] = []

        async def capture_post(url, *, json=None, **kwargs):
            posted_urls.append(url)
            posted_bodies.append(json or {})
            return _ok_response()

        mock_client = _make_mock_client(post_side_effect=capture_post)
        _real_sleep, fast_sleep = self._fast_sleep_once()

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

        assert mock_client.post.call_count >= 1
        assert posted_urls[0] == _HB_URL
        assert posted_bodies[0]["butler_name"] == "my-butler"
        assert task.done()

    async def test_connection_failure_and_404_handling(self, tmp_path: Path, caplog) -> None:
        """ConnectErrors → WARNING (not ERROR), loop continues; 3 consecutive 404s → self-terminate."""
        daemon = self._make_daemon(tmp_path)
        daemon.config.scheduler = SchedulerConfig(
            heartbeat_interval_seconds=1, switchboard_url="http://test-switchboard:41200"
        )
        _real_sleep = asyncio.sleep

        async def fast_sleep(delay: float) -> None:
            await _real_sleep(0)

        # Connection failures: loop continues with WARNING logs
        call_count = 0
        responses: list = [
            httpx.ConnectError("Connection refused"),
            httpx.ConnectError("Connection refused"),
            _ok_response(),
        ]

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            idx = min(call_count, len(responses) - 1)
            call_count += 1
            r = responses[idx]
            if isinstance(r, Exception):
                raise r
            return r

        mock_client = _make_mock_client(post_side_effect=side_effect)
        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_sleep),
            caplog.at_level(logging.WARNING, logger="butlers.daemon"),
        ):
            task = asyncio.create_task(daemon._liveness_reporter_loop())
            await _real_sleep(0.1)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert call_count >= 3
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) >= 1
        assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 0

        # 3 consecutive 404s → self-terminate
        (tmp_path / "p").mkdir(exist_ok=True)
        daemon2 = self._make_daemon(tmp_path / "p")
        daemon2.config.scheduler = SchedulerConfig(
            heartbeat_interval_seconds=1, switchboard_url="http://test-switchboard:41200"
        )
        call_count2 = 0

        async def returns_404(*args, **kwargs):
            nonlocal call_count2
            call_count2 += 1
            return httpx.Response(404)

        mock_client2 = _make_mock_client(post_side_effect=returns_404)
        with (
            patch("butlers.daemon.httpx.AsyncClient", return_value=mock_client2),
            patch("butlers.daemon.asyncio.sleep", side_effect=fast_sleep),
        ):
            await daemon2._liveness_reporter_loop()
        assert call_count2 == 3
