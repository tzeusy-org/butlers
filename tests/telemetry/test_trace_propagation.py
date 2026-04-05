"""Tests for inter-butler trace context propagation."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.core.telemetry import extract_trace_from_args, inject_trace_context

pytestmark = pytest.mark.unit


def _reset_otel_global_state():
    """Fully reset the OpenTelemetry global tracer provider state."""
    trace._TRACER_PROVIDER_SET_ONCE = trace.Once()
    trace._TRACER_PROVIDER = None


@pytest.fixture(autouse=True)
def otel_provider():
    """Set up an in-memory TracerProvider for every test, then tear down."""
    _reset_otel_global_state()
    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": "butler-test"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    yield exporter
    provider.shutdown()
    _reset_otel_global_state()


class TestExtractTraceContextFromToolArgs:
    """Test that tool handlers extract _trace_context from incoming args."""

    async def test_extracts_context_and_removes_key(self, otel_provider):
        """Extracts _trace_context from kwargs, creates child span, removes the key."""
        tracer = trace.get_tracer("test")

        with tracer.start_as_current_span("switchboard.route") as parent_span:
            parent_span_id = parent_span.get_span_context().span_id
            trace_context = inject_trace_context()

        kwargs = {"key": "value", "_trace_context": trace_context}
        parent_ctx = extract_trace_from_args(kwargs)

        # Key must be removed
        assert "_trace_context" not in kwargs
        assert "key" in kwargs

        with tracer.start_as_current_span("butler.tool.get_state", context=parent_ctx):
            pass

        spans = otel_provider.get_finished_spans()
        tool_span = next(s for s in spans if s.name == "butler.tool.get_state")
        assert tool_span.parent.span_id == parent_span_id

    async def test_no_trace_context_in_args(self, otel_provider):
        """Tool handler works when _trace_context is absent."""
        tracer = trace.get_tracer("test")
        kwargs = {"key": "value"}
        parent_ctx = extract_trace_from_args(kwargs)

        with tracer.start_as_current_span("butler.tool.get_state", context=parent_ctx):
            pass

        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.get_state"


class TestEndToEndTracePropagation:
    """Test end-to-end trace propagation from switchboard to target butler."""

    async def test_trace_hierarchy_across_butlers(self, otel_provider):
        """Trace hierarchy is maintained across butler boundaries."""
        tracer = trace.get_tracer("test")

        with tracer.start_as_current_span("external.request") as root_span:
            root_trace_id = root_span.get_span_context().trace_id
            with tracer.start_as_current_span("switchboard.route"):
                trace_context = inject_trace_context()

        kwargs = {"key": "value", "_trace_context": trace_context}
        parent_ctx = extract_trace_from_args(kwargs)

        with tracer.start_as_current_span("butler.tool.get_state", context=parent_ctx) as ts:
            tool_trace_id = ts.get_span_context().trace_id

        assert tool_trace_id == root_trace_id
        spans = otel_provider.get_finished_spans()
        assert len(spans) == 3
        assert len({s.context.trace_id for s in spans}) == 1
