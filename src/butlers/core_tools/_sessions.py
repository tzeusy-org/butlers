"""Sessions core tools: sessions_list, sessions_get, sessions_summary, sessions_daily, top_sessions.

All session tools are only registered for non-STAFFER butlers.
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
    """Register sessions group tools (non-STAFFER only)."""
    pool = ctx.pool
    butler_type = ctx.butler_type

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
        async def top_sessions(limit: int = 10) -> dict:
            """Return the highest-token completed sessions."""
            return await _top_sessions(pool, limit)
