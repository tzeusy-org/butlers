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
`GET /api/switchboard/ingestion/fanout` overview matrix remains outside this
connector-namespace migration.

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
- [Metrics Module](../modules/metrics.md) -- Butler-level Prometheus integration (separate from connector metrics)
