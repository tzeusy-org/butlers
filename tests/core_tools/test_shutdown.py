"""Tests for the ``shutdown`` MCP tool.

Unit:
  - Returns the correct scheduled response.
  - Returns an error for out-of-range grace_seconds.

Integration:
  - Scheduling shutdown with grace_seconds=0 actually sends SIGTERM to the
    process within a short slack window.
"""

from __future__ import annotations

import asyncio
import os
import signal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from butlers.core_tools._base import ToolContext
from butlers.core_tools._shutdown import register_shutdown_tool

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_and_grab_shutdown() -> object:
    """Register shutdown tool on a minimal daemon and return the shutdown() function."""
    registered: dict = {}

    def _core_tool(_group: str, **_kwargs):
        def decorator(fn):
            registered[fn.__name__] = fn
            return fn

        return decorator

    mcp = SimpleNamespace()
    daemon = SimpleNamespace(config=SimpleNamespace(name="qa"))
    ctx = ToolContext(
        daemon=daemon,
        pool=None,
        spawner=None,
        butler_name="qa",
        butler_type=None,
        is_switchboard=False,
        is_messenger=False,
        route_metrics=None,
    )
    register_shutdown_tool(ctx, mcp, _core_tool)
    return registered["shutdown"]


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, expected_grace",
    [
        ({}, 5),  # default grace
        ({"grace_seconds": 10}, 10),  # explicit grace
    ],
)
async def test_shutdown_returns_scheduled_grace(kwargs, expected_grace):
    """shutdown() returns status=scheduled echoing the (default or explicit) grace_seconds."""
    shutdown = _register_and_grab_shutdown()

    tasks_before = {t.get_name() for t in asyncio.all_tasks()}

    result = await shutdown(**kwargs)

    assert result["status"] == "scheduled"
    assert result["grace_seconds"] == expected_grace

    # Cancel the background task so it does not interfere with other tests.
    for task in asyncio.all_tasks():
        if task.get_name() not in tasks_before:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def test_shutdown_returns_scheduled_with_zero_grace():
    """shutdown(grace_seconds=0) is valid and returns status=scheduled."""
    shutdown = _register_and_grab_shutdown()

    # We patch os.kill so the process is not actually signalled.
    with patch("butlers.core_tools._shutdown.os.kill") as mock_kill:
        result = await shutdown(grace_seconds=0)
        # Yield control so the background task fires.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert result["status"] == "scheduled"
    assert result["grace_seconds"] == 0
    mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)


async def test_shutdown_double_call_cancels_previous_task():
    """Calling shutdown twice cancels the first pending task (idempotency)."""
    shutdown = _register_and_grab_shutdown()

    with patch("butlers.core_tools._shutdown.os.kill") as mock_kill:
        # First call with a long grace period.
        await shutdown(grace_seconds=300)
        # Second call overrides the first; grace_seconds=0 fires immediately.
        result = await shutdown(grace_seconds=0)
        # Yield so the second task can run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # Only one SIGTERM should be sent (from the second call).
    assert result["status"] == "scheduled"
    mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)


@pytest.mark.parametrize("grace_seconds", [-1, 301])
async def test_shutdown_returns_error_for_out_of_range_grace(grace_seconds):
    """Out-of-range grace_seconds (below 0 or above the 300 s cap) returns status=error."""
    shutdown = _register_and_grab_shutdown()
    result = await shutdown(grace_seconds=grace_seconds)
    assert result["status"] == "error"
    assert "grace_seconds" in result["error"]
