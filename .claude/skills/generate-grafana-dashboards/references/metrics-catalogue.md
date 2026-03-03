# Butlers Metrics Catalogue

All metric names **verified against live Prometheus** (`https://prometheus.parrot-hen.ts.net`).
Last verified: 2026-03-03. Always re-query Prometheus before generating — new metrics may have been added.

Quick discovery query:
```
GET /api/v1/label/__name__/values?match[]={job="butlers"}
```

---

## Spawner (`butlers_spawner_*`)

All carry `butler` label.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `butlers_spawner_active_sessions` | Gauge | `butler` | Current concurrent LLM sessions |
| `butlers_spawner_queued_triggers` | Gauge | `butler` | Triggers waiting for semaphore slot |
| `butlers_spawner_session_duration_ms_milliseconds` | Histogram | `butler` | End-to-end session wall time |
| `butlers_spawner_input_tokens_tokens_total` | Counter | `butler`, `model` | LLM input tokens consumed per session |
| `butlers_spawner_output_tokens_tokens_total` | Counter | `butler`, `model` | LLM output tokens produced per session |

---

## Buffer (`butlers_buffer_*`)

All carry `butler` label.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `butlers_buffer_queue_depth_messages` | Gauge | `butler` | Current in-memory queue depth |
| `butlers_buffer_enqueue_messages_total` | Counter | `butler`, `path=hot\|cold` | Messages enqueued; `path` distinguishes hot-path vs scanner recovery |
| `butlers_buffer_scanner_recovered_messages_total` | Counter | `butler` | Messages recovered by periodic scanner |
| `butlers_buffer_process_latency_ms_milliseconds` | Histogram | `butler` | Queue wait time: enqueue → processing start |

Note: `butlers_buffer_backpressure_total` (backpressure events) has not been observed in Prometheus — it may never have fired or the name differs. Omit unless confirmed.

---

## Route / Inter-Butler (`butlers_route_*`)

All carry `butler` label.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `butlers_route_queue_depth_requests` | Gauge | `butler` | Accepted-but-unprocessed route inbox rows |
| `butlers_route_accept_latency_ms_milliseconds` | Histogram | `butler` | Time for target butler to ack receipt |
| `butlers_route_process_latency_ms_milliseconds` | Histogram | `butler` | Time from inbox insert to processing start |

---

## Switchboard Ingress (`butlers_switchboard_*` — ingress path)

No `butler` label. Labels: `source` (email, telegram), `model_family`, `policy_tier`, `prompt_version`, `schema_version`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `butlers_switchboard_message_received_total` | Counter | `source`, `model_family`, `policy_tier`, `prompt_version`, `schema_version` | Messages entering the switchboard |
| `butlers_switchboard_ingress_accept_latency_ms_milliseconds` | Histogram | same | Time to accept an ingress message |
| `butlers_switchboard_routing_decision_latency_ms_milliseconds` | Histogram | same | Time for LLM routing decision |
| `butlers_switchboard_end_to_end_latency_ms_milliseconds` | Histogram | `source`, `model_family`, `outcome`, `policy_tier`, `prompt_version`, `schema_version` | Total wall time from receipt to completion |
| `butlers_switchboard_lifecycle_transition_total` | Counter | `source`, `lifecycle_state` (accepted, parsed), `outcome`, `model_family`, `policy_tier`, `prompt_version`, `schema_version` | State machine transitions |
| `butlers_switchboard_thread_affinity_miss_total` | Counter | `source`, `reason` (no_history), `schema_version` | Thread affinity cache misses |
| `butlers_switchboard_retry_attempt_total` | Counter | `source` | Retry attempts |

---

## Switchboard Fanout (`butlers_switchboard_subroute_*`)

Labels: `destination_butler`, `fanout_mode` (tool_routed, ordered), `source`, `outcome`, `schema_version`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `butlers_switchboard_subroute_dispatched_total` | Counter | `destination_butler`, `fanout_mode`, `source`, `outcome`, `schema_version` | Subroute dispatch attempts |
| `butlers_switchboard_subroute_latency_ms_milliseconds` | Histogram | same | End-to-end subroute latency |
| `butlers_switchboard_subroute_result_total` | Counter | same | Final subroute outcomes |

Observed destination butlers: `general`, `education`, `messenger`, `finance`
Observed sources: `switchboard`, `education`, `general`, `home`

---

## Switchboard Health (ratio gauges)

No `butler` label. Labels: `schema_version`.

| Metric | Type | Value range | Description |
|---|---|---|---|
| `butlers_switchboard_circuit_open_targets_ratio` | Gauge | 0–1 | Fraction of targets with open circuit breaker; 0 = all healthy |
| `butlers_switchboard_inflight_requests_ratio` | Gauge | 0–1 | Inflight vs capacity; alert yellow >0.7, red >0.9 |
| `butlers_switchboard_queue_depth_ratio` | Gauge | 0–1 | Queue fill level |
| `butlers_switchboard_queue_dequeue_by_tier_messages_total` | Counter | — | Dequeues by `policy_tier`, `queue_name`, `starvation_override` |

---

## Scheduler (`butlers_scheduler_*`)

| Metric | Type | Labels | Description |
|---|---|---|---|
| `butlers_scheduler_tasks_dispatched_tasks_total` | Counter | `butler`, `task_name`, `outcome=success\|failure` | Scheduled tasks dispatched, tagged by outcome |

---

## Switchboard Ingest Boundary (`butlers_switchboard_ingest_result_*`)

| Metric | Type | Labels | Description |
|---|---|---|---|
| `butlers_switchboard_ingest_result_requests_total` | Counter | `source`, `outcome=success\|validation_error\|db_error` | Ingest boundary outcomes per source channel |

---

## Dashboard API (`http_server_*`)

Emitted by FastAPI OTel auto-instrumentation when `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
Job label: `butlers-dashboard`.

| Metric | Type | Labels | Description |
|---|---|---|---|
| `http_server_request_duration_milliseconds` | Histogram | `http_response_status_code`, `http_request_method`, `url_scheme` | HTTP request duration; `_count` gives request rate |

---

## Resource Attributes

The `target_info` metric exposes resource attributes as labels. When `ENV` environment variable is set (e.g. `ENV=prod`), `deployment_environment` label is populated on `target_info{job="butlers"}`. Use `label_values(target_info{job="butlers"}, deployment_environment)` for the Grafana variable query.

---

## Docket Scheduler (`docket_*`)

These come from FastMCP internals — not Butlers code. Label: `docket_name=fastmcp`.

| Metric | Type | Description |
|---|---|---|
| `docket_queue_depth_ratio` | Gauge | FastMCP task queue fill (0=empty, 1=full) |
| `docket_schedule_depth_ratio` | Gauge | Pending scheduled tasks fill ratio |
| `docket_cache_size_ratio` | Gauge | Cache fill ratio (may not have fired yet) |

---

## Grafana Recommended Thresholds

| Metric | Green | Yellow | Red |
|---|---|---|---|
| Active sessions | <3 | 3–8 | >8 |
| Queued triggers | 0 | 1–3 | >3 |
| Circuit open targets ratio | 0 | — | >0 |
| Inflight / queue depth ratio | <0.7 | 0.7–0.9 | >0.9 |
| E2E latency P95 | <30s | 30–60s | >60s |
