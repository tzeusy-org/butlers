"""OpenTelemetry initialization, span wrappers, and trace context propagation for butler daemons."""

from __future__ import annotations

import contextvars
import functools
import logging
import os
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_TRACER_NAME = "butlers"

# Guard flag: True once the global TracerProvider has been installed.
# Prevents "Overriding of current TracerProvider is not allowed" warnings
# when multiple butlers call init_telemetry() in the same process.
_tracer_provider_installed: bool = False


def init_telemetry(service_name: str) -> trace.Tracer:
    """
    Initialize OpenTelemetry tracing for a butler daemon.

    When OTEL_EXPORTER_OTLP_ENDPOINT is set, configures a real TracerProvider
    with OTLP gRPC exporter on the first call. Subsequent calls (for additional
    butlers in the same process) reuse the existing provider and return a
    correctly-named tracer without triggering provider-override warnings.

    Each butler's spans carry ``service.name`` and ``butler.name`` span
    attributes so that observability backends can distinguish per-butler
    telemetry even though all butlers share a single TracerProvider.

    Args:
        service_name: The butler's service name for tracing (e.g., "butler-switchboard")

    Returns:
        A Tracer instance (real or no-op depending on config)
    """
    global _tracer_provider_installed

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set, using no-op tracer")
        return trace.get_tracer(service_name)

    if _tracer_provider_installed:
        # Provider already set by an earlier butler in this process.
        # Return a tracer using the service_name as instrumentation scope name.
        # Spans will carry a "service.name" span attribute for backend attribution.
        logger.debug(
            "TracerProvider already initialized; reusing existing provider for service=%s",
            service_name,
        )
        return trace.get_tracer(service_name)

    # Import exporter only when needed
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": "butlers"})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    _tracer_provider_installed = True
    logger.info("Telemetry initialized: endpoint=%s", endpoint)

    return trace.get_tracer(service_name)


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer from the current provider (useful for modules)."""
    return trace.get_tracer(name)


def tag_butler_span(span: trace.Span, butler_name: str) -> None:
    """Set butler attribution attributes on a span.

    Sets both butler.name (for filtering in dashboards) and
    service.name (for per-butler attribution in observability backends
    when all butlers share a single process-level TracerProvider resource).

    Use this helper everywhere a span is created outside of tool_span.

    Args:
        span: The span to annotate.
        butler_name: Short butler name (e.g. "switchboard", "finance").
    """
    span.set_attribute("butler.name", butler_name)
    span.set_attribute("service.name", f"butler.{butler_name}")


# ---------------------------------------------------------------------------
# Span wrappers (butlers-0qp.15.2)
# ---------------------------------------------------------------------------


class tool_span:
    """Create an OpenTelemetry span for an MCP tool invocation.

    Can be used as a **context manager** or as a **decorator** on async functions.

    Context manager usage::

        with tool_span("state_get", butler_name="switchboard"):
            ...

    Decorator usage::

        @tool_span("state_get", butler_name="switchboard")
        async def handle_state_get(key: str):
            ...

    The span is named ``butler.tool.<tool_name>`` and carries ``butler.name``
    and ``service.name`` attributes so that observability backends correctly
    attribute spans to their originating butler even when multiple butlers
    share a single TracerProvider in the same process.

    Exceptions are recorded on the span with a full stack trace and
    the span status is set to ERROR before the exception is re-raised.
    """

    def __init__(self, tool_name: str, *, butler_name: str) -> None:
        self._tool_name = tool_name
        self._butler_name = butler_name
        self._span_name = f"butler.tool.{tool_name}"
        # For context-manager use
        self._span: trace.Span | None = None
        self._token: object | None = None

    # -- context manager protocol ------------------------------------------

    def __enter__(self) -> trace.Span:
        from butlers.core.logging import set_butler_context

        tracer = trace.get_tracer(_TRACER_NAME)
        # Use the active session context as parent when available.  This
        # ensures tool spans started in HTTP handler tasks (which don't
        # inherit contextvars) still parent to the butler.llm_session span.
        # When get_active_session_context() returns None, start_span falls
        # back to the current contextvars context (unchanged default behavior).
        self._span = tracer.start_span(self._span_name, context=get_active_session_context())
        tag_butler_span(self._span, self._butler_name)
        self._token = trace.context_api.attach(trace.set_span_in_context(self._span))
        # Ensure butler context is correct for multi-butler mode
        set_butler_context(self._butler_name)
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        if self._span is None:
            return
        if exc_val is not None:
            self._span.set_status(trace.StatusCode.ERROR, str(exc_val))
            self._span.record_exception(exc_val)
        self._span.end()
        if self._token is not None:
            trace.context_api.detach(self._token)

    # -- decorator protocol ------------------------------------------------

    def __call__(self, func):  # noqa: ANN001, ANN204
        # Capture the constructor args so each invocation creates a fresh
        # tool_span instance.  This is the critical fix for the concurrency
        # bug: the original ``with self:`` reused the single decorator object,
        # so concurrent async calls shared ``self._span`` and ``self._token``,
        # causing OpenTelemetry "token was created in a different Context"
        # errors and double-end() calls on finished spans.
        tool_name = self._tool_name
        butler_name = self._butler_name

        @functools.wraps(func)
        async def _wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            # Create a fresh context manager for every invocation so that
            # concurrent calls each have their own _span / _token state.
            with tool_span(tool_name, butler_name=butler_name):
                return await func(*args, **kwargs)

        return _wrapper


# ---------------------------------------------------------------------------
# Active session context (cross-MCP-boundary trace propagation)
# ---------------------------------------------------------------------------
#
# When a butler spawns a Claude Code runtime, the runtime calls MCP tools
# back via HTTP.  These HTTP-dispatched tool handlers run in separate async
# tasks (created by uvicorn/FastMCP), so they do NOT inherit the spawner's
# contextvars-based OTel context.  Each tool_span would therefore create a
# root span with a new trace ID instead of being a child of the session span.
#
# Fix: the Spawner stores the active session's OTel Context here before
# invoking the runtime, and tool_span reads it back via get_active_session_context().
#
# A ContextVar is used (rather than a plain module-level variable) to prevent
# cross-session trace contamination when max_concurrent_sessions > 1 (the
# switchboard uses 3 concurrent sessions).  Each asyncio Task inherits its
# own copy of the ContextVar from the spawning task, so concurrent
# Spawner._run() coroutines each see their own independent session context.
#
# Note: HTTP handler tasks created by uvicorn/FastMCP are spawned from the
# event-loop root context (not from the spawner task), so they do NOT inherit
# the ContextVar value and will see the default (None).  This means tool_span
# in those handlers creates a root span — the same behaviour as before when
# no session context was set.  The concurrency race condition (where session B
# overwrote session A's global variable) is eliminated.

_active_session_context_var: contextvars.ContextVar[Context | None] = contextvars.ContextVar(
    "_active_session_context_var", default=None
)


def set_active_session_context(ctx: Context) -> None:
    """Store the OTel context of the active LLM session for tool_span to use.

    Uses a ContextVar so concurrent sessions each carry their own isolated
    value; no cross-session contamination when max_concurrent_sessions > 1.
    """
    _active_session_context_var.set(ctx)


def get_active_session_context() -> Context | None:
    """Return the active session's OTel context, or None if no session is running."""
    return _active_session_context_var.get()


def clear_active_session_context() -> None:
    """Clear the stored session context (called when the session ends)."""
    _active_session_context_var.set(None)


# ---------------------------------------------------------------------------
# Trace context propagation (butlers-0qp.15.3)
# ---------------------------------------------------------------------------


def inject_trace_context() -> dict[str, str]:
    """Inject the current trace context into a dict using W3C Trace Context format.

    Returns a dict that may contain a ``traceparent`` key (and optionally
    ``tracestate``).  The dict is suitable for inclusion in MCP args as
    ``_trace_context``.

    If there is no active valid span, the returned dict may be empty.
    """
    carrier: dict[str, str] = {}
    inject(carrier)
    return carrier


def extract_trace_context(trace_context_dict: dict[str, str]) -> Context:
    """Extract W3C trace context from a carrier dict.

    Returns an OpenTelemetry ``Context`` that can be passed as the ``context``
    argument when starting a new span, establishing a parent-child relationship
    across process boundaries.
    """
    return extract(trace_context_dict)


def get_traceparent_env() -> dict[str, str]:
    """Return a dict ``{"TRACEPARENT": "..."}`` for the current trace context.

    Suitable for passing as environment variables to spawned runtime instances so
    they can continue the same trace.

    If there is no active valid span, returns an empty dict.
    """
    carrier: dict[str, str] = {}
    inject(carrier)
    traceparent = carrier.get("traceparent")
    if traceparent:
        return {"TRACEPARENT": traceparent}
    return {}


def extract_trace_from_args(kwargs: dict) -> Context | None:
    """Extract trace context from tool call kwargs and remove _trace_context.

    If ``_trace_context`` is present in kwargs, extract it and return a Context
    that can be used as the parent context for a new span. The ``_trace_context``
    key is removed from kwargs as a side effect.

    Returns None if no _trace_context is present, allowing normal span creation.

    Args:
        kwargs: Tool call kwargs that may contain ``_trace_context``

    Returns:
        OpenTelemetry Context if trace context was found, None otherwise
    """
    trace_context_dict = kwargs.pop("_trace_context", None)
    if trace_context_dict:
        return extract_trace_context(trace_context_dict)
    return None
