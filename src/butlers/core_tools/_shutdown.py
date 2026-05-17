"""Shutdown core tool: schedule a graceful butler daemon exit."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Callable
from typing import Any

from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)

# Minimum and maximum allowed grace period (seconds).
_GRACE_MIN = 0
_GRACE_MAX = 300


def register_shutdown_tool(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the ``shutdown`` tool in the ``infra`` group.

    The tool schedules a background coroutine that waits ``grace_seconds``
    and then sends ``SIGTERM`` to the current process.  The existing CLI
    signal handler converts SIGTERM into a clean ``daemon.shutdown()`` call,
    so all in-flight sessions drain, modules close, and the DB pool is
    released before the process exits.

    Returning immediately (before the grace period elapses) is intentional:
    the caller gets a confirmation response while the daemon keeps serving
    in-flight requests until the grace window closes.
    """
    butler_name = ctx.butler_name

    @_core_tool("infra")
    async def shutdown(grace_seconds: int = 5) -> dict:
        """Schedule a graceful shutdown of this butler daemon.

        Parameters
        ----------
        grace_seconds:
            Seconds to wait before sending SIGTERM.  Must be between 0 and
            300 (inclusive).  Defaults to 5.
        """
        if not (_GRACE_MIN <= grace_seconds <= _GRACE_MAX):
            return {
                "status": "error",
                "error": (
                    f"grace_seconds must be between {_GRACE_MIN} and {_GRACE_MAX}; "
                    f"got {grace_seconds}"
                ),
            }

        logger.info(
            "Shutdown scheduled for butler %s in %d second(s)",
            butler_name,
            grace_seconds,
        )

        async def _delayed_sigterm() -> None:
            if grace_seconds > 0:
                await asyncio.sleep(grace_seconds)
            logger.info(
                "Grace period elapsed for butler %s — sending SIGTERM",
                butler_name,
            )
            os.kill(os.getpid(), signal.SIGTERM)

        asyncio.create_task(_delayed_sigterm(), name=f"shutdown-{butler_name}")

        return {"status": "scheduled", "grace_seconds": grace_seconds}
