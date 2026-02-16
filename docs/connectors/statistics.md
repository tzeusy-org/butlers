# Connector Statistics and Dashboard API

Status: Normative (Target State)
Last updated: 2026-02-16
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

2. **Message inbox** (`message_inbox`): Switchboard's canonical ingress lifecycle table. Provides routing/fanout outcomes — which butlers received messages from which connectors.

## 3. Pre-Aggregated Rollups

Raw heartbeat logs and message inbox rows are rolled up into pre-aggregated tables for efficient dashboard queries.

### 3.1 Connector Hourly Rollup

```
connector_stats_hourly (
  connector_type      TEXT NOT NULL,
  endpoint_identity   TEXT NOT NULL,
  hour                TIMESTAMPTZ NOT NULL,  -- truncated to hour

  -- Volume (derived from counter deltas between heartbeats)
  messages_ingested   BIGINT NOT NULL DEFAULT 0,
  messages_failed     BIGINT NOT NULL DEFAULT 0,
  source_api_calls    BIGINT NOT NULL DEFAULT 0,
  dedupe_accepted     BIGINT NOT NULL DEFAULT 0,

  -- Health (derived from heartbeat states in this hour)
  heartbeat_count     INTEGER NOT NULL DEFAULT 0,
  healthy_count       INTEGER NOT NULL DEFAULT 0,
  degraded_count      INTEGER NOT NULL DEFAULT 0,
  error_count         INTEGER NOT NULL DEFAULT 0,

  PRIMARY KEY (connector_type, endpoint_identity, hour)
)
```

### 3.2 Connector Daily Rollup

```
connector_stats_daily (
  connector_type      TEXT NOT NULL,
  endpoint_identity   TEXT NOT NULL,
  day                 DATE NOT NULL,

  -- Volume (sum of hourly)
  messages_ingested   BIGINT NOT NULL DEFAULT 0,
  messages_failed     BIGINT NOT NULL DEFAULT 0,
  source_api_calls    BIGINT NOT NULL DEFAULT 0,
  dedupe_accepted     BIGINT NOT NULL DEFAULT 0,

  -- Health (sum of hourly)
  heartbeat_count     INTEGER NOT NULL DEFAULT 0,
  healthy_count       INTEGER NOT NULL DEFAULT 0,
  degraded_count      INTEGER NOT NULL DEFAULT 0,
  error_count         INTEGER NOT NULL DEFAULT 0,

  -- Uptime (derived)
  uptime_pct          REAL,  -- healthy_count / heartbeat_count * 100

  PRIMARY KEY (connector_type, endpoint_identity, day)
)
```

### 3.3 Fanout Distribution Rollup

Tracks how messages from each connector get routed to each butler. Derived from `message_inbox.dispatch_outcomes` JSONB.

```
connector_fanout_daily (
  connector_type      TEXT NOT NULL,   -- source_channel from message_inbox
  endpoint_identity   TEXT NOT NULL,   -- source_endpoint_identity from message_inbox
  target_butler       TEXT NOT NULL,
  day                 DATE NOT NULL,

  message_count       BIGINT NOT NULL DEFAULT 0,

  PRIMARY KEY (connector_type, endpoint_identity, target_butler, day)
)
```

## 4. Rollup Jobs

### 4.1 Hourly Rollup

Runs every hour (on the hour). Processes:
- `connector_heartbeat_log` rows from the previous hour.
- Computes counter deltas between consecutive heartbeat snapshots for each connector.
- Counts heartbeat states (healthy/degraded/error) for health metrics.
- Upserts into `connector_stats_hourly`.

### 4.2 Daily Rollup

Runs once daily (after midnight UTC). Processes:
- Sums `connector_stats_hourly` rows for the previous day.
- Computes `uptime_pct`.
- Upserts into `connector_stats_daily`.

### 4.3 Fanout Rollup

Runs once daily. Processes:
- `message_inbox` rows from the previous day.
- Groups by `(source_channel, source_endpoint_identity, target_butler)` extracted from `dispatch_outcomes`.
- Upserts into `connector_fanout_daily`.

### 4.4 Scheduling

Rollup jobs are Switchboard scheduled tasks (cron-based via butler.toml):

```toml
[[butler.schedule]]
name = "connector-stats-hourly-rollup"
cron = "5 * * * *"
prompt = "Run the hourly connector statistics rollup."

[[butler.schedule]]
name = "connector-stats-daily-rollup"
cron = "15 0 * * *"
prompt = "Run the daily connector statistics rollup and fanout distribution rollup."
```

## 5. Retention and Pruning

| Table | Retention | Pruning Schedule |
|---|---|---|
| `connector_heartbeat_log` | 7 days | Daily, drop partitions older than 7 days |
| `connector_stats_hourly` | 30 days | Daily, delete rows older than 30 days |
| `connector_stats_daily` | 1 year | Monthly, delete rows older than 1 year |
| `connector_fanout_daily` | 1 year | Monthly, delete rows older than 1 year |
| `connector_registry` | Never pruned | Operator-managed cleanup only |

Pruning rules:
- Pruning jobs run as Switchboard scheduled tasks.
- Pruning MUST be idempotent and safe to re-run.
- Pruning MUST log what was removed (row counts, date ranges).

## 6. Dashboard API Endpoints

All endpoints are core API routes (in `src/butlers/api/routers/connectors.py`), not butler-specific routes. They query the Switchboard database directly.

### 6.1 List Connectors

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

### 6.2 Connector Detail

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

### 6.3 Connector Statistics

```
GET /api/connectors/{connector_type}/{endpoint_identity}/stats?period=24h|7d|30d
```

Returns time-series volume and health statistics for a connector.

Query parameters:
- `period`: Time window. One of `24h`, `7d`, `30d`. Default: `24h`.

Response:
```json
{
  "data": {
    "connector_type": "telegram_bot",
    "endpoint_identity": "bot-123456",
    "period": "24h",
    "summary": {
      "messages_ingested": 342,
      "messages_failed": 5,
      "error_rate_pct": 1.4,
      "uptime_pct": 98.5,
      "avg_messages_per_hour": 14.25
    },
    "timeseries": [
      {
        "bucket": "2026-02-15T13:00:00Z",
        "messages_ingested": 12,
        "messages_failed": 0,
        "healthy_count": 30,
        "degraded_count": 0,
        "error_count": 0
      }
    ]
  },
  "meta": {}
}
```

Timeseries granularity:
- `24h`: hourly buckets (from `connector_stats_hourly`)
- `7d`: hourly buckets (from `connector_stats_hourly`)
- `30d`: daily buckets (from `connector_stats_daily`)

### 6.4 Cross-Connector Summary

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
      },
      {
        "connector_type": "gmail",
        "endpoint_identity": "user@gmail.com",
        "liveness": "online",
        "messages_ingested": 450,
        "messages_failed": 5
      }
    ]
  },
  "meta": {}
}
```

### 6.5 Fanout Distribution

```
GET /api/connectors/fanout?period=7d|30d
```

Returns the connector-to-butler routing distribution matrix.

Response:
```json
{
  "data": {
    "period": "7d",
    "matrix": [
      {
        "connector_type": "telegram_bot",
        "endpoint_identity": "bot-123456",
        "targets": {
          "health": 320,
          "relationship": 180,
          "general": 45
        }
      },
      {
        "connector_type": "gmail",
        "endpoint_identity": "user@gmail.com",
        "targets": {
          "relationship": 280,
          "general": 120,
          "health": 50
        }
      }
    ]
  },
  "meta": {}
}
```

## 7. Frontend Page: /connectors

The dashboard frontend exposes a `/connectors` page with the following views:

### 7.1 Connector Overview Cards

One card per registered connector showing:
- Connector type icon (Telegram, Gmail, etc.)
- Endpoint identity
- Liveness badge (online/stale/offline) with color coding
- Self-reported health state (healthy/degraded/error)
- Uptime percentage (today)
- Last heartbeat age (e.g., "2 min ago")
- Today's ingestion count

### 7.2 Volume Time Series

Line or bar chart showing ingestion volume per connector over the selected time period.

Controls:
- Period selector: 24h / 7d / 30d
- Toggle per-connector visibility

### 7.3 Fanout Distribution

Table or heatmap showing the connector x butler routing matrix.

Columns: target butlers
Rows: connectors
Cells: message count for the selected period

### 7.4 Error Log

Recent connector errors (from heartbeats with `state != healthy`).

Columns:
- Timestamp
- Connector type + identity
- State (degraded/error)
- Error message

## 8. Pydantic Models

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
