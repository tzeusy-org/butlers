# Connector Statistics and Dashboard API

Status: Normative (Target State)
Last updated: 2026-03-04
Primary owner: Platform/Core

## 1. Purpose

This document specifies how connector ingestion statistics are aggregated, stored, and exposed via the dashboard API. The goal is to provide visibility into:

- Which external data sources are configured and alive.
- Ingestion volume and error rates per connector over time.
- How messages from each connector fan out to downstream butlers.
- Connector health and uptime trends.

Related documents:
- `docs/connectors/heartbeat.md` (heartbeat protocol and raw data)
- `docs/connectors/interface.md` (connector interface contract)
- `docs/roles/switchboard_butler.md` (Switchboard ownership)

## 2. Data Sources

Statistics are derived from two sources:

1. **Connector heartbeats** (`connector_heartbeat_log`): Counter snapshots reported every 2 minutes by each connector. Provides ingestion volume, error counts, and source API call counts.

2. **Prometheus/OTel metrics**: Time-series metrics exported by connectors and ingested via the OTel collector. Provides volume, health, and fanout metrics for dashboard time-series queries.

Note: The pre-aggregated SQL rollup tables (`connector_stats_hourly`, `connector_stats_daily`,
`connector_fanout_daily`) were dropped by migration sw_025 (butlers-ufzc). Dashboard stats
endpoints now query Prometheus via PromQL instead of pre-aggregated tables.

## 3. Metrics Pipeline

The OTel/Prometheus pipeline replaces the former SQL rollup jobs:

- Connectors emit metrics via OpenTelemetry (OTLP) to the OTel collector.
- The collector forwards to Prometheus.
- Dashboard endpoints query Prometheus using `PROMETHEUS_URL` (env var).
- All stats and fanout endpoints gracefully degrade to empty lists when `PROMETHEUS_URL` is not set.

## 4. Retention

| Source | Retention | Pruning |
|---|---|---|
| `connector_heartbeat_log` | 7 days | Daily, drop partitions older than 7 days |
| `connector_registry` | Never pruned | Operator-managed cleanup only |
| Prometheus | Configured in Prometheus retention policy | Outside Switchboard scope |

## 5. Dashboard API Endpoints

All endpoints are core API routes (in `src/butlers/api/routers/connectors.py`), not butler-specific routes.

### 5.1 List Connectors

```
GET /api/connectors
```

Returns all known connectors with their current liveness state.

Response:
```json
{
  "data": [
    {
      "connector_type": "telegram_bot",
      "endpoint_identity": "bot-123456",
      "liveness": "online",
      "state": "healthy",
      "error_message": null,
      "version": "1.2.0",
      "uptime_s": 86400,
      "last_heartbeat_at": "2026-02-16T12:00:00Z",
      "first_seen_at": "2026-01-15T08:00:00Z",
      "today": {
        "messages_ingested": 142,
        "messages_failed": 2,
        "uptime_pct": 99.1
      }
    }
  ],
  "meta": {}
}
```

Liveness is derived from `last_heartbeat_at` using the staleness thresholds defined in `docs/connectors/heartbeat.md`.

### 5.2 Connector Detail

```
GET /api/connectors/{connector_type}/{endpoint_identity}
```

Returns full detail for a single connector including current state, checkpoint, and counters.

Response:
```json
{
  "data": {
    "connector_type": "telegram_bot",
    "endpoint_identity": "bot-123456",
    "instance_id": "550e8400-e29b-41d4-a716-446655440000",
    "liveness": "online",
    "state": "healthy",
    "error_message": null,
    "version": "1.2.0",
    "uptime_s": 86400,
    "last_heartbeat_at": "2026-02-16T12:00:00Z",
    "first_seen_at": "2026-01-15T08:00:00Z",
    "registered_via": "self",
    "checkpoint": {
      "cursor": "987654321",
      "updated_at": "2026-02-16T11:58:00Z"
    },
    "counters": {
      "messages_ingested": 12500,
      "messages_failed": 23,
      "source_api_calls": 45000,
      "checkpoint_saves": 6250,
      "dedupe_accepted": 150
    }
  },
  "meta": {}
}
```

### 5.3 Connector Statistics

```
GET /api/connectors/{connector_type}/{endpoint_identity}/stats?period=24h|7d|30d
```

Returns time-series volume and health statistics for a connector, sourced from Prometheus.

Query parameters:
- `period`: Time window. One of `24h`, `7d`, `30d`. Default: `24h`.

Response:
```json
{
  "data": [
    {
      "connector_type": "telegram_bot",
      "endpoint_identity": "bot-123456",
      "hour": "2026-02-15T13:00:00Z",
      "messages_ingested": 12,
      "messages_failed": 0
    }
  ],
  "meta": {}
}
```

Timeseries granularity (Prometheus range queries):
- `24h`: hourly buckets
- `7d`: hourly buckets
- `30d`: daily buckets

Falls back to `{"data": []}` when `PROMETHEUS_URL` is not set or Prometheus returns an error.

### 5.4 Cross-Connector Summary

```
GET /api/connectors/summary?period=24h|7d|30d
```

Returns aggregate statistics across all connectors.

Response:
```json
{
  "data": {
    "period": "24h",
    "total_connectors": 3,
    "connectors_online": 2,
    "connectors_stale": 0,
    "connectors_offline": 1,
    "total_messages_ingested": 1250,
    "total_messages_failed": 8,
    "overall_error_rate_pct": 0.6,
    "by_connector": [
      {
        "connector_type": "telegram_bot",
        "endpoint_identity": "bot-123456",
        "liveness": "online",
        "messages_ingested": 800,
        "messages_failed": 3
      }
    ]
  },
  "meta": {}
}
```

### 5.5 Fanout Distribution

```
GET /api/connectors/fanout?period=7d|30d
```

Returns the connector-to-butler routing distribution matrix, sourced from Prometheus.

Response:
```json
{
  "data": [
    {
      "connector_type": "telegram_bot",
      "endpoint_identity": "bot@123",
      "target_butler": "health",
      "message_count": 15
    }
  ],
  "meta": {}
}
```

Falls back to `{"data": []}` when `PROMETHEUS_URL` is not set or Prometheus returns an error.

## 6. Frontend Page: /connectors

The dashboard frontend exposes a `/connectors` page with the following views:

### 6.1 Connector Overview Cards

One card per registered connector showing:
- Connector type icon (Telegram, Gmail, etc.)
- Endpoint identity
- Liveness badge (online/stale/offline) with color coding
- Self-reported health state (healthy/degraded/error)
- Uptime percentage (today)
- Last heartbeat age (e.g., "2 min ago")
- Today's ingestion count

### 6.2 Volume Time Series

Line or bar chart showing ingestion volume per connector over the selected time period.

Controls:
- Period selector: 24h / 7d / 30d
- Toggle per-connector visibility

### 6.3 Fanout Distribution

Table or heatmap showing the connector x butler routing matrix.

Columns: target butlers
Rows: connectors
Cells: message count for the selected period

### 6.4 Error Log

Recent connector errors (from heartbeats with `state != healthy`).

Columns:
- Timestamp
- Connector type + identity
- State (degraded/error)
- Error message

## 7. Pydantic Models

Core response models for the connectors API (in `src/butlers/api/models/connector.py`):

```python
class ConnectorSummary(BaseModel):
    connector_type: str
    endpoint_identity: str
    liveness: str        # online, stale, offline
    state: str           # healthy, degraded, error
    error_message: str | None = None
    version: str | None = None
    uptime_s: int | None = None
    last_heartbeat_at: datetime | None = None
    first_seen_at: datetime
    today: ConnectorDaySummary | None = None

class ConnectorDaySummary(BaseModel):
    messages_ingested: int = 0
    messages_failed: int = 0
    uptime_pct: float | None = None

class ConnectorDetail(ConnectorSummary):
    instance_id: UUID | None = None
    registered_via: str = "self"
    checkpoint: ConnectorCheckpoint | None = None
    counters: ConnectorCounters | None = None

class ConnectorCheckpoint(BaseModel):
    cursor: str | None = None
    updated_at: datetime | None = None

class ConnectorCounters(BaseModel):
    messages_ingested: int = 0
    messages_failed: int = 0
    source_api_calls: int = 0
    checkpoint_saves: int = 0
    dedupe_accepted: int = 0

class ConnectorStatsBucket(BaseModel):
    bucket: datetime
    messages_ingested: int = 0
    messages_failed: int = 0
    healthy_count: int = 0
    degraded_count: int = 0
    error_count: int = 0

class ConnectorStats(BaseModel):
    connector_type: str
    endpoint_identity: str
    period: str
    summary: ConnectorStatsSummary
    timeseries: list[ConnectorStatsBucket]

class ConnectorStatsSummary(BaseModel):
    messages_ingested: int = 0
    messages_failed: int = 0
    error_rate_pct: float = 0.0
    uptime_pct: float | None = None
    avg_messages_per_hour: float = 0.0

class ConnectorFanoutEntry(BaseModel):
    connector_type: str
    endpoint_identity: str
    targets: dict[str, int]    # butler_name -> message_count
```
