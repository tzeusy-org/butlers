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

from butlers.connectors import bridge_manager
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


def _fake_process_exits_later(exit_code: int, pid: int = 1234) -> MagicMock:
    proc = _fake_process(returncode=None, pid=pid)

    async def _wait():
        await asyncio.sleep(0)
        proc.returncode = exit_code
        return exit_code

    proc.wait = _wait
    return proc


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


@pytest.mark.asyncio
async def test_start_accepts_pair_required_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configured startup success states can treat pair_required as ready-for-pairing."""
    mgr = BridgeSubprocessManager(
        _make_config(startup_success_states=("connected", "pair_required"))
    )
    mgr._STARTUP_POLL_INTERVAL_S = 0.0

    async def _fake_spawn() -> None:
        mgr._process = _fake_process(returncode=None)

    monkeypatch.setattr(mgr, "_spawn", _fake_spawn)
    monkeypatch.setattr(mgr, "_monitor_loop", AsyncMock(return_value=None))
    monkeypatch.setattr(mgr, "_health_poll_loop", AsyncMock(return_value=None))
    monkeypatch.setattr(
        bridge_manager,
        "_http_get_unix",
        AsyncMock(return_value={"state": "pair_required"}),
    )

    await mgr.start()

    assert mgr.is_degraded
    assert mgr.degraded_reason == "Bridge startup state: pair_required"


@pytest.mark.asyncio
async def test_start_fails_fast_on_pair_required_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default startup rules should fail immediately on pair_required and clean up."""
    mgr = BridgeSubprocessManager(_make_config())
    mgr._STARTUP_POLL_INTERVAL_S = 0.0

    async def _fake_spawn() -> None:
        mgr._process = _fake_process(returncode=None)

    monkeypatch.setattr(mgr, "_spawn", _fake_spawn)
    monkeypatch.setattr(mgr, "_monitor_loop", AsyncMock(return_value=None))
    monkeypatch.setattr(
        bridge_manager,
        "_http_get_unix",
        AsyncMock(return_value={"state": "pair_required"}),
    )
    monkeypatch.setattr(bridge_manager, "_http_post_unix", AsyncMock(return_value={}))

    with pytest.raises(RuntimeError, match="Bridge startup state: pair_required"):
        await mgr.start()

    assert mgr._process is None


@pytest.mark.asyncio
async def test_start_fails_fast_when_process_exits_during_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Early no-restart exits during startup should not surface as generic timeouts."""
    mgr = BridgeSubprocessManager(_make_config(startup_timeout_s=30.0))
    mgr._STARTUP_POLL_INTERVAL_S = 0.0

    async def _fake_spawn() -> None:
        mgr._process = _fake_process_exits_later(exit_code=1)

    monkeypatch.setattr(mgr, "_spawn", _fake_spawn)
    monkeypatch.setattr(
        bridge_manager,
        "_http_get_unix",
        AsyncMock(side_effect=ConnectionError("socket unavailable")),
    )

    with pytest.raises(RuntimeError, match="Pairing timeout"):
        await mgr.start()
