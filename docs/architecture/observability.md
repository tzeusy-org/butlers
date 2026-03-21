# Observability

> **Purpose:** Describes the OpenTelemetry integration for distributed tracing, metrics, and trace propagation across butler processes.
> **Audience:** Developers instrumenting new tools or modules, operators configuring observability infrastructure, SREs debugging latency or failures.
> **Prerequisites:** [System Topology](system-topology.md), [Butler Daemon](butler-daemon.md).

## Overview

Butlers uses OpenTelemetry (OTel) for distributed tracing and metrics. Traces and metrics are exported via OTLP HTTP to a Grafana Alloy instance, which forwards them to Grafana Tempo (traces) and Prometheus-compatible storage (metrics). When the `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable is not set, both tracing and metrics fall back to no-op providers — all recording calls become silent, so butlers run correctly without any observability backend.

## Initialization

Telemetry is initialized early in the daemon startup sequence (Phase 2). Two functions handle setup:

### Trace Provider (`init_telemetry`)

`init_telemetry(service_name)` in `src/butlers/core/telemetry.py` creates a `TracerProvider` with a `BatchSpanProcessor` and `OTLPSpanExporter`. The exporter targets `{OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces`.

A guard flag (`_tracer_provider_installed`) ensures the global `TracerProvider` is set only once, even when multiple butlers run in the same process. Subsequent calls reuse the existing provider and return a tracer scoped to the butler's service name.

### Meter Provider (`init_metrics`)

`init_metrics(service_name)` in `src/butlers/core/metrics.py` creates a `MeterProvider` with a `PeriodicExportingMetricReader` and `OTLPMetricExporter`. The exporter targets `{OTEL_EXPORTER_OTLP_ENDPOINT}/v1/metrics` with a 15-second export interval.

The same guard-flag pattern prevents duplicate provider installation.

### Resource Attributes

Both providers share a common `Resource` built by `_build_resource()`:

- `service.name` — always `"butlers"`
- `deployment.environment` — read from the `ENV` environment variable (e.g., `prod`, `dev`). Omitted when unset.

Per-butler attribution is handled at the span and metric level (via `butler.name` and `service.name` span attributes and `butler` metric labels), not at the resource level.

## Trace Propagation

A critical design challenge is maintaining trace continuity across process boundaries. Each butler daemon spawns ephemeral LLM CLI instances (Claude Code, Codex, Gemini) as subprocesses. These subprocesses call back to the butler's MCP tools via HTTP, creating new async tasks that don't inherit the parent's contextvars-based OTel context.

### Traceparent Environment Variable

When the spawner invokes a runtime instance, it injects the current trace context as a `TRACEPARENT` environment variable:

```python
def get_traceparent_env() -> dict[str, str]:
    carrier: dict[str, str] = {}
    inject(carrier)
    traceparent = carrier.get("traceparent")
    if traceparent:
        return {"TRACEPARENT": traceparent}
    return {}
```

This allows the spawned LLM CLI to continue the same trace, linking its work as child spans of the butler's session span.

### Active Session Context

When the LLM CLI calls MCP tools back via HTTP, those tool handlers run in separate async tasks (created by uvicorn/FastMCP) that don't inherit the spawner task's contextvars. Without intervention, each tool call would create a root span with a new trace ID.

The fix uses a `ContextVar` to store the active session's OTel `Context`:

1. Before invoking the runtime, the spawner calls `set_active_session_context(ctx)` to store the current OTel context.
2. When `tool_span` creates a span for an MCP tool handler, it calls `get_active_session_context()` and uses the returned context as the span's parent.
3. After the session ends, `clear_active_session_context()` is called.

Using a `ContextVar` (rather than a plain module-level variable) prevents cross-session trace contamination when `max_concurrent_sessions > 1`. Each spawner asyncio Task inherits its own copy of the ContextVar.

### Cross-Butler Trace Context

For routed requests, trace context is propagated through the `route.v1` envelope's `request_context.trace_context` field. The Switchboard injects W3C Trace Context headers, and target butlers extract them to establish parent-child span relationships across service boundaries.

## Span Instrumentation

### tool_span

The `tool_span` class (`src/butlers/core/telemetry.py`) is the primary instrumentation primitive. It can be used as a context manager or decorator:

```python
# Context manager
with tool_span("state_get", butler_name="switchboard"):
    ...

# Decorator
@tool_span("state_get", butler_name="switchboard")
async def handle_state_get(key: str):
    ...
```

Each span is named `butler.tool.<tool_name>` and carries:
- `butler.name` — the short butler name (e.g., `"switchboard"`)
- `service.name` — `butler.<name>` for per-butler attribution in observability backends

Exceptions are recorded on the span with full stack traces, and the span status is set to ERROR before re-raising.

The decorator creates a fresh `tool_span` instance for every invocation, which is critical for concurrency safety — reusing the decorator object would share `_span` and `_token` state across concurrent async calls.

### tag_butler_span

For spans created outside of `tool_span`, the `tag_butler_span(span, butler_name)` helper sets the same `butler.name` and `service.name` attributes.

## Metrics

The `ButlerMetrics` class (`src/butlers/core/metrics.py`) provides a per-butler wrapper around OpenTelemetry instruments. All instruments are lazily created from the global `MeterProvider`, so it's safe to construct before `init_metrics` is called.

### Instrument Catalog

**Spawner metrics:**
- `butlers.spawner.active_sessions` (UpDownCounter) — concurrent sessions per butler
- `butlers.spawner.queued_triggers` (UpDownCounter) — triggers waiting for the per-butler semaphore
- `butlers.spawner.global_queue_depth` (UpDownCounter) — triggers waiting for the global concurrency cap
- `butlers.spawner.session_duration_ms` (Histogram) — end-to-end session duration
- `butlers.spawner.input_tokens` (Counter) — LLM input tokens per session (labels: butler, model)
- `butlers.spawner.output_tokens` (Counter) — LLM output tokens per session (labels: butler, model)

**Buffer metrics:**
- `butlers.buffer.queue_depth` (UpDownCounter) — in-memory queue depth
- `butlers.buffer.enqueue_total` (Counter) — enqueue events (label: path=hot|cold)
- `butlers.buffer.backpressure_total` (Counter) — queue-full events
- `butlers.buffer.scanner_recovered_total` (Counter) — scanner recoveries
- `butlers.buffer.process_latency_ms` (Histogram) — queue wait time
- `butlers.switchboard.queue.dequeue_by_tier` (Counter) — dequeues by policy tier

**Route metrics:**
- `butlers.route.accept_latency_ms` (Histogram) — route.execute accept phase duration
- `butlers.route.queue_depth` (UpDownCounter) — accepted-but-unprocessed route requests
- `butlers.route.process_latency_ms` (Histogram) — inbox acceptance to processing start

**Scheduler metrics:**
- `butlers.scheduler.tasks_dispatched` (Counter) — tasks dispatched (labels: butler, task_name, outcome)

**Switchboard metrics:**
- `butlers.switchboard.ingest_result` (Counter) — ingest boundary outcomes (labels: source, outcome)

### Cardinality Discipline

All metrics use low-cardinality attributes only. Required attributes include `butler`, `tool_name`, `outcome`, `trigger_source`, `error_class`, and `source_channel` where relevant. High-cardinality identifiers such as `request_id`, raw sender identities, thread IDs, and message text are never used as metric attributes.

### Gauge Registration

On startup, `ButlerMetrics.ensure_registered()` emits zero-value adds on key UpDownCounters so that idle butlers appear in Prometheus/Grafana variable queries like `label_values(..., butler)`.

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | (unset) | OTLP HTTP base endpoint (e.g., `http://alloy:4318`). When unset, tracing and metrics are no-op. |
| `ENV` | No | (unset) | Sets `deployment.environment` resource attribute for environment-scoped dashboards. |

## Related Pages

- [System Topology](system-topology.md) — where the observability pipeline fits
- [Butler Daemon](butler-daemon.md) — telemetry initialization during startup
- [Spawner](../runtime/spawner.md) — trace context injection into spawned processes
- [Session Lifecycle](../runtime/session-lifecycle.md) — session-level trace correlation
