"""Regression tests for tool_span concurrency bugs.

These tests verify that:
1. Concurrent calls to the same @tool_span-decorated async function do not share
   span/token state (no "token was created in a different Context" errors).
2. Cross-session trace contamination is prevented when max_concurrent_sessions > 1:
   tool spans in overlapping sessions are parented to the correct session span.

Tests in this file are designed to FAIL before the fix and PASS after it.
"""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from butlers.core.telemetry import (
    clear_active_session_context,
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
# Bug 1: tool_span decorator concurrency (shared self._span / self._token)
# ---------------------------------------------------------------------------


class TestToolSpanDecoratorConcurrency:
    """Concurrent calls to a @tool_span-decorated function must not share state."""

    async def test_concurrent_calls_produce_independent_spans(self, otel_provider):
        """Two concurrent invocations of a @tool_span decorated function must each
        create their own span and not interfere with each other's lifecycle."""

        # Use a barrier to force both coroutines to overlap in time
        barrier = asyncio.Barrier(2)
        errors: list[Exception] = []

        @tool_span("concurrent_tool", butler_name="test-butler")
        async def concurrent_tool(label: str) -> str:
            # Both coroutines must enter the span body before either exits
            await barrier.wait()
            return label

        async def run(label: str) -> None:
            try:
                result = await concurrent_tool(label)
                assert result == label
            except Exception as exc:
                errors.append(exc)

        await asyncio.gather(run("A"), run("B"))

        assert errors == [], f"Concurrent tool_span calls raised errors: {errors}"

        # Both spans must have been recorded
        spans = otel_provider.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "butler.tool.concurrent_tool"]
        assert len(tool_spans) == 2

    async def test_concurrent_spans_have_correct_status(self, otel_provider):
        """Each concurrent span must report the correct status (OK, not ERROR from
        the other coroutine's exception or detach mismatch)."""
        barrier = asyncio.Barrier(2)

        @tool_span("status_tool", butler_name="test-butler")
        async def status_tool(label: str) -> str:
            await barrier.wait()
            return label

        await asyncio.gather(status_tool("X"), status_tool("Y"))

        spans = otel_provider.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "butler.tool.status_tool"]
        assert len(tool_spans) == 2
        for s in tool_spans:
            # Neither span should be in ERROR state from a detach mismatch
            assert s.status.status_code != trace.StatusCode.ERROR, (
                f"Span unexpectedly in ERROR state: {s.status.description}"
            )

    async def test_concurrent_exceptions_are_isolated(self, otel_provider):
        """An exception in one concurrent call must only mark that span ERROR,
        not the other span."""
        barrier = asyncio.Barrier(2)

        @tool_span("maybe_fail_tool", butler_name="test-butler")
        async def maybe_fail_tool(should_fail: bool) -> str:
            await barrier.wait()
            if should_fail:
                raise ValueError("intentional failure")
            return "ok"

        results = await asyncio.gather(
            maybe_fail_tool(False),
            maybe_fail_tool(True),
            return_exceptions=True,
        )

        # One succeeded, one failed
        ok_results = [r for r in results if r == "ok"]
        err_results = [r for r in results if isinstance(r, ValueError)]
        assert len(ok_results) == 1
        assert len(err_results) == 1

        spans = otel_provider.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "butler.tool.maybe_fail_tool"]
        assert len(tool_spans) == 2

        error_spans = [s for s in tool_spans if s.status.status_code == trace.StatusCode.ERROR]
        ok_spans = [s for s in tool_spans if s.status.status_code != trace.StatusCode.ERROR]
        # Exactly one span should be ERROR
        assert len(error_spans) == 1
        assert len(ok_spans) == 1


# ---------------------------------------------------------------------------
# Bug 2: Cross-session trace contamination via global _active_session_context
# ---------------------------------------------------------------------------


class TestCrossSessionTraceIsolation:
    """tool spans must parent to their own session span, even under concurrent sessions."""

    async def test_concurrent_sessions_have_independent_contexts(self, otel_provider):
        """When two sessions run concurrently, tool spans in each session must be
        parented to their own session span, not the other session's span."""
        tracer = trace.get_tracer("test")
        barrier = asyncio.Barrier(2)

        # Records which session context was active during each tool call
        captured_session_ids: dict[str, int | None] = {}

        async def session_task(session_label: str) -> None:
            """Simulate a Spawner session: set active context, yield, call tool_span."""
            # Start a unique session span for this session
            session_span = tracer.start_span(f"butler.llm_session.{session_label}")
            session_ctx = trace.set_span_in_context(session_span)
            token = trace.context_api.attach(session_ctx)
            set_active_session_context(trace.context_api.get_current())
            trace.context_api.detach(token)

            # Yield so the other session can also call set_active_session_context
            await barrier.wait()

            # Now use tool_span — it should pick up THIS session's context,
            # not the other session's context
            with tool_span("session_tool", butler_name="test-butler") as span:
                captured_session_ids[session_label] = span.parent.span_id if span.parent else None

            session_span.end()
            clear_active_session_context()

        await asyncio.gather(
            session_task("alpha"),
            session_task("beta"),
        )

        spans = otel_provider.get_finished_spans()
        session_alpha = next(s for s in spans if s.name == "butler.llm_session.alpha")
        session_beta = next(s for s in spans if s.name == "butler.llm_session.beta")

        # The session span IDs must be different
        assert session_alpha.context.span_id != session_beta.context.span_id

        # Each tool span's parent must match its own session span
        tool_spans = [s for s in spans if s.name == "butler.tool.session_tool"]
        assert len(tool_spans) == 2

        # Build a map from session span_id to tool span parent
        session_span_ids = {
            session_alpha.context.span_id,
            session_beta.context.span_id,
        }

        for ts in tool_spans:
            assert ts.parent is not None, "Tool span must have a parent (session span)"
            assert ts.parent.span_id in session_span_ids, (
                f"Tool span parent {ts.parent.span_id!r} is not a known session span"
            )

        # Crucially: the two tool spans must NOT both parent to the same session span
        parent_ids = {ts.parent.span_id for ts in tool_spans}
        assert len(parent_ids) == 2, (
            f"Both tool spans have the same parent {parent_ids!r} — "
            "cross-session trace contamination detected"
        )

    async def test_context_isolation_survives_interleaved_set_calls(self, otel_provider):
        """Even if two coroutines call set_active_session_context in an interleaved
        fashion, each one must see its own context, not the globally last-set value."""
        tracer = trace.get_tracer("test")

        session_a_span = tracer.start_span("butler.llm_session.a")
        session_b_span = tracer.start_span("butler.llm_session.b")

        ctx_a = trace.set_span_in_context(session_a_span)
        ctx_b = trace.set_span_in_context(session_b_span)

        barrier_after_set = asyncio.Barrier(2)
        barrier_before_tool = asyncio.Barrier(2)

        parent_ids_seen: dict[str, int | None] = {}

        async def run_session(label: str, ctx, session_span) -> None:
            set_active_session_context(ctx)
            await barrier_after_set.wait()
            # Both sessions have now set their context; the global may have
            # been overwritten by the other task. We now use tool_span.
            await barrier_before_tool.wait()
            with tool_span("interleaved_tool", butler_name="test-butler") as span:
                parent_ids_seen[label] = span.parent.span_id if span.parent else None
            session_span.end()
            clear_active_session_context()

        await asyncio.gather(
            run_session("a", ctx_a, session_a_span),
            run_session("b", ctx_b, session_b_span),
        )

        spans = otel_provider.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "butler.tool.interleaved_tool"]
        assert len(tool_spans) == 2

        parent_ids = {ts.parent.span_id for ts in tool_spans if ts.parent}
        assert len(parent_ids) == 2, (
            f"Cross-session contamination: tool spans share a parent {parent_ids!r}"
        )
