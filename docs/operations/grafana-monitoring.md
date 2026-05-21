# Grafana Monitoring

> **Purpose:** Document the observability stack: OpenTelemetry instrumentation, trace propagation, span architecture, and Grafana integration.
> **Audience:** Operators monitoring Butlers in production, developers debugging performance issues.
> **Prerequisites:** [Docker Deployment](docker-deployment.md), [Environment Config](environment-config.md).

## Overview

Butlers uses OpenTelemetry (OTel) for distributed tracing and metrics, with a Grafana LGTM stack (Loki, Grafana, Tempo, Mimir) as the observability backend. When `OTEL_EXPORTER_OTLP_ENDPOINT` is configured, all butler daemons and the dashboard API emit traces via OTLP HTTP to a Grafana Alloy collector (port 4318), which forwards them to Grafana Tempo. When unset, telemetry falls back to no-op providers with zero overhead.

## Local Development Observability Stack

For local development, a self-contained observability stack is provided via `docker-compose.observability.yml`. This stack includes all components needed to collect and visualize telemetry without external dependencies.

### Starting the Stack

Use `scripts/compose.sh` with the `--observability` flag:

```bash
./scripts/compose.sh --observability
```

This enables the `observability` profile in Docker Compose, which automatically:
- Sets `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318`
- Starts all observability services (otel-collector, Tempo, Prometheus, Grafana)

Alternatively, start the stack directly with Docker Compose:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile observability up -d
```

If using direct Docker Compose, set the OTLP endpoint environment variable:

```bash
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318 \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile observability up -d
```

### Signal Flow

The local observability stack follows this signal flow:

```
Butler Daemons + Dashboard API
  ↓ (OTLP HTTP)
OpenTelemetry Collector (port 4318)
  ├─ /v1/traces  → Grafana Tempo
  └─ /v1/metrics → Prometheus (remote_write)

Connector Health Endpoints (/metrics)
  ↓ (Prometheus scrape)
Prometheus

Grafana
  ├─ Queries Prometheus for metrics
  └─ Queries Tempo for traces
```

### Components

- **otel-collector** — OpenTelemetry Collector (port 4318 for OTLP HTTP, 4317 for gRPC)
  - Receives OTLP signals from all butler services
  - Routes traces to Tempo via gRPC
  - Routes metrics to Prometheus via remote_write

- **Tempo** — Grafana Tempo (port 3200)
  - Receives distributed traces from otel-collector
  - Provides trace query API for Grafana
  - Stores traces in local volume (`tempo_data`)

- **Prometheus** — Prometheus metrics database (port 9090)
  - Scrapes connector health endpoints (prometheus_client text format)
  - Receives OTLP metrics from otel-collector via remote_write
  - Stores time-series data in local volume (`prometheus_data`)

- **Grafana** — Grafana dashboards UI (port 3000)
  - Pre-provisioned with Prometheus and Tempo datasources
  - Pre-configured dashboards from `grafana/*.json`
  - Credentials: `admin` / `admin` (overridable via `GF_SECURITY_ADMIN_PASSWORD`)

### Accessing the UI

- **Grafana** — http://localhost:3000
  - Pre-provisioned dashboards visible on landing page
  - Datasources already configured (Prometheus, Tempo)

- **Prometheus** — http://localhost:9090
  - Query interface for metrics
  - Visualize metrics collected from butlers and connectors

- **Tempo** — http://localhost:3200 (API only)
  - No native UI; use Grafana's Explore tab to browse traces

### Configuration Files

The local stack is configured by:

- **`docker-compose.observability.yml`** — Service definitions (images, ports, volumes, networks)
- **`otel-collector/config.yaml`** — OTLP receiver config and routing rules
- **`prometheus/prometheus.yml`** — Scrape targets for connector health endpoints
- **`tempo/config.yaml`** — Trace ingestion and storage
- **`grafana/provisioning/`** — Datasource and dashboard auto-provisioning

### Stopping the Stack

```bash
./scripts/compose.sh --observability
# Then: docker compose down

# Or directly:
docker compose -f docker-compose.yml -f docker-compose.observability.yml down
```

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

## Dashboard API Prometheus Metrics

Several dashboard API endpoints expose `prometheus_client` counters (text format, scraped by
the Prometheus scrape job that already collects connector health endpoints). These cover error
paths that historically went undetected for extended periods.

### `ingestion_bulk_replay_errors_total`

**Source:** `src/butlers/api/routers/ingestion_events.py`
**Type:** Counter
**Labels:** `code` (HTTP status code string, e.g. `"503"`)

Incremented on every 5xx response from `POST /api/ingestion/events/replay/bulk`. All four
error paths in the handler are instrumented:

1. Shared database pool unavailable (pool key lookup fails → HTTP 503)
2. Phase 1 row-lock failure (FOR UPDATE SKIP LOCKED query exception → HTTP 503)
3. Phase 3 UPDATE failure (replay_pending marking exception → HTTP 503)
4. Catch-all unexpected exception (any other unhandled error → HTTP 503)

**Why this counter exists:** In 2026-05-20 a structural SQL incompatibility (FOR UPDATE +
LEFT JOIN) caused every call to this endpoint to return 503. Without a counter, the failure
was undetected for an entire production day. The counter enables Prometheus alerts and
Grafana dashboards that surface these failures within minutes.

**Recommended alert rule (Prometheus alerting):**

```yaml
- alert: BulkReplay503sElevated
  expr: increase(ingestion_bulk_replay_errors_total{code="503"}[5m]) > 2
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "bulk_replay endpoint returning 503s"
    description: >
      POST /api/ingestion/events/replay/bulk has returned
      {{ $value | printf "%.0f" }} HTTP 503 responses in the last 5 minutes.
      Check server logs for DB connectivity or SQL errors.
```

**Grafana PromQL examples:**

```promql
# Total 503s in the last hour
increase(ingestion_bulk_replay_errors_total{code="503"}[1h])

# Rate of 503s over 5-minute windows
rate(ingestion_bulk_replay_errors_total{code="503"}[5m])
```

## Related Pages

- [Docker Deployment](docker-deployment.md) -- Service configuration including OTLP endpoints
- [Troubleshooting](troubleshooting.md) -- Debugging with traces
- [Dashboard API](../api_and_protocols/dashboard-api.md) -- FastAPI instrumentation details
- [Session Lifecycle](../runtime/session-lifecycle.md) -- Sessions carry `trace_id` for correlation
