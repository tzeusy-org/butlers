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


# ---------------------------------------------------------------------------
# Routing context is derived only from a valid invocation binding (bu-m2zcah)
# ---------------------------------------------------------------------------

_OWNER_ENTITY_ID = "11111111-1111-1111-1111-111111111111"
_OWNER_CONTEXT = {"source_entity_id": _OWNER_ENTITY_ID}


@pytest.fixture
def owner_session(monkeypatch: pytest.MonkeyPatch):
    """A live owner-routed session's routing context, with a resolvable owner entity."""
    import uuid

    from butlers.core import tool_call_capture
    from butlers.modules.memory import content_authority

    async def _owner(_pool: Any) -> uuid.UUID:
        return uuid.UUID(_OWNER_ENTITY_ID)

    monkeypatch.setattr(content_authority, "fetch_owner_entity_id", _owner)
    session_id = f"owner-session-{uuid.uuid4().hex}"
    tool_call_capture.set_runtime_session_routing_context(session_id, _OWNER_CONTEXT)
    yield session_id
    tool_call_capture.clear_runtime_session_routing_context(session_id)


async def _observe_authority(
    scope: dict[str, Any], *, butler_name: str = "relationship"
) -> tuple[dict[str, Any] | None, str]:
    from butlers.core.tool_call_capture import get_current_runtime_session_routing_context
    from butlers.modules.memory.content_authority import resolve_content_authority

    seen: list[tuple[dict[str, Any] | None, str]] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        authority = await resolve_content_authority(object())
        seen.append((get_current_runtime_session_routing_context(), authority.authority))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await _McpRuntimeSessionGuard(app, butler_name=butler_name)(
        scope, _empty_receive, _discard_send
    )
    return seen[0]


async def test_unbound_query_locator_cannot_select_an_owner_routing_context(
    owner_session: str,
) -> None:
    context, authority = await _observe_authority(
        _http_scope(query_string=f"runtime_session_id={owner_session}".encode())
    )

    assert context is None
    assert authority == "third_party"


async def test_valid_invocation_binding_keeps_its_routing_context(owner_session: str) -> None:
    from butlers.core import fact_authority

    token = await fact_authority.register_invocation(
        "relationship", owner_session, source_endpoint=None, routed=True
    )
    try:
        context, authority = await _observe_authority(
            _http_scope(
                headers=[(fact_authority.INVOCATION_HEADER.lower().encode(), token.encode())]
            )
        )
    finally:
        fact_authority.settle_invocation(token)

    assert context == _OWNER_CONTEXT
    assert authority == "owner"


async def test_binding_wins_over_a_query_locator_naming_another_session(
    owner_session: str,
) -> None:
    from butlers.core import fact_authority

    token = await fact_authority.register_invocation(
        "relationship", owner_session, source_endpoint=None, routed=True
    )
    try:
        context, authority = await _observe_authority(
            _http_scope(
                query_string=b"runtime_session_id=some-other-session",
                headers=[(fact_authority.INVOCATION_HEADER.lower().encode(), token.encode())],
            )
        )
    finally:
        fact_authority.settle_invocation(token)

    assert context == _OWNER_CONTEXT
    assert authority == "owner"


async def test_expired_or_foreign_binding_leaves_the_locator_unbound(owner_session: str) -> None:
    from butlers.core import fact_authority

    token = await fact_authority.register_invocation(
        "health", owner_session, source_endpoint=None, routed=True
    )
    try:
        context, authority = await _observe_authority(
            _http_scope(
                query_string=f"runtime_session_id={owner_session}".encode(),
                headers=[(fact_authority.INVOCATION_HEADER.lower().encode(), token.encode())],
            )
        )
    finally:
        fact_authority.settle_invocation(token)

    assert context is None
    assert authority == "third_party"


async def test_request_with_neither_locator_nor_binding_keeps_system_authority(
    owner_session: str,
) -> None:
    context, authority = await _observe_authority(_http_scope())

    assert context is None
    assert authority == "system"


def test_in_process_session_outside_the_guard_keeps_its_routing_context(
    owner_session: str,
) -> None:
    from butlers.core.tool_call_capture import (
        get_current_runtime_session_routing_context,
        reset_current_runtime_session_id,
        set_current_runtime_session_id,
    )

    token = set_current_runtime_session_id(owner_session)
    try:
        assert get_current_runtime_session_routing_context() == _OWNER_CONTEXT
    finally:
        reset_current_runtime_session_id(token)


# ---------------------------------------------------------------------------
# Edges of the binding-only rule: expiry, rebound transport, flag reset (bu-x8l4os)
# ---------------------------------------------------------------------------


async def _observe_with_guard(
    guard: _McpRuntimeSessionGuard, scope: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """Run one request through *guard* and report what a tool would see inside it."""
    from butlers.core.tool_call_capture import get_current_runtime_session_routing_context
    from butlers.modules.memory.content_authority import resolve_content_authority

    seen: list[tuple[dict[str, Any] | None, str]] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        authority = await resolve_content_authority(object())
        seen.append((get_current_runtime_session_routing_context(), authority.authority))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    guard._app = app
    await guard(scope, _empty_receive, _discard_send)
    return seen[0]


async def test_expired_invocation_token_leaves_the_locator_unbound(owner_session: str) -> None:
    import dataclasses
    import time

    from butlers.core import fact_authority

    token = await fact_authority.register_invocation(
        "relationship", owner_session, source_endpoint=None, routed=True
    )
    try:
        live = fact_authority._invocations[token]
        fact_authority._invocations[token] = dataclasses.replace(
            live, deadline=time.monotonic() - 1
        )
        context, authority = await _observe_authority(
            _http_scope(
                query_string=f"runtime_session_id={owner_session}".encode(),
                headers=[(fact_authority.INVOCATION_HEADER.lower().encode(), token.encode())],
            )
        )
    finally:
        fact_authority.settle_invocation(token)

    assert context is None
    assert authority == "third_party"


async def test_rebound_transport_session_leaves_the_locator_unbound(owner_session: str) -> None:
    from butlers.core import fact_authority

    guard = _McpRuntimeSessionGuard(_discard_send, butler_name="relationship")
    transport = (b"mcp-session-id", b"admitted-transport")
    first = await fact_authority.register_invocation(
        "relationship", "first-invocation", source_endpoint=None, routed=True
    )
    second = await fact_authority.register_invocation(
        "relationship", owner_session, source_endpoint=None, routed=True
    )
    try:
        # The transport session is admitted to the first invocation.
        await _observe_with_guard(
            guard,
            _http_scope(
                headers=[(fact_authority.INVOCATION_HEADER.lower().encode(), first.encode())]
                + [transport]
            ),
        )
        # A different, valid invocation cannot take that transport over, so its locator
        # stays diagnostic.
        context, authority = await _observe_with_guard(
            guard,
            _http_scope(
                query_string=f"runtime_session_id={owner_session}".encode(),
                headers=[(fact_authority.INVOCATION_HEADER.lower().encode(), second.encode())]
                + [transport],
            ),
        )
    finally:
        fact_authority.settle_invocation(first)
        fact_authority.settle_invocation(second)

    assert context is None
    assert authority == "third_party"


async def test_unbound_flag_does_not_outlive_the_request(owner_session: str) -> None:
    from butlers.core.tool_call_capture import (
        current_runtime_session_locator_is_unbound,
        get_current_runtime_session_routing_context,
        reset_current_runtime_session_id,
        set_current_runtime_session_id,
    )
    from butlers.modules.memory.content_authority import resolve_content_authority

    inside: list[bool] = []

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        inside.append(current_runtime_session_locator_is_unbound())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await _McpRuntimeSessionGuard(app, butler_name="relationship")(
        _http_scope(query_string=f"runtime_session_id={owner_session}".encode()),
        _empty_receive,
        _discard_send,
    )

    assert inside == [True]
    assert current_runtime_session_locator_is_unbound() is False
    # A later in-process session in the same context is not suppressed by a leaked flag.
    token = set_current_runtime_session_id(owner_session)
    try:
        assert get_current_runtime_session_routing_context() == _OWNER_CONTEXT
        assert (await resolve_content_authority(object())).authority == "owner"
    finally:
        reset_current_runtime_session_id(token)


# ---------------------------------------------------------------------------
# Response-side transport admission: initialize records the binding (bu-84ffid)
# ---------------------------------------------------------------------------


async def _initialize_response(
    guard: _McpRuntimeSessionGuard, token: str, transport: bytes
) -> None:
    """Run an initialize request whose response hands out *transport* as its MCP session id."""
    from butlers.core import fact_authority

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"mcp-session-id", transport)],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    guard._app = app
    await guard(
        _http_scope(headers=[(fact_authority.INVOCATION_HEADER.lower().encode(), token.encode())]),
        _empty_receive,
        _discard_send,
    )


async def test_initialize_response_records_the_transport_binding(owner_session: str) -> None:
    from butlers.core import fact_authority

    guard = _McpRuntimeSessionGuard(_discard_send, butler_name="relationship")
    transport = b"handed-out-transport"
    first = await fact_authority.register_invocation(
        "relationship", "first-invocation", source_endpoint=None, routed=True
    )
    second = await fact_authority.register_invocation(
        "relationship", owner_session, source_endpoint=None, routed=True
    )
    try:
        await _initialize_response(guard, first, transport)
        # The transport was bound by the response alone, so another valid invocation that
        # presents it cannot take it over and its locator stays diagnostic.
        context, authority = await _observe_with_guard(
            guard,
            _http_scope(
                query_string=f"runtime_session_id={owner_session}".encode(),
                headers=[
                    (fact_authority.INVOCATION_HEADER.lower().encode(), second.encode()),
                    (b"mcp-session-id", transport),
                ],
            ),
        )
    finally:
        fact_authority.settle_invocation(first)
        fact_authority.settle_invocation(second)

    assert context is None
    assert authority == "third_party"


async def test_initialize_response_cannot_rebind_a_transport_bound_elsewhere(
    owner_session: str,
) -> None:
    from butlers.core import fact_authority

    guard = _McpRuntimeSessionGuard(_discard_send, butler_name="relationship")
    transport = b"shared-transport"
    owner = await fact_authority.register_invocation(
        "relationship", owner_session, source_endpoint=None, routed=True
    )
    other = await fact_authority.register_invocation(
        "relationship", "other-invocation", source_endpoint=None, routed=True
    )
    try:
        await _initialize_response(guard, owner, transport)
        # A second invocation's initialize response names the same transport; it must not
        # displace the first binding.
        await _initialize_response(guard, other, transport)
        context, authority = await _observe_with_guard(
            guard,
            _http_scope(
                query_string=f"runtime_session_id={owner_session}".encode(),
                headers=[
                    (fact_authority.INVOCATION_HEADER.lower().encode(), owner.encode()),
                    (b"mcp-session-id", transport),
                ],
            ),
        )
    finally:
        fact_authority.settle_invocation(owner)
        fact_authority.settle_invocation(other)

    assert context == _OWNER_CONTEXT
    assert authority == "owner"
