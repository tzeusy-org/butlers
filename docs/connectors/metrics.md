# Connector Metrics
> **Purpose:** Standardized Prometheus instrumentation for all connector runtimes, covering ingestion, source API calls, checkpoints, errors, and attachments.
> **Audience:** Contributors.
> **Prerequisites:** [Connector Interface](overview.md), [Heartbeat Protocol](heartbeat.md).

## Overview

The connector metrics module (`src/butlers/connectors/metrics.py`) provides a uniform Prometheus metrics surface for connector observability. Every connector instance creates a `ConnectorMetrics` object bound to its `connector_type` and `endpoint_identity`, then calls convenience methods to record events. The module also defines global `prometheus_client` Counter and Histogram objects that aggregate across all connector instances in a process.

## Data Sources

Connector statistics are derived from two sources:

1. **Connector heartbeats** (`connector_heartbeat_log`): Counter snapshots reported every 2 minutes by each connector. Provides ingestion volume, error counts, and source API call counts. Retained for 7 days.
2. **Prometheus/OTel metrics**: Time-series metrics exported by connectors via OTLP to the OTel collector, which forwards to Prometheus. Dashboard endpoints query Prometheus using `PROMETHEUS_URL` (env var) and gracefully degrade to empty results when not configured.

## Metrics Catalogue

### Core Metrics

All metrics include `connector_type` and `endpoint_identity` labels.

| Metric | Type | Additional Labels | Description |
|---|---|---|---|
| `connector_ingest_submissions_total` | Counter | `status` | Ingest API submission attempts. Status: `success`, `error`, `duplicate`. |
| `connector_ingest_latency_seconds` | Histogram | `status` | Ingest API latency. Buckets: 5ms to 10s. |
| `connector_source_api_calls_total` | Counter | `api_method`, `status` | Calls to external source APIs (e.g., `getUpdates`, `history.list`). |
| `connector_checkpoint_saves_total` | Counter | `status` | Checkpoint persistence operations. Status: `success` or `error`. |
| `connector_errors_total` | Counter | `error_type`, `operation` | Errors by semantic type and failing operation. |

### Attachment Metrics

| Metric | Type | Additional Labels | Description |
|---|---|---|---|
| `connector_attachment_fetched_eager_total` | Counter | `media_type`, `result` | Eagerly fetched attachments at ingest time. |
| `connector_attachment_fetched_lazy_total` | Counter | `media_type`, `result` | Lazy ref writes and on-demand materializations. |
| `connector_attachment_skipped_oversized_total` | Counter | `media_type` | Attachments skipped due to per-type or global size cap. |
| `connector_attachment_type_distribution_total` | Counter | `media_type` | Processed attachments by MIME type. |

## Label Bounds

Every `prometheus_client` label value must come from a closed set, or from a bounded normalization with a fixed fallback, and must not carry account PII (see [RFC 0005 Cardinality Discipline](../../about/legends-and-lore/rfcs/0005-observability-and-telemetry.md#cardinality-discipline)). Two labels are clamped centrally in `src/butlers/connectors/metrics.py`; the rest are bounded by code or configuration.

### Clamped labels

| Label | Where | Bound |
|---|---|---|
| `endpoint_identity` | `ConnectorMetrics` and the Gmail policy counters | `<family>:<8 hex>` from `prometheus_endpoint_identity()`. `family` is the identity's leading lowercase token (else `unknown`); the digest is the first 8 hex characters of SHA-256 of the full identity. Stable across restarts, one value per account, never the raw email, phone number or handle. Pseudonymous, not anonymous: the digest is unkeyed, so anyone holding a candidate identity can recompute it to find the series, and identities from a small space (phone numbers, numeric account IDs, short handles) can be recovered by enumerating candidates. Treat the label as access-controlled operator data. Empty identity is `unknown`. |
| `media_type` | the four attachment metrics | `prometheus_media_type()`: a fixed list of common MIME types, else `<major>/other` for a registered top-level type, else `other`. Case and parameters are dropped first. |

Instruments that label `endpoint_identity` directly instead of through `ConnectorMetrics` (the Google Drive, Google Health, Home Assistant, Steam, Spotify, OwnTracks and ActivityWatch module-level instruments; Google Health values have the form `google_health:user:<google user id>:<resource>`) still carry the raw value until they adopt `prometheus_endpoint_identity()`.

### Bounded by code or configuration

| Label | Metrics | Bound |
|---|---|---|
| `connector_type` | all connector metrics | Connector family names defined in code. |
| `status`, `result`, `outcome`, `verdict`, `stage`, `operation` | connector and discretion metrics | Fixed literals at each call site. |
| `api_method` | `connector_source_api_calls_total` | Literal API method names at each call site. |
| `error_type` | `connector_errors_total`, live listener failures | `get_error_type()` result: five semantic values, else the lowercased exception class name. Exception classes come from code, never from the sender. |
| `chat_type` | `butlers_interaction_gated_total` | `private`, `group`, `supergroup`, `channel`, derived from the platform chat entity. |
| `participant_count_bucket` | `butlers_interaction_gated_total` | `participant_count_bucket()`: `1-20`, `21-50`, `51-200`, `201-1000`, `1000+`. |
| `predicate` | `health_wellness_ingest_total` | Predicates from the health wellness ingest tables (`_RESOURCE_TO_PREDICATES` and `measurement_<metric>`), plus `unknown`. |
| `app_class` | ActivityWatch window events | `AppClass` literal: `ide`, `terminal`, `browser`, `other`. |
| `mic` | live listener metrics | Operator-configured microphone names. |
| `http_status` | Steam API errors | HTTP status codes of the upstream response. |
| `event_type` | Home Assistant, Google Drive, OwnTracks, Steam | Event-type names defined by the connector. |
| `source`, `channel`, `kind` | discretion metrics | Connector source names and `classify_ignore_kind()` results, defined in code. |
| `runtime_type` | `discretion_auth_failures_total` | Configured runtime adapter types. |
| `error_detail` | `switchboard_runtime_attention_delivery_total` | The `TransportErrorDetail` StrEnum, six fixed codes (`recipient_unavailable`, `policy_denied`, `provider_rejected`, `transport_timeout`, `transport_connection_lost`, `worker_recovery`), plus `none`. `TransportResult` rejects any other value at construction. |
| `ingestion_tier`, `policy_tier`, `assignment_rule` | Gmail tier counters | Code literals in `gmail_policy.py`. |
| `filter_action` | Gmail label-filter counter | `allowed` or `excluded`. |
| `reason` (label filter) | Gmail label-filter counter | `label_allowed`, `label_not_in_include_list`, or `label_exclude:<labels>` where the labels are the subset of the configured exclude list that matched, so it is bounded by configuration, not by code. |
| `data_type`, `filter_reason` | Steam poll and filter counters | Code-defined cursor data types and filter reasons. |
| `resource` | Google Health counters | Code-defined resource names (`sleep`, `activity`, `resting_hr`, `hrv` and the other call-site literals). |
| `metric` | `connector_ha_wellness_classify_total` | The matched wellness metric name from the connector's table, or `none`. |
| `edge` | `runtime_attention_recorder_total` | Code-defined runtime-attention edge results. |
| `schema`, `kind`, `source_type`, `dedup_reason`, `status`, `action`, `failure_category`, `reason` | approvals, audit, QA, chronicler and person-mapping counters | Butler schema names, and code-defined enums or literals. |

## ConnectorMetrics Class

Each connector instance creates a `ConnectorMetrics` object at startup:

```python
metrics = ConnectorMetrics(
    connector_type="telegram_bot",
    endpoint_identity="bot-123456",
)
```

Convenience methods bind identity labels automatically: `record_ingest_submission`, `track_ingest_submission` (context manager with auto-timing), `record_source_api_call`, `record_checkpoint_save`, `record_error`, `record_attachment_fetched`, `record_attachment_skipped_oversized`, and `record_attachment_type_distribution`.

## Error Type Classification

The `get_error_type(exc)` helper maps exception class names to semantic error labels:

| Exception Pattern | Error Type |
|---|---|
| `*HTTPStatus*`, `*HTTP*` | `http_error` |
| `*Timeout*` | `timeout` |
| `*ConnectionError*`, `*ConnectError*` | `connection_error` |
| `*JSON*`, `*Parse*` | `parse_error` |
| `*ValueError*`, `*ValidationError*` | `validation_error` |
| Other | Lowercased class name |

## Retention

| Source | Retention | Pruning |
|---|---|---|
| `connector_heartbeat_log` | 7 days | Daily, drop partitions older than 7 days |
| `connector_registry` | Never pruned | Operator-managed cleanup only |
| Prometheus | Configured in Prometheus retention policy | Outside Switchboard scope |

## Dashboard API Endpoints

Dashboard connector routes live in
`src/butlers/api/routers/ingestion_connectors.py` under
`/api/ingestion/connectors`. The role-aware roster is
`GET /summaries`; connector detail, statistics, and runtime-settings updates
live at `/{type}/{identity}`, `/{type}/{identity}/stats`, and
`/{type}/{identity}/settings`. `GET /cross-summary` is the fleet aggregate.

The roster, detail, and statistics routes are database-sourced. The aggregate
route reports `aggregates_available: false` when its Prometheus-backed metrics
cannot be read; callers must render that as unavailable rather than zero. The
retired Switchboard connector namespace and its per-connector fanout endpoint
are not dashboard API surfaces. The separate cross-connector
`GET /api/switchboard/ingestion/fanout` route remains outside this
connector-namespace migration, but the active Connectors dashboard renders its
7-day routing distribution. Its `meta.aggregates_available` flag distinguishes
a measured empty response from an unavailable aggregate source. Exact
connector and endpoint dimensions come from the sessions-to-ingestion-events
DB projection. The handler separately probes the exact
`butlers_switchboard_subroute_dispatched_total` family for attempted dispatches
carrying bounded `source="connector"` provenance and a non-empty
`destination_butler`; raw connector and endpoint identities never become OTel
labels. A live producer plus at least one successfully queried DB target and
no failed targets makes an empty result measured. Zero queried targets, an
absent or unreadable producer, or any failed target is degraded and never
presented as an all-clear empty distribution. Successful target rows remain
in the API response when another target fails.

## Verification

To confirm connector metrics are being emitted and surfaced correctly:

```bash
# 1. Core metrics are present in Prometheus for active connectors
curl -s "http://localhost:9090/api/v1/query?query=connector_ingest_submissions_total" \
  | python3 -m json.tool | grep -E "connector_type|endpoint_identity|value"
# Expected: entries for each running connector with non-zero success count

# 2. Ingest latency histogram is populated
curl -s "http://localhost:9090/api/v1/query?query=connector_ingest_latency_seconds_bucket" \
  | python3 -m json.tool | grep "le" | head -10
# Expected: histogram buckets from 5ms to 10s with non-zero counts

# 3. Dashboard API roster exposes only runtime-authoritative connectors
curl -s "http://localhost:41200/api/ingestion/connectors/summaries" | python3 -m json.tool | grep -E "liveness|operational_role"
# Expected: runtime instances have liveness; checkpoint rows are nested under
#           their parent rather than appearing as offline connectors

# 4. Heartbeat log accumulates entries (source 1) separate from Prometheus (source 2)
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, COUNT(*) as heartbeat_count, MAX(received_at) as last_heartbeat
   FROM switchboard.connector_heartbeat_log
   WHERE received_at > NOW() - INTERVAL '1 hour'
   GROUP BY connector_type;"
# Expected: one row per active connector; count should be ~30 per hour (one every 2 minutes)

# 5. Degraded mode: fleet aggregate returns aggregates_available=false when Prometheus is down
PROMETHEUS_URL=http://localhost:1 curl -s http://localhost:41200/api/ingestion/connectors/cross-summary \
  | python3 -m json.tool | grep aggregates_available
# Expected: {"aggregates_available": false} rather than a 500 error
```

## Related Pages

- [Connector Interface](overview.md) -- Shared connector contract and lifecycle
- [Heartbeat Protocol](heartbeat.md) -- Liveness signaling that complements these metrics
- [Attachment Handling](attachment-handling.md) -- Attachment-specific metric semantics
- [Observability](../architecture/observability.md) -- Shared Prometheus query transport (separate from connector instrumentation)
