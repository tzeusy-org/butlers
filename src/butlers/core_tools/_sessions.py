"""Sessions core tools: sessions_list, sessions_get, sessions_summary, sessions_daily,
top_sessions, cancel_session.

The query tools (sessions_list, etc.) are only registered for non-STAFFER
butlers. cancel_session is always registered regardless of butler type or
core_groups -- see its docstring below.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from butlers.config import ButlerType
from butlers.core.sessions import sessions_daily as _sessions_daily
from butlers.core.sessions import sessions_get as _sessions_get
from butlers.core.sessions import sessions_list as _sessions_list
from butlers.core.sessions import sessions_summary as _sessions_summary
from butlers.core.sessions import top_sessions as _top_sessions
from butlers.core_tools._base import ToolContext


def register_session_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register sessions group tools (non-STAFFER only) plus cancel_session (always)."""
    pool = ctx.pool
    butler_type = ctx.butler_type
    spawner = ctx.spawner

    # cancel_session is ALWAYS registered regardless of core_groups or butler
    # type -- like route.execute, this is an infrastructure endpoint the
    # dashboard API calls server-to-server (never an LLM-facing tool), so it
    # must survive core_groups pruning. It implements the chat "Stop" button
    # (bu-ep4ks.2): killing the actual runtime subprocess, not just detaching
    # the client's SSE watch.
    @mcp.tool(name="cancel_session")
    async def cancel_session(session_id: str) -> dict:
        """Cancel an in-flight session's runtime invocation, if still running.

        Kills the underlying CLI subprocess via the spawner's cancellation
        handling -- a real terminate, not a client-side stream detach.
        Cancelling a session that already completed (or was never in flight
        on this daemon) is a benign no-op: ``cancelled`` is ``False`` so the
        caller never renders a false "stopped" confirmation for something
        that simply finished on its own.
        """
        cancelled = spawner.cancel_session(session_id)
        return {"cancelled": cancelled, "session_id": session_id}

    if butler_type != ButlerType.STAFFER:

        @_core_tool("sessions")
        async def sessions_list(limit: int = 20, offset: int = 0) -> list[dict]:
            """List sessions ordered by most recent first."""
            sessions = await _sessions_list(pool, limit, offset)
            for s in sessions:
                s["id"] = str(s["id"])
            return sessions

        @_core_tool("sessions")
        async def sessions_get(session_id: str) -> dict | None:
            """Get a session by ID."""
            session = await _sessions_get(pool, uuid.UUID(session_id))
            if session:
                session["id"] = str(session["id"])
            return session

        @_core_tool("sessions")
        async def sessions_summary(period: str = "today") -> dict:
            """Return aggregate session/token stats for a period."""
            return await _sessions_summary(pool, period)

        @_core_tool("sessions")
        async def sessions_daily(from_date: str, to_date: str) -> dict:
            """Return daily session/token aggregates for a date range."""
            return await _sessions_daily(pool, from_date, to_date)

        @_core_tool("sessions")
        async def top_sessions(
            limit: int = 10,
            from_date: str | None = None,
            to_date: str | None = None,
        ) -> dict:
            """Return the highest-token completed sessions.

            When ``from_date``/``to_date`` (ISO date strings) are both provided,
            results are scoped to that inclusive date range. Omit both for
            all-time results.
            """
            return await _top_sessions(pool, limit, from_date, to_date)
