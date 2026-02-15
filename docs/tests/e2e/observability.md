# Observability — Tracing, Metrics, and Diagnostic Validation

## Overview

The Butlers ecosystem uses OpenTelemetry for distributed tracing and structured
logging for diagnostic output. A message traversing the pipeline crosses multiple
process boundaries (switchboard → target butler) and multiple async contexts
(ingest → classify → dispatch → route → trigger → tool execution). Observability
E2E tests validate that traces propagate correctly across these boundaries,
metrics are emitted, and diagnostic data is complete enough to debug failures.

## Distributed Tracing

### Trace Context Propagation

The switchboard injects `TRACEPARENT` environment variables when routing to
target butlers. This allows a single trace to span the entire message lifecycle
across multiple butler processes:

```
Switchboard (trace_id=abc, span_id=001)
    │
    ├─ classify_message() span (span_id=002, parent=001)
    │
    ├─ dispatch_decomposed() span (span_id=003, parent=001)
    │   │
    │   └─ route() span (span_id=004, parent=003)
    │       │
    │       └─ [MCP call to target butler]
    │           │
    │           └─ Target Butler (trace_id=abc, span_id=005, parent=004)
    │               │
    │               ├─ trigger() span (span_id=006, parent=005)
    │               │
    │               └─ tool execution span (span_id=007, parent=006)
    │
    └─ aggregate_responses() span (span_id=008, parent=001)
```

### TRACEPARENT Injection

The spawner injects trace context into the CC instance's environment:

```python
# src/butlers/core/telemetry.py
def get_traceparent_env() -> dict[str, str]:
    """Return TRACEPARENT env var for the current span context."""
    ...

# src/butlers/core/spawner.py
env = _build_env(config)
env.update(get_traceparent_env())  # inject trace context
```

The route function also injects trace context when making MCP calls:

```python
# roster/switchboard/tools/routing/route.py
from butlers.core.telemetry import inject_trace_context
# trace context passed as MCP call metadata
```

### E2E Tracing Tests

| Test | What It Validates | Assertion |
|------|-------------------|-----------|
| Trace ID propagation | Same `trace_id` in switchboard and target butler spans | Query spans by `trace_id`, verify both butlers present |
| Parent-child linkage | Target butler's root span has switchboard's route span as parent | Verify `parent_span_id` in target butler's first span |
| Span completeness | Every pipeline stage emits a span | Count spans per `trace_id`, verify >= expected count |
| Error span attribution | Failed tool call produces error span with correct status | Query error spans, verify `status=ERROR` and `error.message` |

### Trace Validation Without Grafana

In the E2E harness, traces are not exported to Grafana/Tempo (no
`OTEL_EXPORTER_OTLP_ENDPOINT` configured). Instead, traces are captured
in-process using an `InMemorySpanExporter`:

```python
from opentelemetry.sdk.trace.export.in_memory import InMemorySpanExporter

exporter = InMemorySpanExporter()
# Configure tracer provider with in-memory exporter
# After test, query exporter.get_finished_spans()
```

This allows trace assertions without external infrastructure dependencies.

### Trace Correlation

Every session log row includes the `trace_id` that was active when the session
was created:

```sql
sessions (
    session_id UUID,
    trace_id TEXT,  -- OTel trace ID
    ...
)
```

**E2E test:** After a full pipeline run, query `sessions` for the
`trace_id` and verify it matches the trace ID from the switchboard's
classification span.

## Tool Span Instrumentation

Every MCP tool invocation is wrapped in a `tool_span()` context manager that
emits an OpenTelemetry span:

```python
# src/butlers/core/telemetry.py
@contextmanager
def tool_span(tool_name: str, **attributes):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span(f"tool:{tool_name}", attributes=attributes):
        yield
```

### Span Attributes

| Attribute | Value | Example |
|-----------|-------|---------|
| `tool.name` | MCP tool name | `measurement_log` |
| `tool.butler` | Butler that owns the tool | `health` |
| `tool.module` | Module that registered the tool (if any) | `memory` |
| `tool.args` | Serialized tool arguments (redacted for sensitive) | `{"type": "weight", "value": 80}` |
| `tool.result.status` | `success` or `error` | `success` |
| `tool.duration_ms` | Wall-clock execution time | `42` |

### E2E Tool Span Tests

| Test | What It Validates |
|------|-------------------|
| Span emitted per tool call | After triggering a butler, verify span count matches `tool_calls` count in session |
| Correct tool name | Span's `tool.name` matches the tool that was called |
| Error spans for failed tools | A tool that raises produces a span with `status=ERROR` |
| Sensitive arg redaction | Tool arguments marked as sensitive in `ToolMeta.arg_sensitivities` are not in span attributes |

## Routing Telemetry

The switchboard emits routing-specific metrics and spans via the
`get_switchboard_telemetry()` helper:

```python
# roster/switchboard/tools/routing/telemetry.py
def get_switchboard_telemetry():
    """Return the switchboard's telemetry instrumentor."""
    ...
```

### Routing Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `switchboard.classify.duration_ms` | Histogram | `model` | Classification LLM call latency |
| `switchboard.classify.tokens` | Counter | `direction` (input/output) | Token usage per classification |
| `switchboard.route.duration_ms` | Histogram | `target_butler`, `status` | Per-butler routing latency |
| `switchboard.route.errors` | Counter | `target_butler`, `error_class` | Routing failures by type |
| `switchboard.dispatch.subrequests` | Histogram | `fanout_mode` | Subrequests per dispatch |

### E2E Metrics Tests

| Test | What It Validates |
|------|-------------------|
| Classification duration recorded | After classify, metric has at least one observation |
| Route duration per butler | After dispatch, each target butler has a duration observation |
| Error counter increments | After routing to unavailable butler, error counter > 0 |
| Token counter increments | After classification, token counter > 0 for both input and output |

## Session Log Completeness

Every spawner invocation produces a session log row. The E2E harness validates
that session logs contain all diagnostic fields needed for post-mortem analysis:

### Required Session Fields

| Field | Type | Constraint |
|-------|------|------------|
| `session_id` | UUID | Non-null, unique |
| `butler_name` | TEXT | Matches the butler that ran the session |
| `trigger_source` | TEXT | `external`, `heartbeat`, `scheduled`, or `test` |
| `prompt` | TEXT | The full prompt sent to the CC instance |
| `model` | TEXT | Model ID (e.g., `claude-haiku-4-5-20251001`) |
| `status` | TEXT | `running`, `completed`, or `error` |
| `created_at` | TIMESTAMPTZ | Non-null |
| `completed_at` | TIMESTAMPTZ | Non-null for `completed`/`error` status |
| `duration_ms` | INTEGER | `>= 0` for completed sessions |
| `tool_calls` | JSONB | Array of `{name, arguments, result}` |
| `input_tokens` | INTEGER | `>= 0` when reported by adapter |
| `output_tokens` | INTEGER | `>= 0` when reported by adapter |
| `trace_id` | TEXT | OTel trace ID, non-null when tracing active |
| `error` | TEXT | Non-null when `status="error"` |

### E2E Session Log Tests

| Test | What It Validates |
|------|-------------------|
| All fields populated | After a successful trigger, every non-optional field is non-null |
| Duration accuracy | `duration_ms` is within 20% of wall-clock time measured by test |
| Tool calls recorded | `tool_calls` JSONB array has entries matching expected tool names |
| Error sessions | After a failed trigger, `status="error"` and `error` field is set |
| Trace linkage | `trace_id` matches the trace context from the switchboard |

## Structured Logging

### Log Format

All butler daemons emit structured log messages via Python's `logging` module.
The E2E harness captures all logs at `DEBUG` level to a timestamped file.

### Log-Level Assertions

| Level | When | E2E Validation |
|-------|------|----------------|
| `DEBUG` | Every MCP tool call, DB query, state change | Not asserted (too noisy) |
| `INFO` | Butler startup/shutdown, session start/complete, route success | Presence validated for key events |
| `WARNING` | Module degradation, stale client, classification fallback | Presence validated for expected warnings |
| `ERROR` | Unhandled exceptions, route failures, spawner errors | Absence validated for unexpected errors |

### E2E Log Tests

| Test | What It Validates |
|------|-------------------|
| No unexpected errors | After a successful run, `ERROR` log entries only from expected sources (module degradation) |
| Module degradation logged | Each failed module produces a `WARNING` with module name and phase |
| Classification logged | `classify_message` produces an `INFO` entry with butler names |
| Route logged | Each `route()` call produces an `INFO` entry with target butler and status |

## Cost Tracking as Observability

The `cost_tracker` fixture is an observability tool that aggregates LLM usage
across the entire E2E session:

```
════════════════════════════════════════════════════════════
E2E Cost Summary
  LLM calls:    10
  Input tokens:  24,312
  Output tokens:  6,847
  Est. cost:     $0.047
════════════════════════════════════════════════════════════
```

### Per-Scenario Breakdown

Beyond the session summary, the cost tracker can report per-scenario token
usage:

| Scenario | LLM Calls | Input Tokens | Output Tokens |
|----------|-----------|-------------|---------------|
| switchboard-classify-health | 1 | 1,523 | 287 |
| health-weight-log | 1 | 3,201 | 912 |
| ... | ... | ... | ... |

This helps identify scenarios with unexpectedly high token usage (potential
prompt bloat or unnecessary context).

### E2E Cost Tests

| Test | What It Validates |
|------|-------------------|
| Total cost under budget | Session total < $0.20 (conservative ceiling) |
| Per-scenario budget | No single scenario exceeds 10,000 input tokens |
| Token counter accuracy | Fixture totals match sum of per-session `input_tokens`/`output_tokens` |

## Grafana/Tempo Integration (Production Only)

In production, traces are exported to a Grafana/Tempo stack via the OTLP
endpoint configured in `docker-compose.yml`:

```yaml
environment:
  - OTEL_EXPORTER_OTLP_ENDPOINT=http://otel.parrot-hen.ts.net:4318
```

The E2E harness does **not** export to Grafana. However, the harness can be
configured to export by setting `OTEL_EXPORTER_OTLP_ENDPOINT` in the test
environment. This is useful for debugging complex multi-butler flows in a
visual trace viewer.

```bash
# Optional: export E2E traces to local Grafana
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
make test-e2e
# Then open Grafana → Explore → Tempo → search by trace_id
```
