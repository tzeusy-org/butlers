# Grafana Monitoring

> **Purpose:** Document the observability stack: OpenTelemetry instrumentation, trace propagation, span architecture, and Grafana integration.
> **Audience:** Operators monitoring Butlers in production, developers debugging performance issues.
> **Prerequisites:** [Docker Deployment](docker-deployment.md), [Environment Config](environment-config.md).

## Overview

Butlers uses OpenTelemetry (OTel) for distributed tracing and metrics, with a Grafana LGTM stack (Loki, Grafana, Tempo, Mimir) as the observability backend. When `OTEL_EXPORTER_OTLP_ENDPOINT` is configured, all butler daemons and the dashboard API emit traces via OTLP HTTP to a Grafana Alloy collector (port 4318), which forwards them to Grafana Tempo. When unset, telemetry falls back to no-op providers with zero overhead.

## Telemetry Initialization

The `init_telemetry(service_name)` function in `src/butlers/core/telemetry.py` sets up the OpenTelemetry `TracerProvider`:

1. Checks for `OTEL_EXPORTER_OTLP_ENDPOINT` in the environment.
2. If present, creates a `TracerProvider` with an OTLP HTTP span exporter targeting `{endpoint}/v1/traces`.
3. Installs a `BatchSpanProcessor` for efficient batched export.
4. Registers the provider globally via `trace.set_tracer_provider()`.

The provider is installed once per process. A guard flag (`_tracer_provider_installed`) prevents "Overriding of current TracerProvider" warnings when multiple butlers initialize in the same process. Subsequent calls reuse the existing provider and return a correctly-named tracer.

## Span Architecture

### Butler Attribution

Every span carries two key attributes:
- **`butler.name`** -- The short butler name (e.g., `"switchboard"`, `"health"`).
- **`service.name`** -- Formatted as `butler.{name}` for backend attribution.

These are set via `tag_butler_span(span, butler_name)` and enable per-butler filtering in Grafana dashboards.

### Tool Spans

The `tool_span` context manager/decorator creates spans for MCP tool invocations:

```python
with tool_span("state_get", butler_name="switchboard"):
    ...

@tool_span("state_get", butler_name="switchboard")
async def handle_state_get(key: str):
    ...
```

- Span name: `butler.tool.<tool_name>` (e.g., `butler.tool.email_search`)
- Automatically records exceptions with full stack traces and sets span status to ERROR
- Concurrency-safe: each decorator invocation creates a fresh `tool_span` instance, preventing bugs when multiple async calls share the same decorator object.

### Session Context Propagation

When a butler spawns an LLM CLI runtime, the runtime calls MCP tools back via HTTP. These HTTP handlers run in separate async tasks that do not inherit the spawner's OTel context. The `set_active_session_context()` / `get_active_session_context()` mechanism bridges this gap using a `ContextVar`, ensuring tool spans are correctly parented to the session span. The `ContextVar` approach prevents cross-session trace contamination when `max_concurrent_sessions > 1`.

### Cross-Process Trace Propagation

W3C Trace Context is used for cross-process propagation:

- **`inject_trace_context()`** -- Serializes current context into a dict with `traceparent` key.
- **`extract_trace_context()`** -- Deserializes a carrier dict back into an OTel Context.
- **`get_traceparent_env()`** -- Returns `{"TRACEPARENT": "..."}` for spawned subprocess environments.
- **`extract_trace_from_args()`** -- Extracts `_trace_context` from MCP tool call kwargs.

## FastAPI Instrumentation

When `OTEL_EXPORTER_OTLP_ENDPOINT` is set, the dashboard API automatically instruments FastAPI via `opentelemetry.instrumentation.fastapi.FastAPIInstrumentor`. This adds spans for every HTTP request, including route, method, status code, and latency.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | -- | OTLP HTTP endpoint (e.g., `http://alloy:4318`). When unset, all telemetry is no-op. |

In Docker Compose, butler services use:
```yaml
OTEL_EXPORTER_OTLP_ENDPOINT: http://otel.parrot-hen.ts.net:4318
```

## Related Pages

- [Docker Deployment](docker-deployment.md) -- Service configuration including OTLP endpoints
- [Troubleshooting](troubleshooting.md) -- Debugging with traces
- [Dashboard API](../api_and_protocols/dashboard-api.md) -- FastAPI instrumentation details
- [Session Lifecycle](../runtime/session-lifecycle.md) -- Sessions carry `trace_id` for correlation
