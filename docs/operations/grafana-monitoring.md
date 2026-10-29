# Grafana Monitoring

> **Purpose:** Document the observability stack: OpenTelemetry instrumentation, trace propagation, span architecture, and Grafana integration.
> **Audience:** Operators monitoring Butlers in production, developers debugging performance issues.
> **Prerequisites:** [Kubernetes Deployment](kubernetes-deployment.md) for live dev; [Docker Deployment](docker-deployment.md) for legacy/local Compose.

## Overview

Butlers uses OpenTelemetry (OTel) for distributed tracing and metrics, with Tempo, Prometheus and Grafana as the observability backend. When `OTEL_EXPORTER_OTLP_ENDPOINT` is configured, all butler daemons and the dashboard API emit traces and metrics via OTLP HTTP to an OpenTelemetry Collector (`otel-collector`, port 4318), which forwards traces to Grafana Tempo and metrics to Prometheus via `remote_write`. When unset, telemetry falls back to no-op providers with zero overhead.

## Current Dev Observability (Kubernetes)

The live `butlers-dev` release sends OTLP HTTP to the cluster's existing LGTM
stack; the Butlers chart does not install Grafana, Tempo, Prometheus or a
collector. `commonEnv.OTEL_EXPORTER_OTLP_ENDPOINT` is applied to the API, daemon
and connectors. `scripts/k8s/site-helm-args.sh` supplies its site-specific value
from `BUTLERS_OTLP_ENDPOINT`; the checked-in `http://otel.invalid:4318` is a
placeholder, not a usable collector.

Use the site's existing Grafana endpoint and access policy. See
[Kubernetes Deployment](kubernetes-deployment.md#deploy) for the committed-image
workflow and site configuration. This source description does not attest live
collector reachability or dashboard provisioning.

## Monitoring assets

The local Compose observability services and launcher option have been retired.
The cluster LGTM stack remains independently managed. The shared dashboard JSON
in `observability/grafana/`, paused rules in `observability/grafana-alerting/`
and the collector/Prometheus/Tempo configuration remain available to their
cluster provisioning consumers. These files are not evidence that provisioning
or delivery has executed. Alert rules must be provisioned through Grafana's
alerting input, separately from its recursive dashboard input.

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
| `OTEL_EXPORTER_OTLP_ENDPOINT` | -- | OTLP HTTP endpoint (e.g., `http://otel-collector:4318`). When unset, all telemetry is no-op. |

In Docker Compose, butler services use:
```yaml
OTEL_EXPORTER_OTLP_ENDPOINT: http://otel.example.ts.net:4318
```

## Dashboard API Metrics

The dashboard API exports no Prometheus metrics. It mounts no Prometheus
`/metrics` route and has no scrape job in `observability/prometheus/prometheus.yml`;
connector processes are the ones that serve `prometheus_client` text for scraping.
Its telemetry reaches Grafana only through OTLP, via `OTEL_EXPORTER_OTLP_ENDPOINT`
above.

## Durable Domain-Event Delivery Failures

`butlers.domain_event.delivery_failed_permanent_total` is an OTel counter for a
newly durable `failed_permanent` domain-event delivery. It has only
`source_butler`, `destination_butler`, and `reason` (`non_retryable` or
`attempts_exhausted`) labels; it deliberately excludes event IDs, payloads,
exception text, and timestamps.

The Switchboard dashboard displays it with a reset-safe
`increase(...)` query. The provisioned warning rule is configured to evaluate a
new transition over 15 minutes for 5 minutes once an owner-approved change
enables evaluation; it is explicitly paused and ships no contact point or
notification policy. While `isPaused=true`, Grafana performs no evaluation and
creates no alert instances, so this rule cannot diagnose a missing series or
evaluation error. Its configured `NoData` and `Error` policies take effect only
after an owner explicitly enables evaluation.
Enabling evaluation or notification delivery, changing a route, or exercising
an alert requires a separate owner-approved operational change.

Its JSON definition lives in `observability/grafana-alerting/`, for the cluster's
separate alerting-provisioning input. It must remain outside the recursive
dashboard source so Grafana never mistakes the alert rule for a dashboard.

## Related Pages

- [Deployment Posture](deployment-posture.md) -- Dev vs hardened posture, Grafana anon-viewer gating
- [Docker Deployment](docker-deployment.md) -- Service configuration including OTLP endpoints
- [Troubleshooting](troubleshooting.md) -- Debugging with traces
- [Dashboard API](../api_and_protocols/dashboard-api.md) -- FastAPI instrumentation details
- [Session Lifecycle](../runtime/session-lifecycle.md) -- Sessions carry `trace_id` for correlation
