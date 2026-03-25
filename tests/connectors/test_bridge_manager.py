"""Unit tests for BridgeSubprocessManager.

Tests cover:
- _jittered_backoff: backoff delay computation and jitter
- exit-code classification: clean / pair-timeout / session-invalid / crash
- lifecycle: start, running, stop (graceful and SIGTERM fallback)
- health poll: state transitions (connected → degraded → recovered)
- restart loop: unexpected-exit triggers restart; no-restart codes skip it
- binary-not-found: RuntimeError with correct message
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.bridge_manager import (
    BridgeConfig,
    BridgeSubprocessManager,
    _jittered_backoff,
    _parse_http_json,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> BridgeConfig:
    defaults: dict = {
        "binary": "whatsapp-bridge",
        "bridge_socket": "/tmp/test-wa.sock",
        "health_poll_interval_s": 9999.0,  # disable auto polling in most tests
        "startup_timeout_s": 5.0,
    }
    defaults.update(kwargs)
    return BridgeConfig(**defaults)


def _fake_process(returncode: int | None = None, pid: int = 1234) -> MagicMock:
    """Return a mock asyncio.Process."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode

    # wait() returns the exit code; we make it a coroutine
    async def _wait():
        return proc.returncode

    proc.wait = _wait
    proc.send_signal = MagicMock()
    proc.stdout = AsyncMock(spec=asyncio.StreamReader)
    proc.stderr = AsyncMock(spec=asyncio.StreamReader)

    # Make stdout/stderr async-iterable (no lines)
    proc.stdout.__aiter__ = lambda self: self
    proc.stdout.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
    proc.stderr.__aiter__ = lambda self: self
    proc.stderr.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
    return proc


# ---------------------------------------------------------------------------
# _jittered_backoff
# ---------------------------------------------------------------------------


class TestJitteredBackoff:
    def test_attempt_0_is_near_initial(self):
        """Attempt 0 should be within ±25% of 5 s."""
        delay = _jittered_backoff(0)
        assert 3.75 <= delay <= 6.25, f"Unexpected delay: {delay}"

    def test_increases_with_attempt(self):
        """Delay should grow with each attempt."""
        # Use average over many samples to reduce jitter noise
        avg_0 = sum(_jittered_backoff(0) for _ in range(50)) / 50
        avg_3 = sum(_jittered_backoff(3) for _ in range(50)) / 50
        assert avg_3 > avg_0

    def test_capped_at_max(self):
        """Very high attempt numbers must not exceed 300 s (+25%)."""
        for attempt in (50, 100, 1000):
            delay = _jittered_backoff(attempt)
            assert delay <= 300.0 * 1.25 + 0.01, f"delay {delay} exceeds max at attempt {attempt}"

    def test_never_negative(self):
        """Delay is always positive."""
        for attempt in range(20):
            assert _jittered_backoff(attempt) > 0


# ---------------------------------------------------------------------------
# _parse_http_json
# ---------------------------------------------------------------------------


class TestParseHttpJson:
    def test_basic_response(self):
        raw = b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"state": "connected"}'
        assert _parse_http_json(raw) == {"state": "connected"}

    def test_missing_separator_raises(self):
        with pytest.raises(ValueError, match="Malformed HTTP"):
            _parse_http_json(b"no separator here")

    def test_invalid_json_raises(self):
        with pytest.raises(ValueError, match="non-JSON"):
            _parse_http_json(b"HTTP/1.0 200 OK\r\n\r\nnot-json")


# ---------------------------------------------------------------------------
# Exit-code classification (_classify_exit)
# ---------------------------------------------------------------------------


class TestClassifyExit:
    def _mgr(self) -> BridgeSubprocessManager:
        return BridgeSubprocessManager(_make_config())

    def test_exit_0_no_restart(self):
        mgr = self._mgr()
        assert mgr._classify_exit(0) is False
        assert not mgr.is_degraded

    def test_exit_1_pair_timeout(self):
        mgr = self._mgr()
        assert mgr._classify_exit(1) is False
        assert mgr.is_degraded
        assert "pair" in (mgr.degraded_reason or "").lower()

    def test_exit_2_session_invalid(self):
        mgr = self._mgr()
        assert mgr._classify_exit(2) is False
        assert mgr.is_degraded
        assert "session" in (mgr.degraded_reason or "").lower()

    def test_exit_other_triggers_restart(self):
        mgr = self._mgr()
        assert mgr._classify_exit(3) is True
        assert mgr._classify_exit(137) is True
        assert mgr._classify_exit(-11) is True

    def test_exit_other_not_degraded(self):
        mgr = self._mgr()
        mgr._classify_exit(42)
        assert not mgr.is_degraded


# ---------------------------------------------------------------------------
# is_running property
# ---------------------------------------------------------------------------


class TestIsRunning:
    def test_no_process_not_running(self):
        mgr = BridgeSubprocessManager(_make_config())
        assert not mgr.is_running

    def test_process_alive_is_running(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=None)
        assert mgr.is_running

    def test_process_exited_not_running(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=0)
        assert not mgr.is_running


# ---------------------------------------------------------------------------
# Binary-not-found
# ---------------------------------------------------------------------------


class TestBinaryNotFound:
    async def test_start_raises_runtime_error_when_missing(self):
        mgr = BridgeSubprocessManager(_make_config(binary="nonexistent-binary-xyz"))
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="whatsapp-bridge binary not found"):
                await mgr._spawn()


# ---------------------------------------------------------------------------
# Lifecycle: start + stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def _make_running_mgr(self) -> BridgeSubprocessManager:
        """Create a manager with a mocked subprocess that's already 'connected'."""
        mgr = BridgeSubprocessManager(
            _make_config(startup_timeout_s=2.0, health_poll_interval_s=9999.0)
        )
        proc = _fake_process(returncode=None)

        async def _fake_create(*args, **kwargs):
            return proc

        with (
            patch("shutil.which", return_value="/usr/bin/whatsapp-bridge"),
            patch("asyncio.create_subprocess_exec", side_effect=_fake_create),
        ):
            # Pre-set the connected event so start() doesn't block
            mgr._connected_event.set()
            await mgr.start()

        return mgr

    async def test_start_sets_is_running(self):
        mgr = await self._make_running_mgr()
        try:
            assert mgr._process is not None, "start() must create a subprocess"
        finally:
            await mgr.stop()

    async def test_stop_sets_stopping(self):
        mgr = await self._make_running_mgr()
        await mgr.stop()
        assert mgr._stopping is True

    async def test_stop_idempotent(self):
        """Calling stop() twice must not raise."""
        mgr = await self._make_running_mgr()
        await mgr.stop()
        await mgr.stop()  # second call must be a no-op

    async def test_start_clears_degraded(self):
        mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=1.0))
        mgr._degraded = True
        mgr._degraded_reason = "old reason"

        proc = _fake_process(returncode=None)

        async def _fake_create(*args, **kwargs):
            return proc

        with (
            patch("shutil.which", return_value="/usr/bin/whatsapp-bridge"),
            patch("asyncio.create_subprocess_exec", side_effect=_fake_create),
        ):
            mgr._connected_event.set()
            await mgr.start()

        assert not mgr.is_degraded
        assert mgr.degraded_reason is None
        await mgr.stop()

    async def test_start_polls_status_until_connected(self):
        """start() must poll /status via _startup_poll_loop until bridge reports connected."""
        mgr = BridgeSubprocessManager(
            _make_config(startup_timeout_s=5.0, health_poll_interval_s=9999.0)
        )
        proc = _fake_process(returncode=None)
        poll_calls: list[int] = []

        async def _fake_create(*args, **kwargs):
            return proc

        async def _fake_get_unix(socket_path: str, path: str):
            poll_calls.append(len(poll_calls) + 1)
            if len(poll_calls) >= 2:
                # Report connected on the second poll so startup completes
                return {"state": "connected"}
            # First poll returns 'connecting' — should NOT enter degraded mode
            return {"state": "connecting"}

        with (
            patch("shutil.which", return_value="/usr/bin/whatsapp-bridge"),
            patch("asyncio.create_subprocess_exec", side_effect=_fake_create),
            patch(
                "butlers.connectors.bridge_manager._http_get_unix",
                side_effect=_fake_get_unix,
            ),
            patch(
                "butlers.connectors.bridge_manager.BridgeSubprocessManager._STARTUP_POLL_INTERVAL_S",
                new=0.01,
            ),
        ):
            await mgr.start()

        # At least two polls were issued (connecting → connected)
        assert len(poll_calls) >= 2, f"Expected >=2 startup polls, got {len(poll_calls)}"
        # 'connecting' during startup must not mark the bridge degraded
        assert not mgr.is_degraded, "connecting during startup should not set degraded"
        await mgr.stop()


# ---------------------------------------------------------------------------
# Graceful shutdown: POST /disconnect + SIGTERM fallback
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    async def test_disconnect_called_before_wait(self):
        mgr = BridgeSubprocessManager(_make_config())
        proc = _fake_process(returncode=None)

        # Make wait() settle immediately after disconnect
        calls: list[str] = []

        async def _post(*args, **kwargs):
            calls.append("disconnect")
            proc.returncode = 0
            return {"ok": True}

        async def _wait():
            return proc.returncode if proc.returncode is not None else 0

        proc.wait = _wait

        mgr._process = proc
        with patch(
            "butlers.connectors.bridge_manager._http_post_unix",
            side_effect=_post,
        ):
            await mgr._graceful_disconnect()

        assert "disconnect" in calls

    async def test_sigterm_sent_when_process_hangs(self):
        mgr = BridgeSubprocessManager(_make_config())
        proc = _fake_process(returncode=None)
        proc.pid = 5678

        # wait() never resolves — simulates a hanging process
        async def _hanging_wait():
            await asyncio.sleep(9999)

        proc.wait = _hanging_wait

        # SIGTERM causes immediate exit
        async def _wait_after_sigterm():
            return 0

        def _send_signal(sig):
            proc.wait = _wait_after_sigterm

        proc.send_signal = _send_signal
        mgr._process = proc

        async def _fail_post(*args, **kwargs):
            raise ConnectionRefusedError("socket closed")

        with patch(
            "butlers.connectors.bridge_manager._http_post_unix",
            side_effect=_fail_post,
        ):
            # Should complete without raising even though wait() hangs
            await asyncio.wait_for(mgr._graceful_disconnect(), timeout=20.0)

        # Signal was sent (side effect replaced wait)
        assert proc.wait is _wait_after_sigterm


# ---------------------------------------------------------------------------
# Health polling
# ---------------------------------------------------------------------------


class TestHealthPoll:
    async def test_connected_state_sets_event(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=None)

        with patch(
            "butlers.connectors.bridge_manager._http_get_unix",
            return_value={"state": "connected"},
        ):
            await mgr._poll_status()

        assert mgr._connected_event.is_set()
        assert not mgr.is_degraded

    async def test_disconnected_state_sets_degraded(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=None)
        mgr._connected_event.set()  # was connected

        with patch(
            "butlers.connectors.bridge_manager._http_get_unix",
            return_value={"state": "disconnected"},
        ):
            await mgr._poll_status()

        assert mgr.is_degraded
        assert "disconnected" in (mgr.degraded_reason or "")

    async def test_pair_required_state_sets_degraded(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=None)

        with patch(
            "butlers.connectors.bridge_manager._http_get_unix",
            return_value={"state": "pair_required"},
        ):
            await mgr._poll_status()

        assert mgr.is_degraded
        assert "pair" in (mgr.degraded_reason or "").lower()

    async def test_poll_timeout_sets_degraded(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=None)

        async def _timeout(*args, **kwargs):
            raise TimeoutError

        with patch(
            "butlers.connectors.bridge_manager._http_get_unix",
            side_effect=_timeout,
        ):
            await mgr._poll_status()

        assert mgr.is_degraded
        assert "timed out" in (mgr.degraded_reason or "").lower()

    async def test_poll_exception_sets_degraded(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=None)

        with patch(
            "butlers.connectors.bridge_manager._http_get_unix",
            side_effect=ConnectionRefusedError("socket closed"),
        ):
            await mgr._poll_status()

        assert mgr.is_degraded

    async def test_recovery_clears_degraded(self):
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=None)
        mgr._degraded = True
        mgr._degraded_reason = "was broken"

        with patch(
            "butlers.connectors.bridge_manager._http_get_unix",
            return_value={"state": "connected"},
        ):
            await mgr._poll_status()

        assert not mgr.is_degraded
        assert mgr.degraded_reason is None

    async def test_no_poll_when_process_not_running(self):
        """_poll_status is a no-op if the process is not alive."""
        mgr = BridgeSubprocessManager(_make_config())
        mgr._process = _fake_process(returncode=0)  # already exited

        with patch(
            "butlers.connectors.bridge_manager._http_get_unix",
            side_effect=AssertionError("should not be called"),
        ):
            await mgr._poll_status()  # must not call _http_get_unix


# ---------------------------------------------------------------------------
# Restart loop
# ---------------------------------------------------------------------------


class TestRestartLoop:
    async def test_crash_exit_triggers_restart(self):
        """An exit code != 0/1/2 should trigger a restart attempt."""
        mgr = BridgeSubprocessManager(
            _make_config(startup_timeout_s=1.0, health_poll_interval_s=9999.0)
        )

        second_spawn_event = asyncio.Event()
        restart_count = 0

        async def _counting_spawn():
            nonlocal restart_count
            restart_count += 1
            # Each spawned process blocks in wait() until _stopping is set
            proc = MagicMock()
            proc.pid = 9999
            proc.returncode = None
            proc.stdout = AsyncMock(spec=asyncio.StreamReader)
            proc.stderr = AsyncMock(spec=asyncio.StreamReader)
            proc.stdout.__aiter__ = lambda self: self
            proc.stdout.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
            proc.stderr.__aiter__ = lambda self: self
            proc.stderr.__anext__ = AsyncMock(side_effect=StopAsyncIteration)

            async def _blocking_wait():
                while not mgr._stopping:
                    await asyncio.sleep(0)
                proc.returncode = 0
                return 0

            proc.wait = _blocking_wait
            mgr._process = proc
            mgr._connected_event.set()
            if restart_count >= 2:
                second_spawn_event.set()

        mgr._spawn = _counting_spawn

        # First spawn (count=1); override wait() to immediately return 137
        await mgr._spawn()
        proc = mgr._process
        assert proc is not None

        async def _crash_wait():
            return 137

        proc.wait = _crash_wait
        proc.returncode = 137

        _real_sleep = asyncio.sleep

        async def _fast_sleep(delay: float) -> None:
            await _real_sleep(0)

        with patch("butlers.connectors.bridge_manager.asyncio.sleep", side_effect=_fast_sleep):
            monitor = asyncio.create_task(mgr._monitor_loop())
            # Wait until second spawn is triggered (= first restart)
            await asyncio.wait_for(second_spawn_event.wait(), timeout=5.0)
            mgr._stopping = True
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

        assert restart_count >= 2, f"Expected at least one restart, got {restart_count}"

    async def test_clean_exit_no_restart(self):
        """Exit code 0 must not trigger a restart."""
        mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=1.0))

        spawn_calls = 0

        async def _counting_spawn():
            nonlocal spawn_calls
            spawn_calls += 1
            proc = _fake_process(returncode=None)
            mgr._process = proc
            mgr._connected_event.set()

        mgr._spawn = _counting_spawn
        await mgr._spawn()  # initial spawn

        mgr._process.returncode = 0

        monitor = asyncio.create_task(mgr._monitor_loop())
        await asyncio.sleep(0.05)
        mgr._stopping = True
        monitor.cancel()
        try:
            await monitor
        except asyncio.CancelledError:
            pass

        assert spawn_calls == 1, "Clean exit must not trigger restart"

    async def test_no_restart_codes_set_degraded(self):
        """Exit codes 1 and 2 must mark the manager degraded."""
        for code in (1, 2):
            mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=1.0))
            proc = _fake_process(returncode=None)
            mgr._process = proc
            proc.returncode = code

            monitor = asyncio.create_task(mgr._monitor_loop())
            await asyncio.sleep(0.05)
            mgr._stopping = True
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass

            assert mgr.is_degraded, f"rc={code} should set degraded"
