"""ASGI middleware guards for the Butler MCP server.

Provides:
- _McpSseDisconnectGuard: Suppresses expected SSE POST disconnects.
- _McpRuntimeSessionGuard: Binds runtime session IDs from MCP query params.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qs

from starlette.requests import ClientDisconnect

from butlers.core.tool_call_capture import (
    reset_current_runtime_butler_name,
    reset_current_runtime_session_id,
    reset_current_runtime_trigger_source,
    set_current_runtime_butler_name,
    set_current_runtime_session_id,
    set_current_runtime_trigger_source,
)

logger = logging.getLogger(__name__)


class _McpSseDisconnectGuard:
    """Catch expected SSE POST disconnects before they become error traces."""

    def __init__(self, app: Any, *, butler_name: str) -> None:
        self._app = app
        self._butler_name = butler_name

    @staticmethod
    def _is_messages_post(scope: dict[str, Any]) -> bool:
        if scope.get("type") != "http":
            return False
        if str(scope.get("method", "")).upper() != "POST":
            return False
        path = str(scope.get("path", "")).rstrip("/")
        return path == "/messages"

    @staticmethod
    def _session_id(scope: dict[str, Any]) -> str | None:
        query_string = scope.get("query_string")
        if not isinstance(query_string, (bytes, bytearray)):
            return None

        parsed = parse_qs(query_string.decode("utf-8", errors="replace"))
        values = parsed.get("session_id")
        if not values:
            return None

        session_id = values[0].strip()
        return session_id or None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        try:
            await self._app(scope, receive, send)
        except ClientDisconnect:
            if not self._is_messages_post(scope):
                raise

            path = str(scope.get("path", ""))
            session_id = self._session_id(scope) or "unknown"
            logger.debug(
                "Suppressed expected MCP SSE POST disconnect (butler=%s path=%s session_id=%s)",
                self._butler_name,
                path,
                session_id,
            )

            try:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 202,
                        "headers": [(b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
            except Exception:
                logger.debug("MCP SSE disconnect response not sent; client already disconnected")


class _McpRuntimeSessionGuard:
    """Bind runtime session IDs from MCP query params into request context."""

    _MAX_SESSION_MAP_SIZE = 4096

    def __init__(self, app: Any, *, butler_name: str) -> None:
        self._app = app
        self._butler_name = butler_name
        self._mcp_session_to_runtime_session: dict[str, str] = {}

    def __getattr__(self, name: str) -> Any:
        """Proxy unknown attributes to wrapped ASGI app for compatibility."""
        return getattr(self._app, name)

    def _resolve_session_params(self, scope: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract runtime_session_id and trigger_source from query params."""
        query_string = scope.get("query_string")
        if not isinstance(query_string, (bytes, bytearray)):
            return None, None

        parsed = parse_qs(query_string.decode("utf-8", errors="replace"))
        runtime_values = parsed.get("runtime_session_id")
        runtime_session_id = runtime_values[0].strip() if runtime_values else None
        mcp_values = parsed.get("session_id")
        mcp_session_id = mcp_values[0].strip() if mcp_values else None

        trigger_values = parsed.get("trigger_source")
        trigger_source = trigger_values[0].strip() if trigger_values else None

        if runtime_session_id and mcp_session_id:
            self._mcp_session_to_runtime_session[mcp_session_id] = runtime_session_id
            if len(self._mcp_session_to_runtime_session) > self._MAX_SESSION_MAP_SIZE:
                oldest = next(iter(self._mcp_session_to_runtime_session))
                self._mcp_session_to_runtime_session.pop(oldest, None)

        resolved_session_id = runtime_session_id
        if not resolved_session_id and mcp_session_id:
            resolved_session_id = self._mcp_session_to_runtime_session.get(mcp_session_id)
        return resolved_session_id, trigger_source

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        runtime_session_id, trigger_source = self._resolve_session_params(scope)
        session_token = set_current_runtime_session_id(runtime_session_id)
        trigger_token = set_current_runtime_trigger_source(trigger_source)
        butler_token = set_current_runtime_butler_name(self._butler_name)
        try:
            await self._app(scope, receive, send)
        finally:
            reset_current_runtime_butler_name(butler_token)
            reset_current_runtime_trigger_source(trigger_token)
            reset_current_runtime_session_id(session_token)
