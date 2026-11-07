"""Tests for async route dispatch tracing continuity.

Verifies:
1. The background processing task continues the incoming distributed trace.
2. The process span carries request_id as an attribute.
3. The process span links back to the accept-phase span via SpanLink.
4. The accept-phase span carries request_id as an attribute.
5. The accept span still ends before the process span completes.

Each test boots a real daemon (``booted_daemon``) and calls route.execute over MCP;
the background task, route inbox and spawner are the production ones, and the
spans come from the production instrumentation.
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

_REQUEST_ID = "018f6f4e-5b3b-7b2d-9c2f-7b7b6b6b6b6b"


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
    request_id: str = _REQUEST_ID,
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "received_at": "2026-02-14T00:00:00Z",
        "source_channel": source_channel,
        "source_endpoint_identity": source_endpoint_identity,
        "source_sender_identity": source_sender_identity,
    }


async def _route(booted_daemon, tmp_path: Path, **arguments: Any) -> None:
    """Route one request, wait for its session, then drain the daemon."""
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
    # The process span ends after the session returns; shutdown drains the task.
    await booted.shutdown()


def _span(exporter: InMemorySpanExporter, name: str):
    (span,) = [span for span in exporter.get_finished_spans() if span.name == name]
    return span


class TestProcessSpanTraceContinuity:
    """The background processing task should continue switchboard trace context."""

    async def test_process_span_shares_switchboard_trace(
        self, booted_daemon, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.process span shares switchboard trace; accept span ends before process span."""
        parent_tracer = trace.get_tracer("test")
        with parent_tracer.start_as_current_span("switchboard.route") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            parent_span_id = parent_span.get_span_context().span_id
            trace_context = inject_trace_context()

        await _route(booted_daemon, tmp_path, trace_context=trace_context)

        process_span = _span(otel_provider, "route.process")
        assert process_span.context.trace_id == parent_trace_id
        assert process_span.parent is not None
        assert process_span.parent.span_id == parent_span_id
        accept_span = _span(otel_provider, "butler.tool.route.execute")
        assert accept_span.end_time <= process_span.end_time


class TestSpanLink:
    """The process span must carry a SpanLink back to the accept-phase span, with request_id."""

    async def test_process_span_has_link_and_request_id(
        self, booted_daemon, tmp_path: Path, otel_provider: InMemorySpanExporter
    ) -> None:
        """route.process: SpanLink references accept span; both spans carry request_id."""
        await _route(booted_daemon, tmp_path)

        accept_span = _span(otel_provider, "butler.tool.route.execute")
        process_span = _span(otel_provider, "route.process")

        # Both spans carry request_id
        assert accept_span.attributes.get("request_id") == _REQUEST_ID
        assert process_span.attributes.get("request_id") == _REQUEST_ID
        # Accept and process share trace_id
        assert accept_span.context.trace_id == process_span.context.trace_id

        # Process span has SpanLink referencing the accept span
        assert len(process_span.links) >= 1
        link = process_span.links[0]
        assert link.context.span_id == accept_span.context.span_id
        assert link.attributes is not None and "request_id" in link.attributes
