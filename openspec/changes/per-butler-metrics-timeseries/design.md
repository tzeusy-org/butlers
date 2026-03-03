## Context

Butlers already have full OTEL instrumentation for internal metrics (spawner concurrency, buffer health, route latency) via `src/butlers/core/metrics.py`, which wraps the OpenTelemetry SDK and exports to an OTLP endpoint. A LGTM stack (Prometheus + Grafana) is already deployed and receiving these metrics. The framework also has a state store (`butlers.core.state`) that is a simple KV JSONB table in each butler's schema.

What's missing is an MCP-facing interface that lets a butler's LLM session define and emit its own domain-specific metrics (e.g. "emails classified today", "task completion latency") without requiring changes to framework code, and optionally read them back via PromQL for operational self-awareness.

## Goals / Non-Goals

**Goals:**
- Let any butler opt in to a `metrics` module that registers Prometheus metrics dynamically
- Expose 6 MCP tools: `metrics_define`, `metrics_emit`, `metrics_list`, `metrics_query`, `metrics_query_range`, and (implicit) startup re-registration
- Reuse `prometheus_client` (write) and `httpx` (read) — both already in the dependency graph
- Survive restarts by persisting metric definitions to the state store

**Non-Goals:**
- Dashboard UI or alerting rules — out of scope for this change
- Cross-butler metric aggregation — each module instance operates on its own namespace only
- Replacing the existing framework OTEL instrumentation in `butlers.core.metrics`
- Prometheus pushgateway / remote_write — this change uses the pull model (scrape) only

## Decisions

### 1. Write via OTEL SDK, not `prometheus_client` directly

**Decision:** Use the OpenTelemetry SDK (`opentelemetry.metrics`) via the existing `get_meter()` helper in `butlers.core.metrics`, not the `prometheus_client` library directly.

**Rationale:** The framework already configures a MeterProvider with an OTLP exporter on startup. Creating instruments through `get_meter()` means they automatically flow through the same pipeline (OTLP → LGTM stack) as the existing internal metrics. Using `prometheus_client` directly would require either a separate `/metrics` scrape endpoint or Pushgateway, neither of which is set up.

**Alternative considered:** `prometheus_client` with a registry per butler. Rejected because it would require exposing a separate HTTP scrape endpoint per butler daemon, which conflicts with the existing OTLP push model.

**OTEL SDK idempotency:** The SDK returns the same instrument object when `create_counter` / `create_up_down_counter` / `create_histogram` is called with the same name and unit, so re-registration on restart is a no-op with no error.

---

### 2. Read via Prometheus HTTP API (`httpx`), not OTEL

**Decision:** PromQL queries (`metrics_query`, `metrics_query_range`) hit the Prometheus HTTP API directly using `httpx.AsyncClient`. The OTEL SDK is write-only; there is no read-back path through it.

**Rationale:** `httpx` is already a declared dependency. The Prometheus HTTP API is simple, well-documented, and returns JSON directly. The module config accepts `prometheus_query_url` (e.g. `http://lgtm:9090`) pointing at the Prometheus-compatible query endpoint in the LGTM stack.

**Alternative considered:** PromQL over Grafana's proxy API. Rejected — adds auth complexity and couples the module to a specific Grafana version.

---

### 3. Butler name from `db.schema`

**Decision:** The module derives the metric namespace prefix from `db.schema` (the butler's schema name), available on the `Database` object passed to `on_startup`. The prefix is `butler_<schema>_` with hyphens replaced by underscores.

**Rationale:** The daemon does not currently pass `butler_name` as a separate argument to `on_startup`. `db.schema` is set by the daemon before module startup and is exactly the butler's normalized name. This avoids changing the `Module` ABC or the daemon's startup call-site.

**Alternative considered:** Adding `butler_name` to the `on_startup` signature. Deferred to keep this change minimal; can be revisited if other modules need it too.

---

### 4. Definitions persisted in state store, not a new table

**Decision:** Metric definitions (name, type, help, labels, registered_at) are stored as individual JSON blobs in the butler's existing state store under keys `metrics_catalogue:<metric_name>`. `state_list` (with prefix filter) retrieves all definitions at startup.

**Rationale:** Avoids any Alembic migration. The state store is already present in every butler schema. Metric definitions are low-cardinality (tens of entries at most) and infrequently written, so a KV store is entirely adequate.

**Alternative considered:** A dedicated `metrics_catalogue` table. Rejected — not worth a migration for this volume and access pattern.

---

### 5. In-process instrument cache on the module instance

**Decision:** `MetricsModule` maintains a `dict[str, metrics.Instrument]` in-process. `metrics_define` populates it; `metrics_emit` reads from it. Startup re-registration rebuilds it from the state store.

**Rationale:** OTEL instruments are not serialisable and must be live Python objects. The cache avoids repeated `get_meter().create_*()` calls on every emit (though the SDK handles duplicates gracefully, the dict lookup is cheaper and avoids string formatting on every call).

---

### 6. Label validation at define-time, enforced at emit-time

**Decision:** Label names are declared once in `metrics_define`. `metrics_emit` validates that the provided `labels` dict keys exactly match the declared set (no extra, no missing). The OTEL SDK accepts arbitrary attribute dicts, so this is a module-level constraint.

**Rationale:** Prometheus cardinality explosions from unconstrained label values are a known operational risk. Requiring declaration upfront makes the label schema explicit and reviewable. Matching at emit time catches mistakes early.

## Risks / Trade-offs

**[OTEL SDK global MeterProvider]** → The `_meter_provider_installed` guard in `butlers.core.metrics` means all butlers sharing a process share a single MeterProvider. Dynamic instruments created by `MetricsModule` are registered on that shared provider. This is correct behaviour but means metric names must be globally unique across all butlers in the same process (namespace prefix `butler_<name>_` mitigates collision risk).

**[Prometheus cardinality]** → The LLM can in principle define many metrics with high-cardinality labels. No hard limit is enforced in this change. Mitigation: label names must be declared at define-time (not emitted ad hoc), which makes cardinality at least predictable. A future change could add a per-butler metric count limit in config.

**[State store coupling]** → The module uses `state_get` / `state_set` / `state_list` from `butlers.core.state` which operate on the butler's asyncpg pool. If the state store schema changes, the module breaks. Mitigation: the state store API is stable and used by many other components.

**[Prometheus query URL is not validated at startup]** → A misconfigured `prometheus_query_url` will not fail module startup; errors only surface when `metrics_query` is called. Mitigation: a connectivity check can be added to `on_startup` in a follow-on change.

## Migration Plan

Purely additive: a new module directory, no migrations, no changes to existing tables or modules. Deployment is:

1. Merge the change — new `src/butlers/modules/metrics/` is auto-discovered by `ModuleRegistry`
2. To enable for a butler: add `metrics = { prometheus_query_url = "http://lgtm:9090" }` to its `butler.toml` `[modules]` section
3. No restart of unrelated butlers required; module is ignored unless explicitly enabled

Rollback: remove `metrics` from `butler.toml` and restart the butler. No data is left behind (state store keys can be manually cleared if desired).

## Resolved Questions

- **Startup liveness check:** `on_startup` will NOT ping `prometheus_query_url`. Errors surface at query time only. Keeps startup fast and avoids failing the module when Prometheus is temporarily unreachable on boot.

- **Metric count cap:** Hard cap of **1,000 defined metrics per butler**. `metrics_define` returns an error if the catalogue already contains 1,000 entries. The module MUST also include a standing advisory in tool descriptions warning the LLM against high-cardinality label values (e.g. user IDs, request IDs as label values).

- **OTEL temporality for gauges:** The Python OTEL SDK's `OTLPMetricExporter` defaults to **cumulative** temporality, and nothing in `butlers.core.metrics` overrides this. The homelab LGTM stack receives metrics via Alloy's `otelcol.exporter.prometheus` → Prometheus remote_write, which passes cumulative metrics straight through. `UpDownCounter` with cumulative temporality correctly represents absolute gauge values in Prometheus. On process restart, the value resets to 0 and climbs again — standard expected Prometheus behaviour.
