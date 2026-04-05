"""Tests for OTel span wrappers (tool_span) and trace context propagation."""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.core.telemetry import (
    clear_active_session_context,
    extract_trace_context,
    get_active_session_context,
    get_traceparent_env,
    inject_trace_context,
    set_active_session_context,
    tool_span,
)

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


@pytest.fixture(autouse=True)
def _clean_session_context():
    """Reset the active session context between tests."""
    clear_active_session_context()
    yield
    clear_active_session_context()


# ---------------------------------------------------------------------------
# tool_span tests
# ---------------------------------------------------------------------------


class TestToolSpanContextManager:
    """tool_span used as a context manager."""

    def test_creates_span_with_name_and_attributes(self, otel_provider):
        """tool_span creates a correctly named span with butler.name attribute."""
        with tool_span("state_get", butler_name="switchboard"):
            pass
        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.state_get"
        assert spans[0].attributes["butler.name"] == "switchboard"

    def test_records_exception_on_error(self, otel_provider):
        with pytest.raises(ValueError, match="boom"):
            with tool_span("state_get", butler_name="switchboard"):
                raise ValueError("boom")
        spans = otel_provider.get_finished_spans()
        assert spans[0].status.status_code == trace.StatusCode.ERROR
        exception_events = [e for e in spans[0].events if e.name == "exception"]
        assert len(exception_events) == 1
        assert "ValueError" in exception_events[0].attributes["exception.type"]
        assert "boom" in exception_events[0].attributes["exception.message"]


class TestToolSpanDecorator:
    """tool_span used as a decorator on async functions."""

    async def test_decorator_creates_span_and_records_exception(self, otel_provider):
        @tool_span("do_work", butler_name="heartbeat")
        async def do_work(x: int) -> int:
            return x * 2

        result = await do_work(21)
        assert result == 42
        spans = otel_provider.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "butler.tool.do_work"
        assert spans[0].attributes["butler.name"] == "heartbeat"

        @tool_span("fail_work", butler_name="heartbeat")
        async def fail_work():
            raise RuntimeError("async boom")

        with pytest.raises(RuntimeError, match="async boom"):
            await fail_work()
        spans2 = otel_provider.get_finished_spans()
        fail_span = next(s for s in spans2 if s.name == "butler.tool.fail_work")
        assert fail_span.status.status_code == trace.StatusCode.ERROR


# ---------------------------------------------------------------------------
# Trace context propagation tests
# ---------------------------------------------------------------------------


class TestInjectExtractAndTraceparent:
    """inject/extract trace context and get_traceparent_env."""

    def test_inject_returns_traceparent(self, otel_provider):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("parent"):
            ctx = inject_trace_context()
        assert isinstance(ctx, dict)
        assert "traceparent" in ctx
        parts = ctx["traceparent"].split("-")
        assert len(parts) == 4 and parts[0] == "00"

    def test_extract_creates_parent_child_relationship(self, otel_provider):
        tracer = trace.get_tracer("test")

        with tracer.start_as_current_span("original-parent") as parent_span:
            parent_trace_id = parent_span.get_span_context().trace_id
            root_span_id = parent_span.get_span_context().span_id
            ctx_dict = inject_trace_context()

        parent_ctx = extract_trace_context(ctx_dict)
        with tracer.start_as_current_span("child", context=parent_ctx) as child_span:
            assert child_span.get_span_context().trace_id == parent_trace_id

        spans = otel_provider.get_finished_spans()
        remote_child = next(s for s in spans if s.name == "child")
        assert remote_child.parent.span_id == root_span_id

    def test_traceparent_env_and_empty_without_span(self, otel_provider):
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("parent"):
            env = get_traceparent_env()
        assert isinstance(env, dict)
        assert "TRACEPARENT" in env
        assert env["TRACEPARENT"].startswith("00-")

        empty_env = get_traceparent_env()
        assert isinstance(empty_env, dict)


# ---------------------------------------------------------------------------
# Active session context tests (cross-MCP-boundary trace propagation)
# ---------------------------------------------------------------------------


class TestActiveSessionContext:
    """Verify tool_span uses the active session context as parent."""

    def test_tool_span_uses_session_context_as_parent(self, otel_provider):
        """tool_span parents to the session span even when the current task's OTel context is empty.

        This simulates cross-MCP-boundary trace propagation: the spawner sets the active
        session context, detaches from contextvars (simulating a separate HTTP handler task),
        and tool_span must still parent to the session span via the module-level store.
        """
        tracer = trace.get_tracer("test")

        session_span = tracer.start_span("butler.llm_session")
        session_ctx = trace.set_span_in_context(session_span)
        token = trace.context_api.attach(session_ctx)
        set_active_session_context(trace.context_api.get_current())

        # Detach from contextvars to simulate a separate HTTP handler task
        trace.context_api.detach(token)

        # tool_span should still parent to the session span via the
        # module-level _active_session_context
        with tool_span("state_get", butler_name="switchboard"):
            pass

        session_span.end()

        spans = otel_provider.get_finished_spans()
        tool = next(s for s in spans if s.name == "butler.tool.state_get")
        session = next(s for s in spans if s.name == "butler.llm_session")

        assert tool.context.trace_id == session.context.trace_id
        assert tool.parent.span_id == session.context.span_id

    def test_nested_tool_spans_share_trace_id(self, otel_provider):
        """Sequential tool calls both parent to the same session span."""
        tracer = trace.get_tracer("test")

        session_span = tracer.start_span("butler.llm_session")
        session_ctx = trace.set_span_in_context(session_span)
        token = trace.context_api.attach(session_ctx)
        set_active_session_context(trace.context_api.get_current())
        trace.context_api.detach(token)

        with tool_span("state_get", butler_name="switchboard"):
            pass
        with tool_span("notify", butler_name="switchboard"):
            pass

        session_span.end()

        spans = otel_provider.get_finished_spans()
        tool_get = next(s for s in spans if s.name == "butler.tool.state_get")
        tool_notify = next(s for s in spans if s.name == "butler.tool.notify")
        session = next(s for s in spans if s.name == "butler.llm_session")

        assert tool_get.context.trace_id == session.context.trace_id
        assert tool_notify.context.trace_id == session.context.trace_id
        assert tool_get.parent.span_id == session.context.span_id
        assert tool_notify.parent.span_id == session.context.span_id

    def test_root_span_and_lifecycle(self, otel_provider):
        """Without session context, tool_span creates root; set/get/clear lifecycle works."""
        with tool_span("state_get", butler_name="switchboard"):
            pass
        spans = otel_provider.get_finished_spans()
        assert spans[0].parent is None

        # Lifecycle
        assert get_active_session_context() is None
        tracer = trace.get_tracer("test")
        span = tracer.start_span("session")
        ctx = trace.set_span_in_context(span)
        set_active_session_context(ctx)
        assert get_active_session_context() is ctx
        clear_active_session_context()
        assert get_active_session_context() is None
        span.end()
