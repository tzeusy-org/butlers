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


async def test_shutdown_returns_scheduled_with_default_grace():
    """shutdown() returns status=scheduled with the default grace_seconds."""
    shutdown = _register_and_grab_shutdown()

    tasks_before = {t.get_name() for t in asyncio.all_tasks()}

    result = await shutdown()

    assert result["status"] == "scheduled"
    assert result["grace_seconds"] == 5

    # Cancel the background task so it does not interfere with other tests.
    for task in asyncio.all_tasks():
        if task.get_name() not in tasks_before:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def test_shutdown_returns_scheduled_with_explicit_grace():
    """shutdown(grace_seconds=10) returns status=scheduled with grace_seconds=10."""
    shutdown = _register_and_grab_shutdown()

    tasks_before = {t.get_name() for t in asyncio.all_tasks()}

    result = await shutdown(grace_seconds=10)

    assert result["status"] == "scheduled"
    assert result["grace_seconds"] == 10

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


async def test_shutdown_returns_error_for_negative_grace():
    """shutdown(-1) returns status=error."""
    shutdown = _register_and_grab_shutdown()
    result = await shutdown(grace_seconds=-1)
    assert result["status"] == "error"
    assert "grace_seconds" in result["error"]


async def test_shutdown_returns_error_for_grace_exceeding_max():
    """shutdown(301) returns status=error (exceeds 300 s cap)."""
    shutdown = _register_and_grab_shutdown()
    result = await shutdown(grace_seconds=301)
    assert result["status"] == "error"
    assert "grace_seconds" in result["error"]


# ---------------------------------------------------------------------------
# Integration test: SIGTERM is sent within grace + slack
# ---------------------------------------------------------------------------


async def test_shutdown_sends_sigterm_after_grace_period():
    """grace_seconds=0 causes SIGTERM within a short slack window."""
    shutdown = _register_and_grab_shutdown()

    signals_received: list[int] = []
    original_handler = signal.getsignal(signal.SIGTERM)

    def _capture(signum, frame):
        signals_received.append(signum)

    signal.signal(signal.SIGTERM, _capture)
    try:
        await shutdown(grace_seconds=0)
        # Allow the background task to run.
        await asyncio.sleep(0.05)
    finally:
        signal.signal(signal.SIGTERM, original_handler)

    assert signal.SIGTERM in signals_received, (
        "SIGTERM was not received within the grace period + slack"
    )
