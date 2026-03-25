# RFC 0005: Observability and Telemetry

**Status:** Accepted
**Date:** 2026-03-24

## Summary

Butlers uses OpenTelemetry for distributed tracing and metrics, exported via OTLP HTTP to Grafana Alloy, which forwards traces to Tempo and metrics to Prometheus-compatible storage. Trace continuity across process boundaries is maintained through three mechanisms: `TRACEPARENT` environment variable injection into spawned LLM processes, `ContextVar`-based active session context for MCP tool span parenting, and cross-butler trace context propagation via route envelope fields. All tools are instrumented via the `tool_span` primitive. A metrics catalog of counters, histograms, and UpDownCounters covers spawner, buffer, route, scheduler, and switchboard domains with strict low-cardinality discipline.

## Motivation

Butlers spawns ephemeral LLM CLI processes that call back to MCP tools via HTTP, creating async tasks that do not inherit the parent's contextvars-based OTel context. Without explicit trace propagation, each tool call would create a disconnected root span, making distributed debugging impossible. The metrics catalog must provide actionable operational signals (queue depths, session durations, token consumption) without creating cardinality explosions that degrade the metrics backend.

## Design

### Initialization

Telemetry is initialized during daemon startup phase 2 (see RFC 0001). Two functions handle setup:

**Trace Provider (`init_telemetry`):**

`init_telemetry(service_name)` creates a `TracerProvider` with a `BatchSpanProcessor` and `OTLPSpanExporter` targeting `{OTEL_EXPORTER_OTLP_ENDPOINT}/v1/traces`.

A guard flag (`_tracer_provider_installed`) ensures the global `TracerProvider` is set only once, even when multiple butlers run in the same process. Subsequent calls reuse the existing provider and return a tracer scoped to the butler's service name.

**Meter Provider (`init_metrics`):**

`init_metrics(service_name)` creates a `MeterProvider` with a `PeriodicExportingMetricReader` and `OTLPMetricExporter` targeting `{OTEL_EXPORTER_OTLP_ENDPOINT}/v1/metrics`. Export interval: 15 seconds.

The same guard-flag pattern prevents duplicate provider installation.

**No-op fallback:** When `OTEL_EXPORTER_OTLP_ENDPOINT` is not set, both tracing and metrics fall back to no-op providers. All recording calls become silent. Butlers run correctly without any observability backend.

### Resource Attributes

Both providers share a common `Resource`:

| Attribute | Value | Source |
|-----------|-------|--------|
| `service.name` | `"butlers"` | Hardcoded |
| `deployment.environment` | `"prod"`, `"dev"`, etc. | `ENV` environment variable (omitted when unset) |

Per-butler attribution is handled at the span and metric level via `butler.name` and `service.name` attributes, not at the resource level. This avoids creating separate TSDB series per butler at the resource level.

### Trace Propagation

Three mechanisms maintain trace continuity:

#### 1. TRACEPARENT Environment Variable

When the Spawner invokes a runtime instance, it injects the current trace context as a `TRACEPARENT` environment variable:

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

#### 2. Active Session Context (ContextVar)

When the LLM CLI calls MCP tools back via HTTP, those handlers run in separate async tasks (created by uvicorn/FastMCP) that do not inherit the spawner task's contextvars. The fix:

1. Before invoking the runtime, the Spawner calls `set_active_session_context(ctx)` to store the current OTel `Context` in a `ContextVar`.
2. When `tool_span` creates a span for an MCP tool handler, it calls `get_active_session_context()` and uses the returned context as the span's parent.
3. After the session ends, `clear_active_session_context()` is called.

Using a `ContextVar` (rather than a plain module-level variable) prevents cross-session trace contamination when `max_concurrent_sessions > 1`. Each spawner asyncio Task inherits its own copy.

#### 3. Cross-Butler Trace Context

For routed requests (RFC 0003), trace context propagates through the route envelope's `request_context.trace_context` field. The Switchboard injects W3C Trace Context headers at routing time. Target butlers extract them via `extract_trace_context()` to establish parent-child span relationships across service boundaries.

### tool_span Instrumentation

The `tool_span` class (`src/butlers/core/telemetry.py`) is the primary instrumentation primitive. It can be used as a context manager or decorator:

```python
with tool_span("state_get", butler_name="switchboard"):
    ...

@tool_span("state_get", butler_name="switchboard")
async def handle_state_get(key: str):
    ...
```

Each span:

- Named `butler.tool.<tool_name>`
- Carries `butler.name` attribute (short butler name)
- Carries `service.name` attribute (`butler.<name>` for per-butler attribution)
- Records exceptions with full stack traces; sets span status to ERROR before re-raising
- When used as a decorator, creates a fresh `tool_span` instance for every invocation (critical for concurrency safety -- reusing the decorator object would share `_span` and `_token` state across concurrent async calls)

The `tag_butler_span(span, butler_name)` helper sets the same attributes on spans created outside of `tool_span`.

### Metrics Catalog

The `ButlerMetrics` class (`src/butlers/core/metrics.py`) provides a per-butler wrapper around OTel instruments. All instruments are lazily created from the global `MeterProvider`, so construction before `init_metrics` is safe.

#### Spawner Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.spawner.active_sessions` | UpDownCounter | `butler` | Concurrent sessions per butler |
| `butlers.spawner.queued_triggers` | UpDownCounter | `butler` | Triggers waiting for per-butler semaphore |
| `butlers.spawner.global_queue_depth` | UpDownCounter | `butler` | Triggers waiting for global concurrency cap |
| `butlers.spawner.session_duration_ms` | Histogram | `butler` | End-to-end session duration |
| `butlers.spawner.input_tokens` | Counter | `butler`, `model` | LLM input tokens per session |
| `butlers.spawner.output_tokens` | Counter | `butler`, `model` | LLM output tokens per session |

#### Buffer Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.buffer.queue_depth` | UpDownCounter | `butler` | In-memory queue depth |
| `butlers.buffer.enqueue_total` | Counter | `butler`, `path` (`hot`/`cold`) | Enqueue events |
| `butlers.buffer.backpressure_total` | Counter | `butler` | Queue-full events |
| `butlers.buffer.scanner_recovered_total` | Counter | `butler` | Scanner recoveries |
| `butlers.buffer.process_latency_ms` | Histogram | `butler` | Queue wait time |
| `butlers.switchboard.queue.dequeue_by_tier` | Counter | `policy_tier`, `queue_name`, `starvation_override` | Dequeues by priority tier |

#### Route Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.route.accept_latency_ms` | Histogram | `butler` | route.execute accept phase duration |
| `butlers.route.queue_depth` | UpDownCounter | `butler` | Accepted-but-unprocessed route requests |
| `butlers.route.process_latency_ms` | Histogram | `butler` | Inbox acceptance to processing start |

#### Scheduler Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.scheduler.tasks_dispatched` | Counter | `butler`, `task_name`, `outcome` | Tasks dispatched |

#### Switchboard Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.switchboard.ingest_result` | Counter | `source`, `outcome` | Ingest boundary outcomes |
| `butlers.switchboard.thread_affinity.hit` | Counter | `destination_butler` | Thread affinity routing hit |
| `butlers.switchboard.thread_affinity.miss` | Counter | `reason` | Thread affinity miss (reason: `no_thread_id`, `no_history`, `conflict`, `disabled`, `error`) |
| `butlers.switchboard.thread_affinity.stale` | Counter | -- | Historical match outside TTL |
| `butlers.switchboard.triage.rule_matched` | Counter | `rule_type`, `action`, `source_channel` | Triage rule match |
| `butlers.switchboard.triage.pass_through` | Counter | `source_channel`, `reason` | Triage pass-through |
| `butlers.switchboard.triage.evaluation_latency_ms` | Histogram | `result` | End-to-end triage latency |

### Cardinality Discipline

All metrics MUST use low-cardinality attributes only. Permitted attributes: `butler`, `tool_name`, `outcome`, `trigger_source`, `error_class`, `source_channel`, `policy_tier`, `model`, `task_name`, `rule_type`, `action`, `reason`, `result`, `path`, `queue_name`, `starvation_override`, `destination_butler`.

The following MUST NEVER be used as metric attributes: `request_id`, raw sender identities, `thread_id`, message text, session IDs, UUIDs, timestamps, or any other high-cardinality identifier.

### Gauge Registration

On startup, `ButlerMetrics.ensure_registered()` emits zero-value adds on key UpDownCounters so that idle butlers appear in Prometheus/Grafana variable queries like `label_values(..., butler)`.

### Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | (unset) | OTLP HTTP base endpoint. When unset, tracing and metrics are no-op. |
| `ENV` | No | (unset) | Sets `deployment.environment` resource attribute. |
| `BUTLERS_MAX_GLOBAL_SESSIONS` | No | `3` | Global concurrency cap (affects queue depth metrics). |

## Integration

- **RFC 0001:** Telemetry initialized at phase 2. Spawner metrics track concurrency and session lifecycle.
- **RFC 0002:** All tool registrations pass through the logging proxy, which creates OTel spans via `tool_span`.
- **RFC 0003:** Trace context propagates through route envelopes. Switchboard metrics cover ingestion, triage, and thread affinity.
- **RFC 0007:** The dashboard traces page provides a distributed trace index and span waterfall visualization.

## Alternatives Considered

**Module-level variable for active session context.** Rejected because a plain global variable would cause cross-session trace contamination when `max_concurrent_sessions > 1`. `ContextVar` provides per-task isolation.

**Per-butler resource attributes.** Rejected because creating separate TSDB series at the resource level for each butler multiplies storage cost. Per-butler attribution at the span/metric attribute level achieves the same observability without resource-level fan-out.

**Trace sampling.** Not implemented. Current session volumes (dozens per day per butler) do not warrant sampling. All traces are exported. This decision SHOULD be revisited if session volumes increase by an order of magnitude.
