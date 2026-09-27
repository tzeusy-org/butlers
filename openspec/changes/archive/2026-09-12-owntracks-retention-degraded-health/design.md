## Context

`OwnTracksRetention._run_purge()` already catches purge exceptions so the six-hour
background loop can retry. The failure is currently limited to a warning log.
`OwnTracksConnector._get_health_state()` is already the single source for the local
`/health` response and every device heartbeat, so it can expose retention degradation
without creating a new transport, metric, or persistence path.

## Goals / Non-Goals

**Goals:**

- Make a persistent-in-process retention failure visible as existing `degraded` health.
- Keep purge failures non-fatal and retryable.
- Reset the signal promptly after a successful purge.
- Expose only a deterministic, count-based diagnostic rather than exception text.
- Preserve an existing connector `error` state ahead of retention degradation.

**Non-Goals:**

- Persisting the failure count across a restart or adding a migration.
- Adding a Prometheus metric, alert, notification, dashboard/API endpoint, or scheduler change.
- Changing the retention SQL, cadence, retry behavior, or ingestion health semantics.
- Exposing an exception type, message, traceback, database detail, or other raw failure data
  through health or heartbeat payloads.

## Decisions

### Keep the streak in `OwnTracksRetention`

`OwnTracksRetention` will own an in-memory consecutive-failure count. It increments after a
caught purge exception and resets to zero only after a successful `purge_once()` call. A small
read-only accessor will return either no retention diagnosis or a fixed message containing the
streak count. This keeps the state at the operation that defines success/failure and makes a
process restart naturally clear it.

### Reuse the existing connector health callback

`OwnTracksConnector._get_health_state()` will query the retention accessor when a retention
task exists. It will continue to return an existing connector `error` before evaluating the
retention signal; otherwise a nonzero streak returns `degraded` with the sanitized retention
message. The existing heartbeat instances already call this callback, so `/health` and
heartbeat state remain consistent without changing their envelope shape.

### Do not retain exception details

The streak stores only an integer. The health accessor derives its message solely from that
integer; it receives neither the exception object nor `str(exception)`. Existing warning logging
continues to provide local troubleshooting context while the externally surfaced health state is
safe for the connector registry.

### Alternatives considered

- **Durable database counter:** rejected because it needs schema/persistence work and makes a
  transient operational signal survive a process recovery.
- **New Prometheus metric or alert pipeline:** rejected because existing health/heartbeat state
  already has the required operator-visible degraded channel.
- **Connector callback mutating a duplicate streak:** rejected because two owners could drift;
  the retention task is the authoritative success/failure boundary.

## Risks / Trade-offs

- [A restart clears an unresolved failure streak] -> This is intentional process-local behavior;
  the next failed purge re-establishes degradation without a durable counter.
- [A generic diagnostic gives less debugging detail] -> Local warning logs retain diagnostics,
  while the health/heartbeat surface avoids leaking raw database or exception content.
- [A different connector error coincides with retention failure] -> The existing `error` result
  remains first in the health priority order.

## Migration Plan

No migration or rollout data backfill is required. Deploying the code starts the in-memory count
at zero. Rollback is a code rollback; no persisted state needs cleanup.
