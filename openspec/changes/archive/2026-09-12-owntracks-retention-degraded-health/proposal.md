## Why

OwnTracks retention purges retry safely after a database failure, but repeated failures are
currently visible only in warning logs. Operators therefore cannot distinguish a healthy
connector from one that is continuously failing to enforce its configured retention policy.

## What Changes

- Track a process-local consecutive failure streak for the OwnTracks retention purge.
- Surface a nonzero streak through the connector's existing degraded health and heartbeat
  state using a fixed, sanitized diagnostic.
- Reset the streak after a successful purge while retaining the existing non-fatal retry loop.
- Preserve a pre-existing connector error as higher priority than retention degradation.
- Add focused regression coverage for failure, reset, priority, and diagnostic redaction.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `connector-owntracks`: Retention failures become observable through the existing connector
  health and heartbeat state without changing retention retry behavior.

## Impact

- Affects `src/butlers/connectors/owntracks.py` and focused OwnTracks connector tests.
- Reuses the existing health callback consumed by `/health` and `connector.heartbeat.v1`.
- Adds no migration, durable counter, metric, alert, notification, dashboard/API endpoint, or
  raw exception data in the exposed diagnostic.
