---
name: generate-grafana-dashboards
description: Generate or update Grafana dashboard JSONs for the Butlers application. Use when asked to create, refresh, or expand Grafana dashboards. Queries live Prometheus and Tempo to discover actual metric/trace data before generating any JSONs. Knows where instrumentation source code lives, where dashboards are stored, and the OTel→Prometheus naming conventions for this project.
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
| Existing dashboards | `grafana/` in repo root | — |
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
| `grafana/butlers-dashboard.json` | `butlers-fleet-v1` | Butlers Fleet | High-level health: active sessions, throughput, queue fill, E2E latency |
| `grafana/butlers-pressure.json` | `butlers-pressure-v1` | Butlers — System Pressure | Latency percentiles, backpressure, circuit breakers, inflight ratios |
| `grafana/butlers-usage.json` | `butlers-usage-v1` | Butlers — Usage & Cost | Per-butler session rates, token consumption by model, scheduled task dispatch |
| `grafana/butlers-switchboard.json` | `butlers-switchboard-v1` | Butlers — Switchboard | Ingest outcomes, triage decisions, fanout, thread affinity, lifecycle |
| `grafana/butlers-butler.json` | `butlers-butler-v1` | Butlers — Butler Detail | Single-butler drilldown: all subsystem metrics filtered to one butler |
| `grafana/butlers-traces.json` | `butlers-traces-v1` | Butlers — Traces | Trace search, session durations, span breakdowns, error rates (Tempo) |

### What goes in each dashboard

**Fleet** (`butlers-dashboard.json`) — executive view, no filtering needed:
- Stat panels: total active sessions, queued triggers, message throughput, E2E P95
- Stacked time-series: active sessions per butler over time

**System Pressure** (`butlers-pressure.json`) — SLO/alerting view, all panels MUST filter by `$butler`:
- Session duration P50/P95/P99 (spawner latency)
- Buffer process latency P50/P95 (queue wait time)
- Route accept & process latency P50/P95
- Queued triggers per butler
- Buffer queue depth per butler (stat)
- Buffer scanner recovery rate per butler
- Do NOT include switchboard-specific metrics here (E2E latency, circuit/inflight/queue ratios, retry attempts) — those belong in the Switchboard dashboard

**Usage & Cost** (`butlers-usage.json`) — per-butler consumption, all panels MUST filter by `$butler`:
- Session rate (sessions/s) per butler
- Active sessions per butler (stacked timeseries)
- Input tokens/s by butler + model (timeseries)
- Output tokens/s by butler + model (timeseries)
- Total input tokens in window by butler + model (**stat panel** using `increase(...[$__range])` — NOT timeseries)
- Total output tokens in window by butler + model (**stat panel** using `increase(...[$__range])` — NOT timeseries)
- Buffer enqueue rate (hot/cold) per butler
- Scheduled tasks dispatched rate by butler + task_name + outcome
- Do NOT include switchboard-specific metrics here (queue dequeue by tier) — those belong in the Switchboard dashboard

**Switchboard** (`butlers-switchboard.json`) — ingestion boundary:
- Messages received by source (stacked)
- Ingest outcomes by source (success/validation_error/db_error)
- Ingress accept latency P50/P95
- Routing decision latency P50/P95
- Triage: pass_through vs matched, evaluation latency
- Thread affinity misses
- Subroute dispatch rate by destination + fanout_mode
- Subroute results by destination + outcome
- Lifecycle transitions by state + outcome
- Retry attempts by source
- Dashboard API HTTP status rates (job="butlers-dashboard")

**Butler Detail** (`butlers-butler.json`) — single butler (use single-select butler var):
- Active sessions (stat)
- Queued triggers (stat)
- Session duration P50/P95/P99
- Input + output token rates
- Buffer queue depth + process latency
- Scheduled tasks dispatched
- Route queue depth + accept latency + process latency

**Traces** (`butlers-traces.json`) — Tempo trace exploration, datasource UID `tempo`:
- Trace search table (TraceQL `{resource.service.name="butlers"}`, filterable by butler.name)
- LLM session duration histogram (from `butler.llm_session` spans)
- Trace count by root span name (rate timeseries from Tempo metrics)
- Error spans rate (spans with status=error)
- Switchboard message pipeline spans (from `butlers.switchboard.message` root spans)
- Service graph (Tempo node graph panel)
- Note: uses Tempo datasource (UID `tempo`), NOT the Prometheus `$datasource` variable

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

For butler-specific metrics (per-butler metrics module, in-progress), check `openspec/changes/per-butler-metrics-timeseries/design.md`.

### Step 3 — Decide scope

- **"Generate dashboards"** (no qualifier) → produce all 6 files in the catalog
- **"Update the pressure dashboard"** → update only `butlers-pressure.json`
- **"Add a panel for X"** → determine which dashboard owns that metric (see catalog above) and update that file

Check `grafana/` for existing files first — update rather than replace where they exist. See `references/metrics-catalogue.md` for all verified metric names.

### Step 4 — Generate JSON

Use `schemaVersion: 39`. Required structure:

```json
{
  "title": "...",
  "uid": "butlers-<slug>",
  "schemaVersion": 39,
  "refresh": "30s",
  "time": { "from": "now-3h", "to": "now" },
  "templating": { "list": [<datasource-var>, <butler-var>] },
  "panels": [ ... ]
}
```

**Standard variables** (include in every dashboard):

```json
{ "name": "datasource", "type": "datasource", "query": "prometheus", "label": "Datasource" }
```
```json
{
  "name": "butler", "type": "query", "multi": true, "includeAll": true, "label": "Butler",
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "query": { "query": "label_values(butlers_spawner_active_sessions{job=\"butlers\"}, butler)" }
}
```

For **Butler Detail** dashboard, use single-select (no `includeAll`, no `multi`) so panels focus on one butler.

**Optional variable** — include when the dashboard has environment-relevant panels:
```json
{
  "name": "deployment_environment", "type": "query", "multi": false, "includeAll": true,
  "label": "Environment",
  "datasource": { "type": "prometheus", "uid": "${datasource}" },
  "query": { "query": "label_values(target_info{job=\"butlers\"}, deployment_environment)" }
}
```

**Panel gridPos**: 24-column grid. Stat panels: `h=4`, Time series: `h=8`, Gauge: `h=6 w=6`. Row headers: `h=1, w=24`.

**Units**: use `"unit": "ms"` for milliseconds, `"percentunit"` for 0–1 ratios, `"reqps"` for rates, `"short"` for counts.

**Histogram P95 pattern**:
```
histogram_quantile(0.95, sum by(le, butler) (rate(<metric>_bucket{job="butlers", butler=~"$butler"}[$__rate_interval])))
```

**Cumulative total pattern** — for "total over window" panels use a **stat** panel (NOT timeseries). `increase(...[$__range])` produces a single aggregate value; plotting it as a timeseries yields a useless flat line:
```json
{
  "type": "stat",
  "options": { "colorMode": "value", "reduceOptions": { "calcs": ["lastNotNull"] } },
  "targets": [{ "expr": "sum by(butler, model) (increase(<counter>{job=\"butlers\", butler=~\"$butler\"}[$__range]))" }]
}
```

### Design rules

1. **Every panel must respect the `$butler` variable.** If a metric does not carry a `butler` label, it belongs in a dashboard that doesn't expose the `$butler` filter (e.g. Switchboard). Never put a `butlers_switchboard_*` metric in Fleet, Pressure, Usage, or Butler Detail — those dashboards promise per-butler filtering and switchboard metrics break that contract.
2. **Switchboard-only metrics** (`butlers_switchboard_*`, health ratio gauges, queue dequeue by tier) go exclusively in `butlers-switchboard.json`.
3. **Fleet dashboard** may include switchboard E2E latency as an aggregate overview stat (no butler filter expected), but Pressure and Usage must not.

### Step 5 — Save

Write each dashboard to its file in `grafana/`. When generating all dashboards, write all 5 files. Preserve existing panel IDs and UIDs when updating.

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

All metrics carry `job="butlers"`. Per-butler metrics also carry `butler="<name>"`. See `references/metrics-catalogue.md` for all verified metric names and label sets.
