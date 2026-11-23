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

# The docstring records the original butlers-976.3 scope. Current receiver-mode
# lifecycle is covered below by REQ-butler-control-plane-liveness-006.

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
from butlers.testing.daemon import write_butler_config

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


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_real_boot_starts_the_reporter_and_shutdown_clears_it(
    booted_daemon, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In legacy mode the real startup launches the reporter; the real shutdown clears it."""
    monkeypatch.delenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", raising=False)
    booted = await booted_daemon(write_butler_config(tmp_path, "general"))
    daemon = booted.daemon
    assert isinstance(daemon._liveness_reporter_task, asyncio.Task)

    await booted.shutdown()

    assert daemon._liveness_reporter_task is None


async def _run_readiness(
    butler_dir: Path, *, registered: bool
) -> tuple[ButlerDaemon, AsyncMock, AsyncMock]:
    """Run the daemon's own readiness phase on a DB-free host.

    The reporter loop and the epoch registration are the phase's collaborators, so
    they are stand-ins on this daemon; the phase's choices are the subject
    (bu-rvgzmf.2).
    """
    import butlers.lifecycle as lifecycle
    from butlers.testing.tool_host import host_tools

    daemon = (await host_tools(butler_dir, pool=AsyncMock())).daemon
    reporter = AsyncMock()
    registration = AsyncMock(return_value=registered)
    daemon._liveness_reporter_loop = reporter
    daemon._register_boot_epoch = registration
    daemon._retry_boot_registration = AsyncMock()
    state = lifecycle.StartupState()
    state.pool = daemon.db.pool
    await lifecycle._startup_readiness(daemon, state)
    await asyncio.sleep(0)
    return daemon, reporter, registration


async def _cancel(*tasks: asyncio.Task | None) -> None:
    for task in tasks:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class TestLivenessReporterReadiness:
    """The readiness phase picks the liveness writer by mode, for any butler."""

    # Spec: REQ-butler-control-plane-liveness-006
    async def test_receiver_mode_retires_legacy_writer_without_changing_boot_admission(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        flag = "BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER"
        # Only the exact routing cutover value selects receiver authority.
        # A planted reporter must actually execute in each retained legacy mode,
        # for an ordinary butler and the Switchboard alike.
        for index, (value, name) in enumerate(
            (("0", "test-butler"), ("true", "test-butler"), ("", "switchboard"))
        ):
            monkeypatch.setenv(flag, value)
            legacy_dir = tmp_path / f"legacy-{index}"
            legacy_dir.mkdir()
            legacy, reporter, _ = await _run_readiness(
                _make_butler_toml(legacy_dir, name=name), registered=True
            )
            assert isinstance(legacy._liveness_reporter_task, asyncio.Task)
            reporter.assert_awaited_once()
            await _cancel(legacy._liveness_reporter_task)

        # The obsolete writer is absent for both kinds of daemon in receiver mode.
        # Successful and failed boot admission still determine readiness.
        monkeypatch.setenv(flag, "1")
        for index, (name, registered) in enumerate(
            (("test-butler", True), ("switchboard", True), ("test-butler", False))
        ):
            receiver_dir = tmp_path / f"receiver-{index}"
            receiver_dir.mkdir()
            receiver, reporter, registration = await _run_readiness(
                _make_butler_toml(receiver_dir, name=name), registered=registered
            )
            assert receiver._liveness_reporter_task is None
            registration.assert_awaited_once()
            assert receiver._accepting_connections is registered
            if registered:
                assert receiver._boot_registration_task is None
            else:
                assert isinstance(receiver._boot_registration_task, asyncio.Task)
            reporter.assert_not_awaited()
            await _cancel(receiver._boot_registration_task)


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
