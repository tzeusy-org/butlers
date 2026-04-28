"""Tests for MCP runtime-session attribution middleware."""

from __future__ import annotations

from typing import Any

import pytest

from butlers.core.tool_call_capture import (
    get_current_runtime_butler_name,
    get_current_runtime_session_id,
    get_current_runtime_trigger_source,
)
from butlers.guards import _McpRuntimeSessionGuard

pytestmark = pytest.mark.unit


async def _empty_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _discard_send(_message: dict[str, Any]) -> None:
    return None


def _http_scope(
    *,
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "query_string": query_string,
        "headers": headers or [],
    }


async def test_runtime_session_guard_maps_response_mcp_session_header() -> None:
    """Streamable HTTP follow-up requests may carry only the MCP session header."""
    observed: list[tuple[str | None, str | None, str | None]] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        observed.append(
            (
                get_current_runtime_session_id(),
                get_current_runtime_trigger_source(),
                get_current_runtime_butler_name(),
            )
        )
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"mcp-session-id", b"mcp-transport-session")],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    guard = _McpRuntimeSessionGuard(app, butler_name="health")

    sent: list[dict[str, Any]] = []

    async def _capture_send(message: dict[str, Any]) -> None:
        sent.append(message)

    await guard(
        _http_scope(
            query_string=b"runtime_session_id=runtime-session&trigger_source=schedule%3Aweekly"
        ),
        _empty_receive,
        _capture_send,
    )
    await guard(
        _http_scope(headers=[(b"mcp-session-id", b"mcp-transport-session")]),
        _empty_receive,
        _capture_send,
    )

    assert observed == [
        ("runtime-session", "schedule:weekly", "health"),
        ("runtime-session", "schedule:weekly", "health"),
    ]


async def test_runtime_session_guard_maps_request_mcp_session_header() -> None:
    """A request with runtime query and MCP header should seed later header-only requests."""
    observed: list[str | None] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        observed.append(get_current_runtime_session_id())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard = _McpRuntimeSessionGuard(app, butler_name="health")

    await guard(
        _http_scope(
            query_string=b"runtime_session_id=runtime-session",
            headers=[(b"mcp-session-id", b"mcp-transport-session")],
        ),
        _empty_receive,
        _discard_send,
    )
    await guard(
        _http_scope(headers=[(b"mcp-session-id", b"mcp-transport-session")]),
        _empty_receive,
        _discard_send,
    )

    assert observed == ["runtime-session", "runtime-session"]
