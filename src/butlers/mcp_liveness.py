"""An MCP client whose calls end when the server stops answering (bu-dhloi1.147).

A raw ``fastmcp`` client waits for a reply forever. After ``kill -9`` of the server, or with it
frozen, a call in flight hung past 330s and 620s when measured; cancelling freed it at once. A
blanket deadline does not fit, because calls such as ``route`` wait on real work.

``LivenessClient`` pings the session while a call is in flight. A server doing slow work still
answers pings, so a long call runs to the end, and a server that is dead or frozen misses
``max_missed_pings`` in a row, which ends the call with :class:`ServerUnresponsive`. When the
server was killed, the ping's own failure usually ends the call first, with the transport's
error, which callers already treat as a dead connection.

Whether the call took effect before the server went away is unknown: treat
:class:`ServerUnresponsive` like any other mid-call transport failure.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
from collections.abc import Awaitable
from typing import Any, TypeVar

import mcp.types
from fastmcp import Client
from fastmcp.client.progress import ProgressHandler

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

#: Seconds between pings while a call is in flight.
DEFAULT_PING_INTERVAL_S = 10.0
#: Seconds a ping gets before it counts as missed.
DEFAULT_PING_TIMEOUT_S = 5.0
#: Consecutive missed pings that declare the server dead. With the defaults a frozen server is
#: detected after about 35s: 3 pings, 10s apart, each given 5s.
DEFAULT_MAX_MISSED_PINGS = 3
#: Seconds ``Client.__aenter__`` (the MCP ``initialize`` handshake) gets. fastmcp's own default is
#: no deadline, so a first connect, or a reconnect, to a frozen server would hang (bu-dhloi1.246).
DEFAULT_INIT_TIMEOUT_S = 30.0


class ServerUnresponsive(ConnectionError):  # noqa: N818
    """The server stopped answering pings while a call was in flight; the call was cancelled.

    The server may have run the call, or may run it later. A ``ConnectionError`` so callers
    that already treat transport failures as a dead peer handle it.
    """


class LivenessClient(Client):
    """A ``fastmcp.Client`` whose in-flight calls end when the server stops answering pings."""

    def __init__(
        self,
        *args: Any,
        ping_interval_s: float = DEFAULT_PING_INTERVAL_S,
        ping_timeout_s: float = DEFAULT_PING_TIMEOUT_S,
        max_missed_pings: int = DEFAULT_MAX_MISSED_PINGS,
        init_timeout: float | None = DEFAULT_INIT_TIMEOUT_S,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, init_timeout=init_timeout, **kwargs)
        self._ping_interval_s = ping_interval_s
        self._ping_timeout_s = ping_timeout_s
        self._max_missed_pings = max_missed_pings

    async def call_tool_mcp(
        self,
        name: str,
        arguments: dict[str, Any],
        progress_handler: ProgressHandler | None = None,
        timeout: datetime.timedelta | float | int | None = None,  # noqa: ASYNC109
        meta: dict[str, Any] | None = None,
    ) -> mcp.types.CallToolResult:
        # ``Client.call_tool`` goes through this method, so watching here covers both.
        return await self._while_alive(
            super().call_tool_mcp(name, arguments, progress_handler, timeout, meta),
            "call_tool",
        )

    async def _watch(self) -> None:
        """Return only once the server has missed too many pings in a row."""
        missed = 0
        while True:
            await asyncio.sleep(self._ping_interval_s)
            try:
                await asyncio.wait_for(self.ping(), timeout=self._ping_timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception:
                missed += 1
                if missed >= self._max_missed_pings:
                    return
            else:
                missed = 0

    async def _while_alive(self, call: Awaitable[_T], what: str) -> _T:
        call_task = asyncio.ensure_future(call)
        watch_task = asyncio.ensure_future(self._watch())
        watch_task.set_name("mcp-liveness-watch")
        try:
            await asyncio.wait({call_task, watch_task}, return_when=asyncio.FIRST_COMPLETED)
            if call_task.done():
                return call_task.result()
            logger.warning(
                "MCP server stopped answering pings (%d missed); cancelling %s",
                self._max_missed_pings,
                what,
            )
            call_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await call_task
            raise ServerUnresponsive(
                f"MCP server missed {self._max_missed_pings} pings in a row during {what}; "
                "outcome unknown, it may have taken effect"
            )
        finally:
            for task in (call_task, watch_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(call_task, watch_task, return_exceptions=True)
