# RFC 0005: Observability and Telemetry

**Status:** Accepted
**Date:** 2026-03-24
Governing doctrine: [v1.md, What v1 Is](../../heart-and-soul/v1.md#what-v1-is) (OpenTelemetry observability is a named v1 capability) and [vision.md rule 4, the daemon must be testable, debuggable and predictable](../../heart-and-soul/vision.md#non-negotiable-rules).

## Summary

Butlers uses OpenTelemetry for distributed tracing and metrics, exported via OTLP HTTP to an OpenTelemetry Collector (`otel-collector`), which forwards traces to Tempo and metrics to Prometheus via `remote_write`. Trace continuity across process boundaries is maintained through three mechanisms: `TRACEPARENT` environment variable injection into spawned LLM processes, `ContextVar`-based active session context for MCP tool span parenting, and cross-butler trace context propagation via route envelope fields. All tools are instrumented via the `tool_span` primitive. Higher-level recovery workflows emit phase-level spans and metrics, while admission-control decisions are tracked separately from launched execution failures. A metrics catalog of counters, histograms, and UpDownCounters covers spawner, buffer, route, scheduler, switchboard, and recovery domains with strict low-cardinality discipline.

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

### Workflow and Recovery Telemetry

Higher-level recovery workflows (for example self-healing and QA investigations) operate above individual runtime sessions. Each workflow phase SHOULD emit its own span with low-cardinality attributes such as `workflow` and `phase`, while higher-cardinality identifiers such as `attempt_id`, `session_id`, `request_id`, and `trace_id` MAY appear on spans or persisted evidence records.

Admission-control outcomes that occur before a runtime session launches MUST be recorded distinctly from execution failures. A cooldown rejection, circuit-breaker rejection, or concurrency-cap rejection is operationally important, but it is not evidence that an investigation session failed.

Structured evidence for workflow failures SHOULD be persisted outside the metrics stream. Examples include `session_id`, `request_id`, `trace_id`, runtime type, model, redacted stderr excerpts, tool-call summaries, and references to larger redacted artifacts. These identifiers and evidence bundles are appropriate for traces, logs, and API payloads, but they MUST NOT become metric labels.

### Metrics Catalog

The `ButlerMetrics` class (`src/butlers/core/metrics.py`) provides a per-butler wrapper around OTel instruments, while narrowly scoped module-level recorders serve shared durable boundaries that do not own a daemon instance. All instruments are lazily created from the global `MeterProvider`, so construction before `init_metrics` is safe.

#### Spawner Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.spawner.active_sessions` | UpDownCounter | `butler` | Concurrent sessions per butler |
| `butlers.spawner.queued_triggers` | UpDownCounter | `butler` | Triggers waiting for per-butler semaphore |
| `butlers.spawner.global_queue_depth` | UpDownCounter | `butler` | Triggers waiting for global concurrency cap |
| `butlers.spawner.session_duration_ms` | Histogram | `butler` | End-to-end session duration |
| `butlers.spawner.input_tokens` | Counter | `butler`, `model` | LLM input tokens per session |
| `butlers.spawner.output_tokens` | Counter | `butler`, `model` | LLM output tokens per session |
| `butlers.spawner.failover_attempts_total` | Counter | `butler`, `from_model`, `to_model`, `reason` | Same-tier failover transitions |
| `butlers.spawner.failover_suppressed_total` | Counter | `butler`, `reason` (first token only) | Failovers suppressed by the eligibility classifier |
| `butlers.spawner.failover_exhausted_total` | Counter | `butler`, `tier` | Same-tier failover loops that exhausted every candidate |

#### Recovery Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.recovery.active_workflows` | UpDownCounter | `butler`, `workflow` | Active recovery/investigation workflows |
| `butlers.recovery.phase_duration_ms` | Histogram | `butler`, `workflow`, `phase`, `outcome` | Per-phase workflow duration |
| `butlers.recovery.dispatch_decisions_total` | Counter | `butler`, `workflow`, `decision` | Admission-control decisions before session launch |
| `butlers.recovery.execution_failures_total` | Counter | `butler`, `workflow`, `phase`, `error_class` | Terminal failures from launched workflow phases |

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
| `butlers.switchboard.thread_affinity.hit` | Counter | `source` (constant `email`), `destination_butler`, `policy_tier` (constant `affinity`), `schema_version` (constant `thread_affinity.v1`) | Thread affinity routing hit |
| `butlers.switchboard.thread_affinity.miss` | Counter | `source` (constant `email`), `reason`, `schema_version` (constant `thread_affinity.v1`) | Thread affinity miss (reason: `no_thread_id`, `no_history`, `conflict`, `disabled`, `error`) |
| `butlers.switchboard.thread_affinity.stale` | Counter | `source` (constant `email`), `schema_version` (constant `thread_affinity.v1`) | Historical match outside TTL |
| `butlers.switchboard.triage.rule_matched` | Counter | `rule_type`, `action`, `source_channel` | Triage rule match |
| `butlers.switchboard.triage.pass_through` | Counter | `source_channel`, `reason` | Triage pass-through |
| `butlers.switchboard.triage.evaluation_latency_ms` | Histogram | `result` | End-to-end triage latency |

#### Switchboard Routing Metrics

`SwitchboardTelemetry` (`roster/switchboard/tools/routing/telemetry.py`) owns these instruments. Calls through `SwitchboardTelemetry.attrs()` (today only the route path in `roster/switchboard/tools/routing/route.py`) keep only `_ALLOWED_ATTRIBUTE_KEYS`, lowercase values, and truncate them to 64 characters. The ingress path in `src/butlers/modules/pipeline.py` passes its own attribute dict straight to seven of these instruments (`message_received`, `message_deduplicated`, `ingress_accept_latency_ms`, `lifecycle_transition`, `routing_decision_latency_ms`, `fallback_to_general` and `end_to_end_latency_ms`), so that filter does not apply there.

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.switchboard.message_received` | Counter | allowed keys | Accepted switchboard messages |
| `butlers.switchboard.message_deduplicated` | Counter | allowed keys | Messages deduplicated at ingress |
| `butlers.switchboard.message_overload_rejected` | Counter | allowed keys | Messages rejected by the overload policy |
| `butlers.switchboard.fallback_to_general` | Counter | allowed keys | Fallback decisions routed to general |
| `butlers.switchboard.ambiguity_to_general` | Counter | allowed keys | Ambiguous messages routed to general |
| `butlers.switchboard.router_parse_failure` | Counter | allowed keys | Router parse or validation failures |
| `butlers.switchboard.subroute_dispatched` | Counter | allowed keys | Subroutes dispatched to downstream butlers |
| `butlers.switchboard.subroute_result` | Counter | allowed keys | Subroute outcomes |
| `butlers.switchboard.lifecycle_transition` | Counter | allowed keys | Request lifecycle transitions |
| `butlers.switchboard.retry_attempt` | Counter | allowed keys | Retry attempts for route operations |
| `butlers.switchboard.circuit_transition` | Counter | allowed keys | Circuit-breaker state transitions |
| `butlers.switchboard.ingress_accept_latency_ms` | Histogram | allowed keys | Acceptance to normalized ingress context |
| `butlers.switchboard.routing_decision_latency_ms` | Histogram | allowed keys | Routing decision and classification latency |
| `butlers.switchboard.subroute_latency_ms` | Histogram | allowed keys | One subroute dispatch |
| `butlers.switchboard.fanout_completion_latency_ms` | Histogram | allowed keys | Fanout dispatch and aggregation |
| `butlers.switchboard.end_to_end_latency_ms` | Histogram | allowed keys | Ingress acceptance to completion |
| `butlers.switchboard.queue_depth` | Observable gauge | `schema_version` | Ingress and routing queue depth |
| `butlers.switchboard.inflight_requests` | Observable gauge | `schema_version` | Inflight switchboard requests |
| `butlers.switchboard.circuit_open_targets` | Observable gauge | `schema_version` | Circuit-open downstream targets |

"Allowed keys" means the subset of `source`, `destination_butler`, `outcome`, `lifecycle_state`, `error_class`, `policy_tier`, `fanout_mode`, `model_family`, `prompt_version` and `schema_version` that the call site supplies. Of these, `source`, `policy_tier`, `model_family`, `prompt_version` and `schema_version` are read from tool arguments on the ingress path (`src/butlers/modules/pipeline.py`; the `pipeline.process` MCP tool takes `source_channel` as free text) and are not bounded there. `model_family`, `prompt_version`, `schema_version` and `source` are not in the permitted list under Cardinality Discipline, and `policy_tier` is permitted only where it comes from the validated envelope's `PolicyTier` set or is a code constant (thread-affinity hits record `affinity`). The production ingest worker supplies none of the four tier and version arguments, so they take constant defaults today; the fix is tracked in bu-dr89b5.

#### Ingestion Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.ingestion.rule_matched` | Counter | `scope_type`, `rule_type`, `action` (`route_to:<butler>` collapsed to `route_to`), `source_channel` | Ingestion policy rule matched |
| `butlers.ingestion.rule_pass_through` | Counter | `scope_type`, `source_channel`, `reason` | Ingestion policy evaluation with no rule match |
| `butlers.ingestion.evaluation_latency_ms` | Histogram | `scope_type`, `result` | Ingestion policy evaluation latency |
| `butlers.ingestion.read.admission` | Histogram | `operation` | Seconds waiting for Timeline read admission |
| `butlers.ingestion.read.duration` | Histogram | `operation`, `outcome` | Admitted Timeline read duration in seconds |
| `butlers.ingestion.read.outcomes` | Counter | `operation`, `outcome` | Timeline read outcomes, including admission refusals |
| `butlers.ingest.bearer_scrubbed` | Counter | `kind`, `source_channel` | Bearer-material artifacts withheld at the ingest boundary |
| `butlers.pipeline.decomposition_empty` | Counter | `source_channel`, `connector_type` | Decompositions that returned no signals; `connector_type` is read from the request context and is not yet bounded, so it is not in the permitted list under Cardinality Discipline |

#### Domain-Event Delivery Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.domain_event.delivery_failed_permanent_total` | Counter | `source_butler`, `destination_butler`, `reason` (`non_retryable`/`attempts_exhausted`) | New durable `failed_permanent` delivery transitions only |

The domain-event counter MUST be recorded only after `mark_delivery_failed()` has successfully returned `failed_permanent`. It is deliberately best-effort after the durable ledger write: exporter failure MUST NOT roll back, replace, or otherwise alter the delivery outcome. Event IDs, types, payloads, exception text, and timestamps are forbidden attributes.

#### Memory Knowledge-Gap Metrics

| Instrument | Type | Labels | Description |
|------------|------|--------|-------------|
| `butlers.memory.knowledge_gap_closure_failures_total` | Counter | `butler` (registered name, or `unknown` outside a butler context), `stage` (`closure`) | Knowledge-gap closure failures swallowed so the fact write could commit |

The counter is best-effort and recorded after the failure is logged: an emission failure MUST NOT fail the fact write. Entity, predicate and fact identifiers and exception text are forbidden attributes.

### Cardinality Discipline

All metrics MUST use low-cardinality attributes only. Permitted attributes: `butler`, `tool_name`, `outcome`, `trigger_source`, `error_class`, `source_channel`, `policy_tier` (the validated `PolicyTier` set, or the code constant `affinity` on thread-affinity hits; see the ingress note under Switchboard Routing Metrics), `model`, `task_name`, `rule_type`, `action`, `reason`, `result`, `path`, `queue_name`, `starvation_override`, `source_butler`, `destination_butler`, `workflow`, `phase`, `decision`, `stage` (a closed set of bookkeeping step names, currently `closure`; an unsupported value is rejected before emission). Also permitted, each with the bound that keeps it safe: `operation` (the handler name of an ingestion Timeline GET route, a code-defined set), `scope_type` (`global` or `connector:<connector type>`, endpoint identity stripped), `lifecycle_state` (a closed set of code-defined request lifecycle names), `fanout_mode` (a closed set of code-defined fanout names), `kind` (the three bearer-scrub artifact kinds), `tier` (the closed `Complexity` tier set), and `from_model` / `to_model` (model catalog IDs, bounded like `model`).

The following MUST NEVER be used as metric attributes: `request_id`, raw sender identities, `thread_id`, message text, session IDs, attempt IDs, trace IDs, UUIDs, timestamps, or any other high-cardinality identifier.

### Prometheus Client Instruments (Out of Scope)

This RFC governs OpenTelemetry instruments only: the catalog above and the Cardinality Discipline permitted-attribute list apply to instruments created through the OpenTelemetry meter. Instruments created with `prometheus_client`, either through `butlers.metrics_registry` (`get_or_create_counter`, `get_or_create_gauge`, `get_or_create_histogram`, `get_or_create_summary`) or by direct `Counter(...)`, `Gauge(...)` and `Histogram(...)` calls, are out of scope and are NOT cataloged here.

Reasons:

- **Different pipeline.** Connector processes serve them on a loopback-only `/metrics` endpoint, so no out-of-pod scraper reaches most of them today; bu-n80xsk proposes moving connector metrics to OTLP. They are not exported over OTLP today, so the permitted-attribute list does not constrain them. Those registered in butler daemons and the dashboard API process (the switchboard runtime-attention worker, `core/dispatch_outcomes.py`, the chronicler calendar adapter, QA, approvals delivery, and the audit and home-person-mapping routers) are served nowhere today; bu-773cjf wires or deletes them.
- **Roughly 80 instruments, mostly connector-owned.** Most live under `src/butlers/connectors/`. The rest are in the switchboard runtime-attention worker, `core/dispatch_outcomes.py`, the audit and home-person-mapping API routers, the chronicler calendar adapter, the QA dispatch and module, and approvals delivery lifecycle and recovery. A second catalog here would duplicate their definitions and drift.
- **Little of it is read.** `observability/prometheus/prometheus.yml` scrapes only connector health endpoints, and the only prometheus_client metric any in-repo Grafana dashboard references is `chronicler_episode_participants_resolved_total` (`observability/grafana/butlers-chronicler.json`).

Governing places:

- `src/butlers/metrics_registry.py` owns registration and idempotent get-or-create semantics.
- `observability/prometheus/prometheus.yml` defines what is scraped.
- `docs/connectors/metrics.md` catalogs the shared `ConnectorMetrics` instruments and their labels, and `docs/connectors/overview.md` has query examples. Connector-specific and butler-process instruments are documented only where they are defined.

Constraints that still apply to these instruments:

- The "MUST NEVER" attribute list above is a design rule for every metric backend, prometheus_client labels included.
- A prometheus_client instrument that is later scraped and read by an in-repo dashboard or alert SHOULD be reviewed against that rule, and promoted into the catalog above if it becomes part of the supported observability surface.
- Label values for these instruments are not validated against the permitted-attribute list. Some connector labels (for example `endpoint_identity`, `media_type`) are not bounded by that list; tracked in bu-p88e3l and bu-x3wk5i rather than governed here.

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
- **RFC 0002:** Core tool registrations pass through `_ToolCallLoggingMCP`, which logs and captures calls without creating spans; module tool registrations pass through `_SpanWrappingMCP`, which creates `butler.tool.<name>` spans via `tool_span` in addition to logging and capturing.
- **RFC 0003:** Trace context propagates through route envelopes. Switchboard metrics cover ingestion, triage, and thread affinity.
- **RFC 0007:** The dashboard traces page provides a distributed trace index and span waterfall visualization.

## Alternatives Considered

**Module-level variable for active session context.** Rejected because a plain global variable would cause cross-session trace contamination when `max_concurrent_sessions > 1`. `ContextVar` provides per-task isolation.

**Per-butler resource attributes.** Rejected because creating separate TSDB series at the resource level for each butler multiplies storage cost. Per-butler attribution at the span/metric attribute level achieves the same observability without resource-level fan-out.

**Trace sampling.** Not implemented. Current session volumes (dozens per day per butler) do not warrant sampling. All traces are exported. This decision SHOULD be revisited if session volumes increase by an order of magnitude.
