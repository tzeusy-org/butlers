"""OpenTelemetry initialization, span wrappers, and trace context propagation for butler daemons."""

from __future__ import annotations

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


def init_telemetry(service_name: str) -> trace.Tracer:
    """
    Initialize OpenTelemetry tracing for a butler daemon.

    When OTEL_EXPORTER_OTLP_ENDPOINT is set, configures a real TracerProvider
    with OTLP gRPC exporter. Otherwise, returns a no-op tracer.

    Args:
        service_name: The butler's service name for tracing (e.g., "butler-switchboard")

    Returns:
        A Tracer instance (real or no-op depending on config)
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set, using no-op tracer")
        return trace.get_tracer(service_name)

    # Import exporter only when needed
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)
    logger.info("Telemetry initialized: service=%s, endpoint=%s", service_name, endpoint)

    return trace.get_tracer(service_name)


def get_tracer(name: str) -> trace.Tracer:
    """Get a tracer from the current provider (useful for modules)."""
    return trace.get_tracer(name)


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

    The span is named ``butler.tool.<tool_name>`` and carries a ``butler.name``
    attribute.  Exceptions are recorded on the span with a full stack trace and
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
        self._span = tracer.start_span(self._span_name)
        self._span.set_attribute("butler.name", self._butler_name)
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
        @functools.wraps(func)
        async def _wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            with self:
                return await func(*args, **kwargs)

        return _wrapper


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

    Suitable for passing as environment variables to spawned CC instances so
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
