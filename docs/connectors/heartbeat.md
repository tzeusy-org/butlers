# Connector Heartbeat Protocol

> **Purpose:** Specify the heartbeat protocol that all connectors implement to report liveness and operational statistics to the Switchboard.
> **Audience:** Developers building connectors or operating the Switchboard.
> **Prerequisites:** [Connector Architecture Overview](overview.md).

## Overview

Connectors are independent processes that run outside the Switchboard daemon lifecycle; the
heartbeat is how the system knows which ones are alive and ingesting. All connectors MUST
implement it. Where the heartbeat sits in the overall topology is described in
[Integration Points §8](../../about/lay-and-land/integration.md) and
[Data Flow §6](../../about/lay-and-land/data-flow.md); this page covers the protocol rules.

Connectors self-register on their first heartbeat -- no manual pre-configuration is required.

## Heartbeat Envelope

Connectors submit `connector.heartbeat.v1` payloads through the `connector.heartbeat` MCP tool on
the same Switchboard connection they ingest over (`SWITCHBOARD_MCP_URL`). The wire contract is the
Pydantic model `ConnectorHeartbeatV1` in `roster/switchboard/tools/connector/heartbeat.py`, which
also lists the accepted `connector_type` values (`VALID_CONNECTOR_TYPES`). Every section is
`extra="forbid"`, so an unknown field rejects the heartbeat. The rules the model cannot express:

- `instance_id` is a UUID generated once per process start; a new value on a known connector
  means a restart or replacement and is logged as such.
- `status.state` is `healthy`, `degraded` (operational with issues) or `error` (unable to
  ingest); `error_message` carries context for the latter two.
- Counters are monotonically increasing since process start. The Switchboard computes deltas
  against the previous snapshot, so a connector must never reset them mid-process.
- `checkpoint` is an opaque provider cursor; `capabilities` (for example `backfill: true`) drives
  which dashboard controls render.
- `sent_at` plus the `server_time` in the acknowledgment allow clock-drift detection.

## Frequency and Staleness

Connectors send a heartbeat every **2 minutes** by default. The heartbeat runs as a background task
independent of the ingestion loop; heartbeat failures MUST NOT block or crash ingestion.

Liveness is derived at read time from heartbeat recency by `derive_liveness()`
(`src/butlers/core/liveness.py`):

| Condition | Derived state |
|---|---|
| Last heartbeat < 5 min ago | `online` |
| Last heartbeat 5-15 min ago | `stale` |
| Last heartbeat > 15 min ago, or never | `offline` |
| Heartbeat more than 5 min in the future (clock skew) | `offline` |

Rules:
- `stale` connectors remain eligible for display but are flagged in the dashboard.
- `offline` connectors are flagged as down. No automatic deregistration.
- The Switchboard MUST NOT automatically remove connector records. Cleanup is an operator action.
- `unclassified` is not a liveness state: it applies only to a registry row whose
  operational role has not yet been claimed as a runtime instance.

## Switchboard Processing

The `connector.heartbeat` tool (`roster/switchboard/tools/connector/heartbeat.py`) validates the
envelope, self-registers an unknown `(connector_type, endpoint_identity)` pair (recording
`first_seen_at` and `registered_via`), upserts `switchboard.connector_registry` with the latest
state, counters and checkpoint, appends to the partitioned `switchboard.connector_heartbeat_log`
(7-day retention), computes counter deltas for rollups, and returns
`{status: "accepted", server_time}`.

## Connector-Side Implementation

The shared client lives in `src/butlers/connectors/heartbeat.py`: `HeartbeatConfig.from_env()`
reads `CONNECTOR_HEARTBEAT_INTERVAL_S` (clamped to 30-300 s) and `CONNECTOR_HEARTBEAT_ENABLED`
(disable only for dev/testing), and `ConnectorHeartbeat` runs the loop. It reads counter values
straight from the connector's Prometheus registry, filtered by `connector_type` and
`endpoint_identity`, so a connector gets correct counters by emitting the standard connector
metrics rather than by tracking them separately.

## Verification

To confirm the heartbeat protocol is functioning as specified:

```bash
# 1. Connectors appear in connector_registry after their first heartbeat
psql -h localhost -U butlers -d butlers -c \
  "SELECT connector_type, endpoint_identity, state,
          NOW() - last_heartbeat_at AS heartbeat_age
   FROM switchboard.connector_registry ORDER BY connector_type;"
# Expected: all running connectors present; heartbeat_age < 5 minutes (online threshold)

# 2. Liveness state transitions correctly (online/stale/offline)
# Stop a connector and wait past each threshold, then query liveness
# Expected: state transitions from 'online' → 'stale' at 5 min → 'offline' after more than 15 min;
# a runtime connector with no heartbeat is also offline

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
curl -s http://localhost:41200/api/ingestion/connectors/summaries | python3 -m json.tool | grep -E "liveness|last_heartbeat"
# Expected: each runtime connector shows online/stale/offline liveness from its
# heartbeat; storage-only checkpoints are nested under their parent
```

## Related Pages

- [Connector Architecture Overview](overview.md) -- What connectors are and how they work
- [Metrics](metrics.md) -- Statistics aggregation and dashboard API
- [Connector Interface Contract](overview.md) -- Full connector contract
