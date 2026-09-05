---
name: generate-grafana-dashboards
description: Generate or update Grafana dashboard JSONs for the Butlers application. Use when asked to create, refresh, or expand Grafana dashboards. Queries live Prometheus and Tempo to discover actual metric/trace data before generating any JSONs. Knows where instrumentation source code lives, where dashboards are stored, and the OTel→Prometheus naming conventions for this project.
metadata:
  owner: tze
  authors:
    - tze
    - Claude
  status: active
  last_reviewed: "2026-09-05"
---

# Generate Grafana Dashboards

Butlers exports all three OTel signals via OTLP → Grafana Alloy → LGTM stack:
- **Metrics** → Mimir (Prometheus-compatible). Query live Prometheus first — metric names are not guessable.
- **Traces** → Tempo. Query via TraceQL. Every MCP tool call, LLM session, scheduler tick, and switchboard pipeline stage is instrumented.
- **Logs** → Loki (**not yet shipping** — butler logs write to disk with trace_id/span_id fields but no shipper is configured).

## Key endpoints

| Resource | Location | Grafana datasource UID |
|---|---|---|
| Prometheus | `https://prometheus.parrot-hen.ts.net` | `${datasource}` (variable) |
| Tempo | `http://lgtm-tempo.lgtm.svc.cluster.local:3200` (cluster-internal) | `tempo` |
| Loki | `http://lgtm-loki.lgtm.svc.cluster.local:3100` (cluster-internal) | `loki` |
| OTLP ingest | `http://otel.parrot-hen.ts.net:4318` | — |
| Existing dashboards | `observability/grafana/` in repo root | — |
| Metrics instrumentation | `src/butlers/core/metrics.py` | — |
| Tracing instrumentation | `src/butlers/core/telemetry.py` | — |

### Tempo discovery (from k8s)

```bash
# List available trace tags
kubectl -n lgtm exec lgtm-tempo-0 -- wget -qO- 'http://localhost:3200/api/search/tags'
# Get values for a tag
kubectl -n lgtm exec lgtm-tempo-0 -- wget -qO- 'http://localhost:3200/api/search/tag/butler.name/values'
# Search traces
kubectl -n lgtm exec lgtm-tempo-0 -- wget -qO- 'http://localhost:3200/api/search?limit=20'
# TraceQL search
kubectl -n lgtm exec lgtm-tempo-0 -- wget -qO- 'http://localhost:3200/api/search?q=%7Bname%3D%22butler.llm_session%22%7D&limit=10'
```

---

## Dashboard Catalog

Each perspective lives in its own file. When the user asks to "generate dashboards", produce **all** of them. When they ask to update a specific one, update only that file.

| File | UID | Title | Perspective |
|---|---|---|---|
| `observability/grafana/butlers-dashboard.json` | `butlers-fleet-v1` | Butlers Fleet | High-level health: active sessions, throughput, queue fill, E2E latency |
| `observability/grafana/butlers-pressure.json` | `butlers-pressure-v1` | Butlers — System Pressure | Latency percentiles, backpressure, circuit breakers, inflight ratios |
| `observability/grafana/butlers-usage.json` | `butlers-usage-v1` | Butlers — Usage & Cost | Per-butler session rates, token consumption by model, scheduled task dispatch |
| `observability/grafana/butlers-switchboard.json` | `butlers-switchboard-v1` | Butlers — Switchboard | Ingest outcomes, triage decisions, fanout, thread affinity, lifecycle |
| `observability/grafana/butlers-butler.json` | `butlers-butler-v1` | Butlers — Butler Detail | Single-butler drilldown: all subsystem metrics filtered to one butler |
| `observability/grafana/butlers-traces.json` | `butlers-traces-v1` | Butlers — Traces | Trace search, session durations, span breakdowns, error rates (Tempo) |

Full per-dashboard panel list: [`references/dashboard-catalog.md`](references/dashboard-catalog.md) —
load when generating or auditing a specific dashboard's panel set.

### Design rules

1. **Every panel must respect the `$butler` variable.** If a metric does not carry a `butler` label, it belongs in a dashboard that doesn't expose the `$butler` filter (e.g. Switchboard). Never put a `butlers_switchboard_*` metric in Fleet, Pressure, Usage, or Butler Detail — those dashboards promise per-butler filtering and switchboard metrics break that contract.
2. **Switchboard-only metrics** (`butlers_switchboard_*`, health ratio gauges, queue dequeue by tier) go exclusively in `butlers-switchboard.json`.
3. **Fleet dashboard** may include switchboard E2E latency as an aggregate overview stat (no butler filter expected), but Pressure and Usage must not.

---

## Workflow

### Step 1 — Discover live metrics

Always start here. Never guess metric names.

```
GET https://prometheus.parrot-hen.ts.net/api/v1/label/__name__/values?match[]={job="butlers"}
```

This returns the canonical list of all metric names currently in Prometheus. Use it to know what exists before writing any queries.

To get label sets for a metric group:
```
GET https://prometheus.parrot-hen.ts.net/api/v1/query?query={job="butlers",__name__=~"butlers_switchboard.*"}
```

### Step 2 — Read source if needed

If you need to understand what a metric measures (units, semantics, when it fires), read:
- `src/butlers/core/metrics.py` — all framework metrics with docstrings
- `src/butlers/core/telemetry.py` — span naming and `butler.name` / `service.name` attributes

For butler-specific metrics (per-butler metrics module), check
`openspec/changes/archive/per-butler-metrics-timeseries/design.md`.

### Step 3 — Decide scope

- **"Generate dashboards"** (no qualifier) → produce all 6 files in the catalog
- **"Update the pressure dashboard"** → update only `butlers-pressure.json`
- **"Add a panel for X"** → determine which dashboard owns that metric (see catalog above) and update that file

Check `observability/grafana/` for existing files first — update rather than replace where
they exist. See [`references/metrics-catalogue.md`](references/metrics-catalogue.md) for all
verified metric names, trace span names, and label sets — load before writing any query so
names aren't guessed.

### Step 4 — Generate JSON

Use `schemaVersion: 39`. Every dashboard needs: `datasource` + `butler` templating variables
(single-select for Butler Detail), 24-column `gridPos` panels, correct `unit` per panel type,
and — for "total over window" panels — a **stat** panel with `increase(...[$__range])`, never
a timeseries. Full JSON snippets for all of this:
[`references/dashboard-json-patterns.md`](references/dashboard-json-patterns.md) — load when
writing or editing a dashboard's actual panel/variable JSON.

### Step 5 — Save

Write each dashboard to its file in `observability/grafana/`. When generating all dashboards, write all 6 files. Preserve existing panel IDs and UIDs when updating.

---

## OTel → Prometheus naming rules

These are **verified** against live Prometheus for this project:

| Rule | Example |
|---|---|
| Dots → underscores | `butlers.spawner.active_sessions` → `butlers_spawner_active_sessions` |
| `unit="ms"` → append `_milliseconds` | `session_duration_ms` → `session_duration_ms_milliseconds` |
| Unit already suffix of name → **not** re-appended | `active_sessions` (unit `sessions`) → stays `active_sessions` |
| Non-suffix unit → appended | `queue_depth` (unit `messages`) → `queue_depth_messages` |
| Counter → append `_total` (after unit) | `enqueue` (unit `messages`) → `enqueue_messages_total` |
| UpDownCounter → no `_total` | stays as gauge |
| Histograms → `_bucket`, `_sum`, `_count` suffixes | standard |

All metrics carry `job="butlers"`. Per-butler metrics also carry `butler="<name>"`. See
[`references/metrics-catalogue.md`](references/metrics-catalogue.md) for all verified metric
names and label sets.
