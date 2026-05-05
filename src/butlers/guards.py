"""ASGI middleware guards for the Butler MCP server.

Provides:
- _McpSseDisconnectGuard: Suppresses expected SSE POST disconnects.
- _McpRuntimeSessionGuard: Binds runtime session IDs from MCP query/header state.
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
        response_started = False
        response_complete = False

        async def _send_with_response_state(message: dict[str, Any]) -> None:
            nonlocal response_started, response_complete
            message_type = message.get("type")
            await send(message)
            if message_type == "http.response.start":
                response_started = True
            elif message_type == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        try:
            await self._app(scope, receive, _send_with_response_state)
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
                if not response_started:
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 202,
                            "headers": [(b"content-length", b"0")],
                        }
                    )
                if not response_complete:
                    await send({"type": "http.response.body", "body": b""})
            except Exception:
                logger.debug("MCP SSE disconnect response not sent; client already disconnected")


class _McpRuntimeSessionGuard:
    """Bind runtime session IDs from MCP query params/headers into request context."""

    _MAX_SESSION_MAP_SIZE = 4096
    _MCP_SESSION_ID_HEADER = b"mcp-session-id"

    def __init__(self, app: Any, *, butler_name: str) -> None:
        self._app = app
        self._butler_name = butler_name
        self._mcp_session_to_runtime_context: dict[str, tuple[str, str | None]] = {}

    def __getattr__(self, name: str) -> Any:
        """Proxy unknown attributes to wrapped ASGI app for compatibility."""
        return getattr(self._app, name)

    @classmethod
    def _header_value(cls, headers: Any, name: bytes) -> str | None:
        """Return a decoded ASGI header value."""
        if not isinstance(headers, list | tuple):
            return None
        lowered_name = name.lower()
        for raw_name, raw_value in headers:
            if not isinstance(raw_name, (bytes, bytearray)):
                continue
            if bytes(raw_name).lower() != lowered_name:
                continue
            if not isinstance(raw_value, (bytes, bytearray)):
                return None
            value = bytes(raw_value).decode("utf-8", errors="replace").strip()
            return value or None
        return None

    def _remember_mcp_session(
        self,
        mcp_session_id: str | None,
        runtime_session_id: str | None,
        trigger_source: str | None,
    ) -> None:
        """Bind an MCP transport session token to a runtime session."""
        if not mcp_session_id or not runtime_session_id:
            return
        self._mcp_session_to_runtime_context[mcp_session_id] = (
            runtime_session_id,
            trigger_source,
        )
        if len(self._mcp_session_to_runtime_context) > self._MAX_SESSION_MAP_SIZE:
            oldest = next(iter(self._mcp_session_to_runtime_context))
            self._mcp_session_to_runtime_context.pop(oldest, None)

    def _resolve_session_params(self, scope: dict[str, Any]) -> tuple[str | None, str | None]:
        """Extract runtime_session_id and trigger_source from query params or MCP headers."""
        query_string = scope.get("query_string")
        parsed = (
            parse_qs(query_string.decode("utf-8", errors="replace"))
            if isinstance(query_string, (bytes, bytearray))
            else {}
        )
        runtime_values = parsed.get("runtime_session_id")
        runtime_session_id = runtime_values[0].strip() if runtime_values else None
        mcp_values = parsed.get("session_id")
        mcp_session_id = (mcp_values[0].strip() if mcp_values else None) or self._header_value(
            scope.get("headers"), self._MCP_SESSION_ID_HEADER
        )

        trigger_values = parsed.get("trigger_source")
        trigger_source = trigger_values[0].strip() if trigger_values else None

        self._remember_mcp_session(mcp_session_id, runtime_session_id, trigger_source)

        resolved_session_id = runtime_session_id
        if (not resolved_session_id or trigger_source is None) and mcp_session_id:
            remembered = self._mcp_session_to_runtime_context.get(mcp_session_id)
            if remembered is not None:
                remembered_session_id, remembered_trigger_source = remembered
                resolved_session_id = resolved_session_id or remembered_session_id
                if trigger_source is None:
                    trigger_source = remembered_trigger_source
        return resolved_session_id, trigger_source

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        runtime_session_id, trigger_source = self._resolve_session_params(scope)
        session_token = set_current_runtime_session_id(runtime_session_id)
        trigger_token = set_current_runtime_trigger_source(trigger_source)
        butler_token = set_current_runtime_butler_name(self._butler_name)

        async def _send_with_session_capture(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and runtime_session_id:
                response_mcp_session_id = self._header_value(
                    message.get("headers"), self._MCP_SESSION_ID_HEADER
                )
                self._remember_mcp_session(
                    response_mcp_session_id,
                    runtime_session_id,
                    trigger_source,
                )
            await send(message)

        try:
            await self._app(scope, receive, _send_with_session_capture)
        finally:
            reset_current_runtime_butler_name(butler_token)
            reset_current_runtime_trigger_source(trigger_token)
            reset_current_runtime_session_id(session_token)
