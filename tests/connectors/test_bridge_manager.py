"""Condensed BridgeSubprocessManager tests — key lifecycle contracts.

Verifies:
- Exit code classification: clean exit / session-invalid / crash
- Backoff delay: bounded and increasing
- is_running state: True for alive process, False otherwise

[bu-35fm7]
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.bridge_manager import (
    BridgeConfig,
    BridgeSubprocessManager,
    _jittered_backoff,
)

pytestmark = pytest.mark.unit


def _make_config(**kwargs) -> BridgeConfig:
    defaults: dict = {
        "binary": "whatsapp-bridge",
        "bridge_socket": "/tmp/test-wa.sock",
        "health_poll_interval_s": 9999.0,
        "startup_timeout_s": 5.0,
    }
    defaults.update(kwargs)
    return BridgeConfig(**defaults)


def _fake_process(returncode: int | None = None, pid: int = 1234) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode

    async def _wait():
        return proc.returncode

    proc.wait = _wait
    proc.send_signal = MagicMock()
    proc.stdout = AsyncMock(spec=asyncio.StreamReader)
    proc.stderr = AsyncMock(spec=asyncio.StreamReader)
    proc.stdout.__aiter__ = lambda self: self
    proc.stdout.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
    proc.stderr.__aiter__ = lambda self: self
    proc.stderr.__anext__ = AsyncMock(side_effect=StopAsyncIteration)
    return proc


def _blocking_process(pid: int = 1234) -> MagicMock:
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = None

    async def _wait():
        await asyncio.Future()

    proc.wait = _wait
    proc.send_signal = MagicMock()
    return proc


def _eventual_exit_process(returncode: int, pid: int = 1234) -> tuple[MagicMock, asyncio.Event]:
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = None
    exit_event = asyncio.Event()

    async def _wait():
        await exit_event.wait()
        proc.returncode = returncode
        return returncode

    proc.wait = _wait
    proc.send_signal = MagicMock()
    proc.stdout = AsyncMock(spec=asyncio.StreamReader)
    proc.stderr = AsyncMock(spec=asyncio.StreamReader)
    proc.stdout.__aiter__.return_value = proc.stdout
    proc.stdout.__anext__.side_effect = StopAsyncIteration
    proc.stderr.__aiter__.return_value = proc.stderr
    proc.stderr.__anext__.side_effect = StopAsyncIteration
    return proc, exit_event


def test_backoff_attempt_zero_is_bounded() -> None:
    """Attempt 0 should be within ±25% of initial value (5s)."""
    delay = _jittered_backoff(0)
    assert 3.75 <= delay <= 6.25


def test_backoff_increases_with_attempt() -> None:
    """Multiple runs of higher attempt generally produce higher delays (on average)."""
    import statistics

    low_samples = [_jittered_backoff(0) for _ in range(20)]
    high_samples = [_jittered_backoff(5) for _ in range(20)]
    assert statistics.mean(high_samples) > statistics.mean(low_samples)


def test_backoff_capped_at_max() -> None:
    """Backoff must not exceed max (300s) * (1 + jitter_factor=0.25) at any attempt."""
    for attempt in (0, 5, 19, 50, 100, 1000):
        assert _jittered_backoff(attempt) <= 300.0 * 1.25 + 0.01


def test_exit_0_no_restart() -> None:
    """Exit code 0 = clean exit; _classify_exit must return False (no restart)."""
    mgr = BridgeSubprocessManager(_make_config())
    assert not mgr._classify_exit(0)


def test_exit_other_triggers_restart() -> None:
    """Non-zero, non-special exit codes indicate crash; _classify_exit must return True."""
    mgr = BridgeSubprocessManager(_make_config())
    assert mgr._classify_exit(99)


def test_not_running_when_no_process() -> None:
    """is_running must be False when no process has been started."""
    mgr = BridgeSubprocessManager(_make_config())
    assert not mgr.is_running


def test_is_running_with_alive_process() -> None:
    """is_running must be True when process returncode is None (still alive)."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._process = _fake_process(returncode=None)
    assert mgr.is_running


def test_not_running_with_exited_process() -> None:
    """is_running must be False when process has exited (returncode set)."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._process = _fake_process(returncode=0)
    assert not mgr.is_running


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [
        ("pair_required", "pair_required"),
        ("disconnected", "Bridge status: disconnected"),
    ],
)
async def test_start_succeeds_in_degraded_mode_for_terminal_startup_states(
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    expected_reason: str,
) -> None:
    mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=0.25, startup_allow_degraded=True))
    proc = _blocking_process()

    async def _spawn() -> None:
        mgr._process = proc

    monkeypatch.setattr(mgr, "_spawn", _spawn)
    monkeypatch.setattr(mgr, "_graceful_disconnect", AsyncMock())
    monkeypatch.setattr(mgr, "_STARTUP_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(return_value={"state": state}),
    )

    await asyncio.wait_for(mgr.start(), timeout=1.0)

    assert mgr.is_degraded
    assert mgr.degraded_reason == expected_reason
    assert not mgr._connected_event.is_set()
    assert mgr._startup_ready_event.is_set()

    await mgr.stop()


async def test_start_clears_degraded_if_bridge_recovers_before_health_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=0.25, startup_allow_degraded=True))
    proc = _blocking_process()

    async def _spawn() -> None:
        mgr._process = proc

    monkeypatch.setattr(mgr, "_spawn", _spawn)
    monkeypatch.setattr(mgr, "_graceful_disconnect", AsyncMock())
    monkeypatch.setattr(mgr, "_STARTUP_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(side_effect=[{"state": "pair_required"}, {"state": "connected"}]),
    )

    await asyncio.wait_for(mgr.start(), timeout=1.0)

    assert not mgr.is_degraded
    assert mgr.degraded_reason is None
    assert mgr._connected_event.is_set()
    assert mgr._startup_ready_event.is_set()

    await mgr.stop()


async def test_start_times_out_when_degraded_states_are_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=0.01))
    proc = _blocking_process()

    async def _spawn() -> None:
        mgr._process = proc

    monkeypatch.setattr(mgr, "_spawn", _spawn)
    monkeypatch.setattr(mgr, "_graceful_disconnect", AsyncMock())
    monkeypatch.setattr(mgr, "_STARTUP_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(return_value={"state": "pair_required"}),
    )

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(mgr.start(), timeout=1.0)

    await mgr.stop()


@pytest.mark.parametrize(
    ("returncode", "expected_reason"),
    [
        (1, "Pairing timeout — re-pair required"),
        (2, "Session invalidated — re-pair required"),
    ],
)
async def test_start_succeeds_when_bridge_exits_into_terminal_degraded_mode(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    expected_reason: str,
) -> None:
    mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=0.25, startup_allow_degraded=True))
    proc, exit_event = _eventual_exit_process(returncode)

    async def _spawn() -> None:
        mgr._process = proc

    monkeypatch.setattr(mgr, "_spawn", _spawn)
    monkeypatch.setattr(mgr, "_graceful_disconnect", AsyncMock())
    monkeypatch.setattr(mgr, "_STARTUP_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(side_effect=ConnectionError("bridge not ready")),
    )

    start_task = asyncio.create_task(mgr.start())
    await asyncio.sleep(0)
    exit_event.set()

    await asyncio.wait_for(start_task, timeout=1.0)

    assert mgr.is_degraded
    assert mgr.degraded_reason == expected_reason
    assert mgr._startup_ready_event.is_set()

    await mgr.stop()


# ---------------------------------------------------------------------------
# Degraded-duration tracking + live-liveness cross-check (stale-link watchdog)
# ---------------------------------------------------------------------------


def test_degraded_duration_none_when_healthy() -> None:
    """A freshly-created manager is not degraded and reports no duration."""
    mgr = BridgeSubprocessManager(_make_config())
    assert mgr.degraded_duration_s is None


def test_set_degraded_starts_duration_clock() -> None:
    """Entering degraded mode starts the duration clock."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._set_degraded("link down")
    assert mgr.is_degraded
    assert mgr.degraded_duration_s is not None
    assert mgr.degraded_duration_s >= 0.0


def test_repeated_set_degraded_does_not_reset_clock() -> None:
    """Repeated _set_degraded calls (e.g. every health poll) must not reset the clock."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._set_degraded("first")
    first_since = mgr._degraded_since
    mgr._set_degraded("second")
    assert mgr._degraded_since == first_since


def test_clear_degraded_resets_duration() -> None:
    """Recovery clears degraded state and the duration clock."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._set_degraded("link down")
    mgr._clear_degraded()
    assert not mgr.is_degraded
    assert mgr.degraded_reason is None
    assert mgr.degraded_duration_s is None


async def test_poll_status_degrades_when_link_dead_despite_connected_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """state=connected but live probe says link down → degraded (StreamReplaced guard)."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._process = _fake_process(returncode=None)
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(return_value={"state": "connected", "connected": False, "logged_in": True}),
    )
    await mgr._poll_status()
    assert mgr.is_degraded
    assert mgr.degraded_duration_s is not None


async def test_poll_status_healthy_when_connected_and_logged_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """state=connected with a live, logged-in probe is healthy and recovers from degraded."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._process = _fake_process(returncode=None)
    mgr._set_degraded("was down")
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(return_value={"state": "connected", "connected": True, "logged_in": True}),
    )
    await mgr._poll_status()
    assert not mgr.is_degraded
    assert mgr.degraded_duration_s is None
    assert mgr._connected_event.is_set()


async def test_poll_status_healthy_without_liveness_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An older bridge omitting connected/logged_in stays healthy on state=connected."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._process = _fake_process(returncode=None)
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(return_value={"state": "connected"}),
    )
    await mgr._poll_status()
    assert not mgr.is_degraded


def test_set_degraded_terminal_flag() -> None:
    """Terminal degraded states are flagged so the watchdog skips them."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._set_degraded("Session invalidated — re-pair required", terminal=True)
    assert mgr.is_degraded_terminal is True


def test_set_degraded_recoverable_is_not_terminal() -> None:
    """A recoverable degradation (e.g. session taken over) is not terminal."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._set_degraded("Link down despite state=connected (session taken over?)")
    assert mgr.is_degraded_terminal is False


def test_is_degraded_terminal_false_when_healthy() -> None:
    mgr = BridgeSubprocessManager(_make_config())
    assert mgr.is_degraded_terminal is False


def test_clear_degraded_resets_terminal_flag() -> None:
    mgr = BridgeSubprocessManager(_make_config())
    mgr._set_degraded("pair_required", terminal=True)
    mgr._clear_degraded()
    assert mgr.is_degraded_terminal is False


def test_recoverable_then_terminal_updates_flag_without_resetting_clock() -> None:
    """Escalating a recoverable outage to terminal flips the flag but keeps the clock."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._set_degraded("Bridge status: disconnected")
    since = mgr._degraded_since
    assert mgr.is_degraded_terminal is False
    mgr._set_degraded("pair_required", terminal=True)
    assert mgr.is_degraded_terminal is True
    assert mgr._degraded_since == since
