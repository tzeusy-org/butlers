"""route() reports a target tool's error result as a failure, not a delivery (bu-dhloi1.293).

FastMCP's ``CallToolResult`` names the flag ``is_error``; ``isError`` is only the
wire name. route() read ``isError``, so every routed tool failure was recorded as
delivered. These tests drive the real ``route()`` against a real FastMCP server
through a real FastMCP ``Client``.

route() also adds a ``trace_context`` carrier while a span is active. It now adds
it only to the tools that declare that parameter
(``tests/contracts/test_route_trace_context_tools.py``): any other tool's fixed
signature refuses it, and with errors now reported that refusal would fail the
call.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

from butlers.core.telemetry import inject_trace_context

pytestmark = pytest.mark.unit


def _server(seen: list[dict[str, Any]]) -> FastMCP:
    server = FastMCP("target")

    @server.tool
    def state_set(key: str, value: int) -> dict:
        seen.append({"key": key, "value": value})
        if value < 0:
            raise ValueError("value must be non-negative")
        return {"key": key, "value": value}

    @server.tool
    def contacts_resolve_channel(
        channel_type: str, trace_context: dict[str, str] | None = None
    ) -> dict:
        seen.append({"channel_type": channel_type, "trace_context": trace_context})
        return {"traced": bool(trace_context)}

    @server.tool(name="route.execute")
    def route_execute(schema_version: str) -> dict:
        seen.append({"schema_version": schema_version})
        raise ValueError("delivery refused by the target")

    return server


@pytest.fixture
def routing(monkeypatch):
    module = importlib.import_module("butlers.tools.switchboard.routing.route")
    monkeypatch.delenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", raising=False)
    monkeypatch.setattr(
        module,
        "resolve_routing_target",
        AsyncMock(return_value=({"endpoint_url": "http://localhost:41104/mcp"}, None)),
    )
    monkeypatch.setattr(module, "_log_routing", AsyncMock())
    monkeypatch.setattr(module, "_record_confirmed_route", AsyncMock())
    return module


@pytest.fixture
def seen(monkeypatch, routing) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    server = _server(calls)

    async def call_tool(endpoint_url: str, tool: str, args: dict[str, Any]) -> Any:
        async with Client(server) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    monkeypatch.setattr(routing, "_call_tool_with_router_client", call_tool)
    # The capability-header path opens its own client; reach the same server.
    monkeypatch.setattr(routing, "MCPClient", lambda _transport: Client(server))
    return calls


def _span():
    context = SpanContext(
        trace_id=0x0AF7651916CD43DD8448EB211C80319C,
        span_id=0x00F067AA0BA902B7,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.use_span(NonRecordingSpan(context), end_on_exit=False)


async def test_a_tool_error_is_a_rejected_route_not_a_delivery(routing, seen) -> None:
    outcome = await routing.route("POOL", "health", "state_set", {"key": "k", "value": -1})

    assert "result" not in outcome
    assert outcome["retryable"] is False
    assert outcome["transport"]["outcome"] == "rejected"
    assert "non-negative" in outcome["error"]
    routing._record_confirmed_route.assert_not_awaited()
    (log_call,) = routing._log_routing.await_args_list
    assert log_call.args[4] is False  # recorded as a failed route
    assert seen == [{"key": "k", "value": -1}]


async def test_a_successful_tool_is_still_delivered(routing, seen) -> None:
    outcome = await routing.route("POOL", "health", "state_set", {"key": "k", "value": 1})

    assert outcome["result"] == {"key": "k", "value": 1}
    assert outcome["transport"]["outcome"] == "confirmed"
    routing._record_confirmed_route.assert_awaited_once()


async def test_a_tool_error_on_the_capability_header_path_is_rejected_too(
    monkeypatch, routing, seen
) -> None:
    from butlers.core import fact_authority

    monkeypatch.setattr(fact_authority, "source_transport_headers", lambda _target: {"x-test": "1"})
    outcome = await routing.route("POOL", "messenger", "route.execute", {"schema_version": "v1"})

    assert outcome["transport"]["outcome"] == "rejected"
    assert "delivery refused" in outcome["error"]
    routing._record_confirmed_route.assert_not_awaited()


async def test_a_legacy_isError_result_is_still_refused(monkeypatch, routing) -> None:
    class _Legacy:
        isError = True
        content: list = []

    monkeypatch.setattr(routing, "_call_tool_with_router_client", AsyncMock(return_value=_Legacy()))
    with pytest.raises(routing.TransportRejected):
        await routing._call_butler_tool("http://t/mcp", "state_set", {})


async def test_with_tracing_on_a_strict_tool_gets_no_carrier(routing, seen) -> None:
    with _span():
        assert inject_trace_context(), "tracing must be active for this test to mean anything"
        outcome = await routing.route("POOL", "health", "state_set", {"key": "k", "value": 1})

    assert outcome["result"] == {"key": "k", "value": 1}
    assert seen == [{"key": "k", "value": 1}]


async def test_with_tracing_on_a_tool_declaring_the_carrier_receives_it(routing, seen) -> None:
    with _span():
        outcome = await routing.route(
            "POOL", "relationship", "contacts_resolve_channel", {"channel_type": "email"}
        )

    assert outcome["result"] == {"traced": True}
    assert "traceparent" in seen[0]["trace_context"]
