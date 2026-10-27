## Why

RFC 0022 currently carries the adopted fleet-event behavior without a capability-spec home. The owner released this documentation maintenance through `bu-hycu7t`; moving its requirements into OpenSpec lets the RFC retain the transport decision and trade-offs without losing rules.

## What Changes

- Document the [Observed] RFC 0022 transport, producer, privacy, recovery, and cache-freshness contract as `core-fleet-events`, with stable requirement IDs and existing test citations.
- Condense RFC 0022 to its accepted decision and trade-offs, and update its index row to link the capability contract.
- Add citation comments to existing tests without changing test logic or runtime behavior.
- Sync and archive this bookkeeping change in the same delivery.

## Capabilities

### New Capabilities

- `core-fleet-events`: The existing PostgreSQL NOTIFY/LISTEN bridge from producer processes to the dashboard fleet event bus.

### Modified Capabilities

None. This change records already implemented, accepted behavior.

## Impact

Documentation and test comments only. No runtime, API, schema, provider, deployment, or live-data change is authorized. The source is RFC 0022 and the maintenance plan/evidence dated 2026-10-03 (cluster D); the inspected baseline is `37dfc67bf4b21b1d0e660449c2e69fa819472c08`.
