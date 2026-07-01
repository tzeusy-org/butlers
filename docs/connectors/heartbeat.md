# Connector Heartbeat Protocol

> **Purpose:** Specify the heartbeat protocol that all connectors implement to report liveness and operational statistics to the Switchboard.
> **Audience:** Developers building connectors or operating the Switchboard.
> **Prerequisites:** [Connector Architecture Overview](overview.md).

## Overview

Connectors are independent processes that run outside the Switchboard daemon lifecycle. The heartbeat protocol provides the mechanism for the system to know which connectors are alive, healthy, and actively ingesting data. All connectors MUST implement this protocol.

Connectors self-register on their first heartbeat -- no manual pre-configuration is required.

## Heartbeat Envelope

Connectors submit `connector.heartbeat.v1` payloads via MCP tool call (`connector.heartbeat`) using the same SSE-based MCP connection configured via `SWITCHBOARD_MCP_URL`.

```json
{
  "schema_version": "connector.heartbeat.v1",
  "connector": {
    "connector_type": "telegram_bot|gmail|telegram_user_client|live_listener|...",
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
  "capabilities": {
    "backfill": true
  },
  "sent_at": "RFC3339 timestamp"
}
```

### Field Reference

**connector** (required):

| Field | Description |
|---|---|
| `connector_type` | Canonical type name; matches `CONNECTOR_PROVIDER` env var |
| `endpoint_identity` | Receiving identity, auto-resolved at startup |
| `instance_id` | Stable UUID for this process instance, generated at startup |
| `version` | Optional software version for operational visibility |

**status** (required):

| Field | Description |
|---|---|
| `state` | `healthy` (normal), `degraded` (operational with issues), `error` (unable to ingest) |
| `error_message` | Human-readable context when `degraded` or `error`; null when `healthy` |
| `uptime_s` | Seconds since this connector instance started |

**counters** (required): All counters are monotonically increasing since process start.

| Counter | Description |
|---|---|
| `messages_ingested` | Successfully submitted to Switchboard ingest API |
| `messages_failed` | Failed ingest submission after retries exhausted |
| `source_api_calls` | Total calls to source provider API |
| `checkpoint_saves` | Total checkpoint persistence operations |
| `dedupe_accepted` | Messages accepted by Switchboard as duplicates |

**checkpoint** (optional): Opaque provider-specific cursor value and last advance timestamp.

**capabilities** (optional): Feature flags like `backfill: true` for dashboard control rendering.

**sent_at** (required): Generation timestamp for clock-drift detection and latency measurement.

## Frequency and Staleness

Connectors MUST send a heartbeat every **2 minutes** (120 seconds).

The heartbeat runs as a background async task independent of the ingestion loop. Heartbeat failures MUST NOT block or crash ingestion.

### Staleness Thresholds

The Switchboard derives connector liveness from heartbeat recency:

| Condition | Derived state |
|---|---|
| Last heartbeat < 2 min ago | `online` |
| Last heartbeat 2-4 min ago | `stale` |
| Last heartbeat > 4 min ago | `offline` |
| No heartbeat ever received | `unknown` |

Rules:
- `stale` connectors remain eligible for display but are flagged in the dashboard.
- `offline` connectors are flagged as down. No automatic deregistration.
- The Switchboard MUST NOT automatically remove connector records. Cleanup is an operator action.

## Self-Registration

When the Switchboard receives a heartbeat from an unknown `(connector_type, endpoint_identity)` pair:

1. Create a new `connector_registry` record.
2. Set `first_seen_at` to current timestamp.
3. Set `registered_via` to `"self"`.
4. Accept the heartbeat normally.

When a known connector sends a heartbeat with a new `instance_id`, the record is updated and the instance change is logged (indicates restart or replacement).

## Implementation

The shared heartbeat implementation lives in `butlers.connectors.heartbeat`:

- `HeartbeatConfig` -- Configuration dataclass with `from_env()` factory that reads `CONNECTOR_HEARTBEAT_INTERVAL_S` and `CONNECTOR_HEARTBEAT_ENABLED`.
- `ConnectorHeartbeat` -- Background task manager that generates a stable `instance_id`, runs a loop at the configured interval, collects counter values from Prometheus metrics, determines health state via a caller-provided callback, and submits the envelope via `CachedMCPClient`.

The implementation reads counter values directly from the Prometheus registry (`connector_ingest_submissions_total`, `connector_source_api_calls_total`, `connector_checkpoint_saves_total`) with label filtering by `connector_type` and `endpoint_identity`.

## Switchboard Persistence

### connector_registry

Stores current state of each known connector. Primary key: `(connector_type, endpoint_identity)`. Includes latest counter snapshot, checkpoint state, health state, and instance metadata.

### connector_heartbeat_log

Append-only log of heartbeat events, partitioned by `received_at`. Used for historical analysis and rollup input. Retention: 7 days.

## Processing Rules

On receiving a heartbeat, the Switchboard:

1. Validates the envelope against the schema.
2. Upserts `connector_registry` with latest state, counters, and checkpoint.
3. Appends to `connector_heartbeat_log`.
4. Computes delta counters for rollup input.
5. Returns acknowledgment with `server_time` for clock-drift detection.

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `CONNECTOR_HEARTBEAT_INTERVAL_S` | No | 120 | Heartbeat interval (min: 30, max: 300) |
| `CONNECTOR_HEARTBEAT_ENABLED` | No | true | Set to `false` for dev/testing only |

## Verification

To confirm the heartbeat protocol is functioning as specified:

```bash
# 1. Connectors appear in connector_registry after their first heartbeat
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, state,
          NOW() - last_heartbeat_at AS heartbeat_age
   FROM switchboard.connector_registry ORDER BY connector_type;"
# Expected: all running connectors present; heartbeat_age < 2 minutes (online threshold)

# 2. Liveness state transitions correctly (online/stale/offline)
# Stop a connector and wait 3 minutes, then query liveness
# Expected: state transitions from 'online' → 'stale' at 2 min → 'offline' at 4 min

# 3. connector_heartbeat_log accumulates entries at ~2-minute intervals
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, received_at
   FROM switchboard.connector_heartbeat_log
   ORDER BY received_at DESC LIMIT 10;"
# Expected: entries spaced ~120 seconds apart per connector; 7-day retention enforced

# 4. Heartbeat acknowledgment includes server_time for clock-drift detection
# (Observable via connector logs)
grep "heartbeat.*server_time\|clock_drift" /var/log/butlers/gmail-connector.log 2>/dev/null | tail -5
# Expected: server_time field present in acknowledgment; drift logged if > threshold

# 5. Dashboard shows connector liveness derived from heartbeat recency
curl -s http://localhost:41200/api/connectors | python3 -m json.tool | grep -E "state|last_heartbeat"
# Expected: each connector shows state=online and a recent last_heartbeat_at timestamp
```

## Related Pages

- [Connector Architecture Overview](overview.md) -- What connectors are and how they work
- [Metrics](metrics.md) -- Statistics aggregation and dashboard API
- [Connector Interface Contract](overview.md) -- Full connector contract
