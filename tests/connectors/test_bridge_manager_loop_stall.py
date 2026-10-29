"""bu-e8l4j4: a stalled event loop must not make a ready bridge miss its startup deadline.

``start()`` arms the startup timer, then the poller gets its first turn. A loop stall longer than
``startup_timeout_s`` in between used to let the timer win against a bridge that was already in a
terminal startup state ("Bridge did not become startup-ready within 0s", seen as a merge-queue
flake). CPU contention alone does not reproduce it; starving the loop does, so the stall is a
real blocking call placed ahead of the poller's first turn.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.bridge_manager import BridgeConfig, BridgeSubprocessManager


def _block_event_loop(seconds: float) -> None:
    """Deliberately block the calling (event-loop) thread: the fault this test needs."""
    time.sleep(seconds)


@pytest.mark.parametrize(
    ("state", "expected_reason"),
    [("pair_required", "pair_required"), ("disconnected", "Bridge status: disconnected")],
)
async def test_start_reaches_a_terminal_state_despite_a_loop_stall_longer_than_the_timeout(
    monkeypatch: pytest.MonkeyPatch, state: str, expected_reason: str
) -> None:
    mgr = BridgeSubprocessManager(
        BridgeConfig(
            binary="whatsapp-bridge",
            bridge_socket="/tmp/test-wa.sock",
            health_poll_interval_s=9999.0,
            startup_timeout_s=0.25,
            startup_allow_degraded=True,
        )
    )
    proc = MagicMock(pid=1234, returncode=None)

    async def _wait() -> None:
        await asyncio.Future()

    proc.wait = _wait

    async def _spawn() -> None:
        mgr._process = proc

    async def _stalling_monitor() -> None:
        _block_event_loop(0.4)  # stalls between arming the timer and the poller's first turn

    monkeypatch.setattr(mgr, "_spawn", _spawn)
    monkeypatch.setattr(mgr, "_monitor_loop", _stalling_monitor)
    monkeypatch.setattr(mgr, "_graceful_disconnect", AsyncMock())
    monkeypatch.setattr(mgr, "_STARTUP_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        "butlers.connectors.bridge_manager._http_get_unix",
        AsyncMock(return_value={"state": state}),
    )

    await asyncio.wait_for(mgr.start(), timeout=5.0)

    assert mgr.is_degraded
    assert mgr.degraded_reason == expected_reason
    assert mgr._startup_ready_event.is_set()

    await mgr.stop()
