# Connector Heartbeat Protocol

Status: Normative (Target State)
Last updated: 2026-02-16
Primary owner: Platform/Core

## 1. Purpose

Connectors are independent processes (polling daemons, webhook listeners) that run outside the Switchboard daemon lifecycle. The system needs a mechanism to know which connectors are alive, healthy, and actively ingesting data.

This document specifies the heartbeat protocol that all connectors MUST implement to report liveness and operational statistics to the Switchboard.

Related documents:
- `docs/connectors/interface.md` (connector interface contract)
- `docs/connectors/statistics.md` (aggregation and dashboard API)
- `docs/roles/switchboard_butler.md` (Switchboard ownership of heartbeat ingestion)

## 2. Design Goals

- Connectors self-register on first heartbeat — no manual configuration needed.
- Switchboard derives connector liveness from heartbeat recency.
- Heartbeats carry lightweight operational counters for volume/error tracking.
- Protocol is simple enough that any connector (Python, Go, shell script) can implement it.

## 3. Heartbeat Envelope

Connectors submit `connector.heartbeat.v1` payloads to the Switchboard via MCP tool call.

```json
{
  "schema_version": "connector.heartbeat.v1",
  "connector": {
    "connector_type": "telegram_bot|gmail|imap|slack|...",
    "endpoint_identity": "bot-123|user@gmail.com|...",
    "instance_id": "uuid-of-this-process-instance",
    "version": "optional semver or git sha"
  },
  "status": {
    "state": "healthy|degraded|error",
    "error_message": null,
    "uptime_s": 3600
  },
  "counters": {
    "messages_ingested": 42,
    "messages_failed": 1,
    "source_api_calls": 150,
    "checkpoint_saves": 10,
    "dedupe_accepted": 0
  },
  "checkpoint": {
    "cursor": "provider-specific-cursor-value",
    "updated_at": "RFC3339 timestamp"
  },
  "sent_at": "RFC3339 timestamp"
}
```

### Field Definitions

**connector** (required):
- `connector_type`: Canonical connector type name. Must match the connector's `CONNECTOR_PROVIDER` env var value.
- `endpoint_identity`: The receiving identity this connector serves. Must match `CONNECTOR_ENDPOINT_IDENTITY`.
- `instance_id`: Stable UUID for this process instance, generated at startup. Allows distinguishing restarts and multiple instances of the same connector type.
- `version`: Optional. Connector software version for operational visibility.

**status** (required):
- `state`: One of `healthy`, `degraded`, `error`.
  - `healthy`: Normal operation, ingesting successfully.
  - `degraded`: Operational but experiencing issues (high error rate, slow source API, approaching rate limits).
  - `error`: Unable to ingest — source unreachable, auth expired, unrecoverable failure.
- `error_message`: Human-readable error context when state is `degraded` or `error`. Null when `healthy`.
- `uptime_s`: Seconds since this connector instance started.

**counters** (required):
- All counters are monotonically increasing since process start (not since last heartbeat).
- `messages_ingested`: Total messages successfully submitted to Switchboard ingest API.
- `messages_failed`: Total messages that failed ingest submission (after retries exhausted).
- `source_api_calls`: Total calls made to the source provider API.
- `checkpoint_saves`: Total checkpoint persistence operations.
- `dedupe_accepted`: Total messages accepted by Switchboard as duplicates (not errors, just already-seen).

**checkpoint** (optional):
- `cursor`: Opaque provider-specific checkpoint value (e.g., Telegram `update_id`, Gmail `historyId`).
- `updated_at`: Timestamp of last checkpoint advance.

**sent_at** (required):
- Timestamp when this heartbeat was generated. Used for clock-drift detection and latency measurement.

## 4. Transport

Heartbeats are submitted via MCP tool call to the Switchboard MCP server.

Tool name: `connector.heartbeat`

Transport is the same SSE-based MCP connection used for `ingest` calls, configured via `SWITCHBOARD_MCP_URL`. Connectors SHOULD reuse their existing MCP client connection for heartbeats.

## 5. Frequency and Staleness

### Heartbeat Interval

Connectors MUST send a heartbeat every **2 minutes** (120 seconds).

Recommended implementation:
- Use a background async task or thread that fires independently of the ingestion loop.
- Heartbeat failures MUST NOT block or crash the ingestion loop.
- If a heartbeat submission fails, log a warning and retry on the next interval.

### Staleness Thresholds

Switchboard derives connector liveness from heartbeat recency:

| Condition | Derived State |
|---|---|
| Last heartbeat < 2 minutes ago | `online` |
| Last heartbeat 2–4 minutes ago | `stale` |
| Last heartbeat > 4 minutes ago | `offline` |
| No heartbeat ever received | `unknown` (only in registry, not self-registered) |

Rules:
- `stale` connectors remain eligible for display but are flagged in the dashboard.
- `offline` connectors are flagged as down. No automatic deregistration — the record persists for historical visibility.
- Switchboard MUST NOT automatically remove connector records. Cleanup is an operator action.

## 6. Self-Registration

Connectors self-register on their first heartbeat. No pre-configuration is required.

When Switchboard receives a heartbeat from an unknown `(connector_type, endpoint_identity)` pair:
1. Create a new connector record in `connector_registry`.
2. Set `first_seen_at` to current timestamp.
3. Set `registered_via` to `"self"`.
4. Accept the heartbeat normally.

When a known connector sends a heartbeat with a new `instance_id`:
1. Update the record to reflect the new instance.
2. Log the instance change (indicates restart or replacement).
3. Preserve historical records from the previous instance.

## 7. Switchboard Persistence

### connector_registry Table

Stores the current state of each known connector.

```
connector_registry (
  connector_type      TEXT NOT NULL,
  endpoint_identity   TEXT NOT NULL,
  instance_id         UUID,
  version             TEXT,
  state               TEXT NOT NULL DEFAULT 'unknown',
  error_message       TEXT,
  uptime_s            INTEGER,
  last_heartbeat_at   TIMESTAMPTZ,
  first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  registered_via      TEXT NOT NULL DEFAULT 'self',

  -- Latest counter snapshot (monotonic since instance start)
  counter_messages_ingested   BIGINT DEFAULT 0,
  counter_messages_failed     BIGINT DEFAULT 0,
  counter_source_api_calls    BIGINT DEFAULT 0,
  counter_checkpoint_saves    BIGINT DEFAULT 0,
  counter_dedupe_accepted     BIGINT DEFAULT 0,

  -- Checkpoint state
  checkpoint_cursor    TEXT,
  checkpoint_updated_at TIMESTAMPTZ,

  PRIMARY KEY (connector_type, endpoint_identity)
)
```

### connector_heartbeat_log Table

Append-only log of heartbeat events for historical analysis and rollup input.

```
connector_heartbeat_log (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY,
  connector_type      TEXT NOT NULL,
  endpoint_identity   TEXT NOT NULL,
  instance_id         UUID,
  state               TEXT NOT NULL,
  error_message       TEXT,
  uptime_s            INTEGER,

  -- Counter snapshot at heartbeat time
  counter_messages_ingested   BIGINT,
  counter_messages_failed     BIGINT,
  counter_source_api_calls    BIGINT,

  received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at             TIMESTAMPTZ
)
PARTITION BY RANGE (received_at)
```

Retention: 7 days of raw heartbeat logs. Older data is available via rollup tables (see `docs/connectors/statistics.md`).

## 8. Heartbeat Processing Rules

On receiving a `connector.heartbeat.v1` payload, Switchboard MUST:

1. Validate the envelope against the schema.
2. Upsert `connector_registry` with the latest state, counters, and checkpoint.
3. Append to `connector_heartbeat_log` for historical tracking.
4. Compute delta counters (diff from previous snapshot) for rollup input.
5. Return acknowledgment to the connector.

Response shape:
```json
{
  "status": "accepted",
  "server_time": "RFC3339 timestamp"
}
```

The `server_time` field allows connectors to detect clock drift.

## 9. Environment Variables

Additional connector environment variable for heartbeat:

- `CONNECTOR_HEARTBEAT_INTERVAL_S` (optional, default: `120`): Heartbeat interval in seconds. Minimum: 30, maximum: 300.
- `CONNECTOR_HEARTBEAT_ENABLED` (optional, default: `true`): Set to `false` to disable heartbeats (development/testing only).

## 10. Implementation Notes

### Connector Side

Connectors SHOULD implement heartbeat as a background task that:
1. Collects current counter values from the metrics subsystem.
2. Determines current health state from recent error rates or source availability.
3. Serializes the heartbeat envelope.
4. Submits via the shared MCP client (same connection as `ingest`).
5. Logs failures but never crashes or blocks the main ingestion loop.

### Switchboard Side

Switchboard SHOULD:
- Process heartbeats asynchronously (non-blocking ingestion path).
- Use the heartbeat log as input for periodic statistics rollup jobs.
- Expose connector liveness via the dashboard API (see `docs/connectors/statistics.md`).
