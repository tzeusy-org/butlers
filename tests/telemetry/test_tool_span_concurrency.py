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


class TestToolSpanDecoratorConcurrency:
    """Concurrent calls to a @tool_span-decorated function must not share state."""

    async def test_concurrent_calls_and_status(self, otel_provider):
        """Two concurrent calls each get their own span; neither should be ERROR."""
        barrier = asyncio.Barrier(2)
        errors: list[Exception] = []

        @tool_span("concurrent_tool", butler_name="test-butler")
        async def concurrent_tool(label: str) -> str:
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
        spans = otel_provider.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "butler.tool.concurrent_tool"]
        assert len(tool_spans) == 2
        for s in tool_spans:
            assert s.status.status_code != trace.StatusCode.ERROR

    async def test_concurrent_exceptions_are_isolated(self, otel_provider):
        """An exception in one concurrent call must only mark that span ERROR."""
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

        assert len([r for r in results if r == "ok"]) == 1
        assert len([r for r in results if isinstance(r, ValueError)]) == 1

        spans = otel_provider.get_finished_spans()
        tool_spans = [s for s in spans if s.name == "butler.tool.maybe_fail_tool"]
        error_spans = [s for s in tool_spans if s.status.status_code == trace.StatusCode.ERROR]
        ok_spans = [s for s in tool_spans if s.status.status_code != trace.StatusCode.ERROR]
        assert len(error_spans) == 1
        assert len(ok_spans) == 1


class TestCrossSessionTraceIsolation:
    """tool spans must parent to their own session span, even under concurrent sessions."""

    async def test_concurrent_sessions_have_independent_contexts(self, otel_provider):
        """Tool spans in concurrent sessions parent to their own session span."""
        tracer = trace.get_tracer("test")
        barrier = asyncio.Barrier(2)

        async def session_task(session_label: str) -> None:
            session_span = tracer.start_span(f"butler.llm_session.{session_label}")
            session_ctx = trace.set_span_in_context(session_span)
            token = trace.context_api.attach(session_ctx)
            set_active_session_context(trace.context_api.get_current())
            trace.context_api.detach(token)
            await barrier.wait()

            with tool_span("session_tool", butler_name="test-butler"):
                pass

            session_span.end()
            clear_active_session_context()

        await asyncio.gather(session_task("alpha"), session_task("beta"))

        spans = otel_provider.get_finished_spans()
        session_alpha = next(s for s in spans if s.name == "butler.llm_session.alpha")
        session_beta = next(s for s in spans if s.name == "butler.llm_session.beta")
        assert session_alpha.context.span_id != session_beta.context.span_id

        tool_spans = [s for s in spans if s.name == "butler.tool.session_tool"]
        assert len(tool_spans) == 2

        session_span_ids = {session_alpha.context.span_id, session_beta.context.span_id}
        for ts in tool_spans:
            assert ts.parent is not None
            assert ts.parent.span_id in session_span_ids

        parent_ids = {ts.parent.span_id for ts in tool_spans}
        assert len(parent_ids) == 2, "Cross-session trace contamination detected"
