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
(MONTHLY partitions, nominal seven-day whole-partition pruning), computes counter deltas for rollups, and returns
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
# Expected: entries spaced ~120 seconds apart per connector; MONTHLY partitions use nominal seven-day pruning, so older rows may survive

# 4. Heartbeat acknowledgment includes server_time for clock-drift detection
# (Observable via connector logs)
grep "heartbeat.*server_time\|clock_drift" /var/log/butlers/gmail-connector.log 2>/dev/null | tail -5
# Expected: server_time field present in acknowledgment; drift logged if > threshold

# 5. Dashboard shows connector liveness derived from heartbeat recency
curl -s http://localhost:41200/api/ingestion/connectors/summaries | python3 -m json.tool | grep -E "liveness|last_heartbeat"
# Expected: each runtime connector shows online/stale/offline liveness from its
# heartbeat; storage-only checkpoints are nested under their parent
```

## Implementation Notes

- `status.state` is a closed vocabulary (`healthy`, `degraded`, `error`). A connector that has not
  finished its first transport attempt reports `degraded` with detail such as `transport=starting`,
  never a fourth state.

### Gmail classification admission

The optional fixed `capabilities.known_contact_check` object carries version=1, state/reason, strict nonnegative query generation, UTC last-success, process instance UUID and a nullable admission epoch UUID. Gmail startup sends unloaded/not_loaded, generation0, no success and no epoch; the server stores unknown and issues a fresh ordering epoch. Normal evidence must match the admitted instance/epoch and monotonically advance generation. Same-generation mutation and old instance/epoch/generation are refused before registry/counter/liveness mutation; identical duplicates cannot renew successful-query freshness. Legacy or malformed metadata is classification unknown. Capabilities bind as Python objects through the production JSONB codec; unrelated flags retain their contract. Existing historical JSON strings are not reinterpreted as authority.

The optional `classification_ack` contains admitted, instance_id, generation, request_admission_epoch, admission_epoch and a closed reason. Instance/generation/request epoch echo the exact request, including refusals. Startup echoes null and returns a new epoch; normal admitted replies return the sent epoch; refusals return no adoptable epoch. These public epochs order observations under existing heartbeat registration; they are not authentication credentials or new caller authority.

For opted-in Gmail, one publisher slot covers latest request assembly, send and exact ACK adoption. The two-second bound includes queue wait. Cancellation/timeout retires the attempt before releasing the slot; late replies cannot adopt or clear newer admission. Transport tails receive cancellation and bounded cleanup. A late no-epoch startup can still make the SERVER conservatively unknown; client retirement does not undo that observation. Subsequent refusal/handshake/loaded publication restores current admission without changing local query history. Generic periodic/default-disabled heartbeat behavior and provider health remain unchanged.

The Gmail primary writer keeps endpoint advisory → registry row → admission validation ordering. Under the released sw_041 strengthened recording contract, an admitted request appends history and persists registry admission in the same transaction and commits before an adoptable ACK; refused requests mutate neither. This explicitly replaces the previous registry-commit-before-best-effort-log ordering. Required append failure now rolls back the admitted transaction and yields non-blocking publication failure; it never demotes a successful local contact-query snapshot. The seven-day heartbeat log is not classification authority. New classification metadata is removed from capability diagnostics before logging, and malformed fields are normalized before generic validation errors. New diagnostics contain closed outcomes, no contact/error/token/epoch payloads.

## Related Pages

- [Connector Architecture Overview](overview.md) -- What connectors are and how they work
- [Metrics](metrics.md) -- Statistics aggregation and dashboard API
- [Connector Interface Contract](overview.md) -- Full connector contract

## Proposed historical count-bucket recording contract

Accepted ordinary handler heartbeats append the existing log row and upsert the existing registry row atomically, with database received_at and xid8 stamping and a server-owned protected coverage boundary. This uses the existing two DML writes and no new roles/grants or retention rule. Coverage is forward-only after actual paired activation. Supported registry-only SQL refreshes remain compatible and invalidate recording completeness rather than mint it. Normal runtime history mutation/marker forgery and partition replacement cannot produce a complete gap. The API is read-only and independently preserves event counts when an optional heartbeat query fails under a savepoint.

`not listening` means no durably accepted exact-endpoint heartbeat in a closed interval covered by complete receiver recording, not proof of a provider outage or physical receiver availability. Positive received heartbeats establish only their own bucket; stored healthy/degraded/error state remains separate. Unreadable, best-effort, preactivation, pre-first-seen, old-retention, replaced-partition or missing compatible receiver evidence is `liveness unknown`. Empty successful reads cannot establish completeness. Server-owned internal coverage/xid/catalog metadata never appears in chart DTOs or diagnostics. No deployed adoption is asserted by this source documentation.

### Proposed lock/time and trigger integrity precision

Canonical writers and history row guards take the exact endpoint lock before assigning `clock_timestamp()`/current xid. A direct child/COPY statement started before a reader wait receives its accepted time after that wait, not its earlier `statement_timestamp()`. The reader takes compatible catalog-stability relation locks and endpoint/registry locks before capturing its database `as_of`. Ordinary TRIGGER grants remain unchanged; actual parent/child/registry trigger closure, function owners/context and extra-trigger interference are checked rather than assumed safe. Unpaired direct registry producers stay supported but invalidate coverage; registry-first/history-later work in one transaction must refuse and roll back, rather than merely commit a NULL marker. A distinct, nonblocking transaction advisory witness remembers an unpaired heartbeat mutation only for refusal. The history guard checks it before its endpoint wait; session unlock cannot clear it, successful savepoints retain it, and rollback removes it with the mutation. It cannot mint coverage and adds no persistent write. The existing two DML operations and Gmail refusal/normal retry behavior are retained. This describes sw_041 source; actual role, PostgreSQL and elapsed-history evidence remain separate.
