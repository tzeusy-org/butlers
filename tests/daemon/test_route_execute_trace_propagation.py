"""Test trace propagation through route.execute to spawner.

Verifies that:
- Trace context from route.execute is propagated to spawner
- Butler sessions spawned via routing share the same trace_id as parent
- Sessions triggered without trace context create independent traces

Each test boots a real daemon (``booted_daemon``) and calls route.execute over MCP;
the routed session runs through the real route inbox and spawner, and the spans
come from the production instrumentation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.core.telemetry import inject_trace_context
from butlers.testing.daemon import write_butler_config

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def otel_provider(monkeypatch: pytest.MonkeyPatch):
    """Set up an in-memory TracerProvider for every test, then tear down."""
    # Startup's init_telemetry then reuses this provider instead of exporting.
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


def _route_request_context(
    *,
    source_endpoint_identity: str = "switchboard",
    source_sender_identity: str = "health",
    source_channel: str = "telegram_bot",
    source_thread_identity: str | None = "12345",
    request_id: str = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b",
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "request_id": request_id,
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }
    if source_thread_identity is not None:
        ctx["source_thread_identity"] = source_thread_identity
    return ctx


async def _route(booted_daemon, tmp_path: Path, **arguments: Any):
    """Route one request through a booted daemon and wait for its session."""
    booted = await booted_daemon(write_butler_config(tmp_path))
    result = await booted.call_tool(
        "route.execute",
        {
            "schema_version": "route.v1",
            "request_context": _route_request_context(),
            "input": {"prompt": "Run health check."},
            **arguments,
        },
    )
    assert result["status"] == "accepted"
    await booted.runtime.wait_for_invocations(1)
    # The session span ends after the runtime returns; shutdown drains it.
    await booted.shutdown()
    return booted


def _spans(exporter: InMemorySpanExporter, name: str) -> list:
    return [span for span in exporter.get_finished_spans() if span.name == name]


class TestRouteExecuteTracePropagation:
    """Verify trace context propagates through route.execute to spawner."""

    async def test_trace_context_propagated_to_spawner(
        self, booted_daemon, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.execute span is a child of the switchboard's trace."""
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("switchboard.route") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            parent_span_id = parent_span.get_span_context().span_id
            trace_context = inject_trace_context()

        await _route(booted_daemon, tmp_path, trace_context=trace_context)

        (route_span,) = _spans(otel_provider, "butler.tool.route.execute")
        assert route_span.context.trace_id == parent_trace_id
        assert route_span.parent.span_id == parent_span_id
        # The routed session joins the switchboard's trace too.
        (session_span,) = _spans(otel_provider, "butler.llm_session")
        assert session_span.context.trace_id == parent_trace_id

    async def test_no_trace_context_creates_independent_trace(
        self, booted_daemon, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """Sessions without trace_context create independent traces.

        With no routed trace_context there is no Switchboard trace to join: the
        route span descends only from the MCP server's own span for this request,
        and the session stays in that request's trace.
        """
        await _route(booted_daemon, tmp_path)

        (route_span,) = _spans(otel_provider, "butler.tool.route.execute")
        # Client and server both record a "tools/call" span; the server's is the
        # local child of the remote client span.
        (server_span,) = [
            span
            for span in _spans(otel_provider, "tools/call route.execute")
            if span.parent is not None and span.parent.is_remote
        ]
        assert route_span.parent.span_id == server_span.context.span_id
        (session_span,) = _spans(otel_provider, "butler.llm_session")
        assert session_span.context.trace_id == route_span.context.trace_id
