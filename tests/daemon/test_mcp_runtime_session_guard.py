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

    # Synthetic conformance input exercises the real registration/guard seam;
    # this is not accepted-source, HTTP authentication or SQL proof.
    from butlers.core import fact_authority

    seen_reports = []

    async def report_app(scope, receive, send):
        seen_reports.append(fact_authority.current_fact_write_context().authority)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guarded_report = _McpRuntimeSessionGuard(report_app, butler_name="relationship")
    synthetic = fact_authority._current_report.set(fact_authority.FactWriteContext("owner"))
    try:
        token = await fact_authority.register_invocation(
            "relationship",
            "actual-runtime",
            source_endpoint=None,
            routed=True,
        )
    finally:
        fact_authority._current_report.reset(synthetic)
    try:
        await guarded_report(
            _http_scope(query_string=b"runtime_session_id=actual-runtime"),
            _empty_receive,
            _discard_send,
        )
        await guarded_report(
            _http_scope(
                headers=[
                    (fact_authority.INVOCATION_HEADER.lower().encode(), token.encode()),
                    (b"mcp-session-id", b"admitted-session"),
                ]
            ),
            _empty_receive,
            _discard_send,
        )
        await _McpRuntimeSessionGuard(report_app, butler_name="health")(
            _http_scope(headers=[(fact_authority.INVOCATION_HEADER.encode(), token.encode())]),
            _empty_receive,
            _discard_send,
        )
        fact_authority.settle_invocation(token)
        await guarded_report(
            _http_scope(
                headers=[
                    (fact_authority.INVOCATION_HEADER.encode(), token.encode()),
                ]
            ),
            _empty_receive,
            _discard_send,
        )
        assert seen_reports == ["third_party", "owner", "third_party", "third_party"]
        assert fact_authority._current_report.get() is None
    finally:
        fact_authority.settle_invocation(token)
