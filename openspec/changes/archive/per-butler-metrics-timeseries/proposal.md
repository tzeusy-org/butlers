## Why

Butlers currently have no way to emit or query arbitrary operational metrics. OpenTelemetry instrumentation in the framework covers internal infrastructure (spawner, buffer, route latency), but individual butlers cannot declare their own domain-specific metrics or read back historical values for self-awareness. The LGTM stack and Prometheus HTTP API are already wired up — the missing piece is an MCP-facing module that gives butlers first-class access to both sides of Prometheus.

## What Changes

- Introduce a new opt-in `module-metrics` that wraps `prometheus_client` (write) and the Prometheus HTTP API (read)
- Butlers can dynamically register named Prometheus metrics (counter, gauge, histogram), emit samples, and query historical data via PromQL
- Metric definitions are persisted to the butler's existing state store (KV JSONB) so they survive restarts and are re-registered on startup — no new DB tables or migrations

## Capabilities

### New Capabilities

- `module-metrics`: Opt-in module providing MCP tools for Prometheus interaction. Write side: register a metric definition (`metrics_define`), emit a sample (`metrics_emit`). Read side: instant query (`metrics_query`) and range query (`metrics_query_range`) via PromQL against the configured Prometheus endpoint. Catalogue side: list registered metric definitions (`metrics_list`). All butler metrics are auto-namespaced as `butler_<butler_name>_<metric_name>` to prevent cross-butler collisions. Definitions are persisted in the butler's state store (KV) for restart durability.

### Modified Capabilities

_(none)_

## Impact

- **New module:** `src/butlers/modules/metrics/` — `__init__.py` (MetricsModule, tool registration), `storage.py` (state-store persistence of definitions), `prometheus.py` (dynamic metric registration via `prometheus_client`, PromQL HTTP queries via `httpx`)
- **No migrations:** Metric samples live in Prometheus/LGTM; definitions live in the existing state store — zero new tables
- **Configuration:** Module needs `PROMETHEUS_URL` (or reads from existing OTEL config) to point at the Prometheus query endpoint
- **Backwards compatibility:** Additive only — existing butlers unaffected unless they opt in to the module
