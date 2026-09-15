# Dashboard Catalog — Panel Contents

Full per-dashboard panel breakdown. Load this when generating or auditing a
specific dashboard's panel set; see `SKILL.md` for the file/UID/title table
and the cross-cutting design rules (which dashboards may carry the `$butler`
filter, where switchboard-only metrics belong).

## Fleet (`butlers-dashboard.json`)

Executive view, no filtering needed:
- Stat panels: total active sessions, queued triggers, message throughput, E2E P95
- Stacked time-series: active sessions per butler over time

## System Pressure (`butlers-pressure.json`)

SLO/alerting view, all panels MUST filter by `$butler`:
- Session duration P50/P95/P99 (spawner latency)
- Buffer process latency P50/P95 (queue wait time)
- Route accept & process latency P50/P95
- Queued triggers per butler
- Buffer queue depth per butler (stat)
- Buffer scanner recovery rate per butler
- Do NOT include switchboard-specific metrics here (E2E latency, circuit/inflight/queue
  ratios, retry attempts) — those belong in the Switchboard dashboard

## Usage & Cost (`butlers-usage.json`)

Per-butler consumption, all panels MUST filter by `$butler`:
- Session rate (sessions/s) per butler
- Active sessions per butler (stacked timeseries)
- Input tokens/s by butler + model (timeseries)
- Output tokens/s by butler + model (timeseries)
- Total input tokens in window by butler + model (**stat panel** using
  `increase(...[$__range])` — NOT timeseries)
- Total output tokens in window by butler + model (**stat panel** using
  `increase(...[$__range])` — NOT timeseries)
- Buffer enqueue rate (hot/cold) per butler
- Scheduled tasks dispatched rate by butler + task_name + outcome
- Do NOT include switchboard-specific metrics here (queue dequeue by tier) — those
  belong in the Switchboard dashboard

## Switchboard (`butlers-switchboard.json`)

Ingestion boundary:
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

## Butler Detail (`butlers-butler.json`)

Single butler (use single-select butler var):
- Active sessions (stat)
- Queued triggers (stat)
- Session duration P50/P95/P99
- Input + output token rates
- Buffer queue depth + process latency
- Scheduled tasks dispatched
- Route queue depth + accept latency + process latency

## Traces (`butlers-traces.json`)

Tempo trace exploration, datasource UID `tempo`:
- Trace search table (TraceQL `{resource.service.name="butlers"}`, filterable by butler.name)
- LLM session duration histogram (from `butler.llm_session` spans)
- Trace count by root span name (rate timeseries from Tempo metrics)
- Error spans rate (spans with status=error)
- Switchboard message pipeline spans (from `butlers.switchboard.message` root spans)
- Service graph (Tempo node graph panel)
- Note: uses Tempo datasource (UID `tempo`), NOT the Prometheus `$datasource` variable
