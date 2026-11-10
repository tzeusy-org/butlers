"""A relay reaches its strict twin through route() with tracing on (RFC 0038 D7, bu-2c1g7v).

route() adds a ``trace_context`` carrier to the target's arguments whenever a
span is active. A relay target is a strict service twin, so the relay's call
drops that carrier; and a twin's error result is a refused call, never a
delivered one. Both are checked through Switchboard's real ``route()`` against
a real FastMCP server reached by a real FastMCP ``Client``.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client, FastMCP
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags
from pydantic import BaseModel

from butlers.core.service_plane import wiring
from butlers.core.service_plane.relays import RelayEndpoint
from butlers.core.telemetry import inject_trace_context

pytestmark = pytest.mark.unit

_ARGS = {"memory_type": "fact", "memory_id": "22222222-2222-2222-2222-222222222222"}


def _twin(seen: list[dict[str, Any]]) -> FastMCP:
    server = FastMCP("health")

    @server.tool
    def memory_get_service(memory_type: str, memory_id: str) -> dict:
        seen.append({"memory_type": memory_type, "memory_id": memory_id})
        if memory_type == "episode":
            raise ValueError("not a catalogued fact")
        return {"id": memory_id, "type": memory_type}

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


def _connect(monkeypatch, routing, server: FastMCP) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def call_tool(endpoint_url: str, tool: str, args: dict[str, Any]) -> Any:
        sent.append(dict(args))
        async with Client(server) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    monkeypatch.setattr(routing, "_call_tool_with_router_client", call_tool)
    return sent


def _endpoint() -> RelayEndpoint:
    return RelayEndpoint(
        name="memory.catalog_fetch",
        allowed_callers=frozenset(),
        target_butlers=frozenset({"health"}),
        target_tool="memory_get_service",
        args_model=BaseModel,
    )


def _span():
    context = SpanContext(
        trace_id=0x0AF7651916CD43DD8448EB211C80319C,
        span_id=0x00F067AA0BA902B7,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    return trace.use_span(NonRecordingSpan(context), end_on_exit=False)


async def test_with_tracing_on_the_twin_gets_exactly_the_relay_arguments(
    monkeypatch, routing
) -> None:
    seen: list[dict[str, Any]] = []
    sent = _connect(monkeypatch, routing, _twin(seen))

    with _span():
        assert inject_trace_context(), "tracing must be active for this test to mean anything"
        outcome = await wiring.relay_dispatch("POOL", _endpoint())(
            "health", "memory_get_service", dict(_ARGS)
        )

    assert outcome["result"] == {"id": _ARGS["memory_id"], "type": "fact"}
    assert sent == [_ARGS] and seen == [_ARGS]


async def test_the_injected_carrier_is_what_a_strict_twin_refuses(monkeypatch, routing) -> None:
    """Without the relay's call, route()'s carrier reaches the twin and is refused."""
    seen: list[dict[str, Any]] = []
    server = _twin(seen)

    with _span():
        carrier = inject_trace_context()
    async with Client(server) as client:
        result = await client.call_tool(
            "memory_get_service", {**_ARGS, "trace_context": carrier}, raise_on_error=False
        )

    assert result.is_error is True and seen == []


async def test_a_twin_error_is_a_refused_call_not_a_delivered_one(monkeypatch, routing) -> None:
    seen: list[dict[str, Any]] = []
    _connect(monkeypatch, routing, _twin(seen))
    args = {**_ARGS, "memory_type": "episode"}

    with _span():
        outcome = await wiring.relay_dispatch("POOL", _endpoint())(
            "health", "memory_get_service", args
        )

    assert "result" not in outcome
    assert outcome["retryable"] is False
    assert outcome["transport"]["outcome"] == "rejected"
    assert "not a catalogued fact" in outcome["error"]
    routing._record_confirmed_route.assert_not_awaited()
    assert seen == [args]


async def test_a_legacy_isError_result_is_refused_too(monkeypatch, routing) -> None:
    class _Legacy:
        isError = True
        content: list = []

    async def call_tool(endpoint_url: str, tool: str, args: dict[str, Any]) -> Any:
        return _Legacy()

    monkeypatch.setattr(routing, "_call_tool_with_router_client", call_tool)
    call = wiring.relay_call_fn(routing)
    with pytest.raises(routing.TransportRejected):
        await call("http://t/mcp", "memory_get_service", dict(_ARGS))


async def test_an_empty_result_is_uncertain_not_delivered(monkeypatch, routing) -> None:
    class _Empty:
        is_error = False
        content: list = []

    async def call_tool(endpoint_url: str, tool: str, args: dict[str, Any]) -> Any:
        return _Empty()

    monkeypatch.setattr(routing, "_call_tool_with_router_client", call_tool)
    with pytest.raises(routing.TransportUncertain):
        await wiring.relay_call_fn(routing)("http://t/mcp", "memory_get_service", dict(_ARGS))
