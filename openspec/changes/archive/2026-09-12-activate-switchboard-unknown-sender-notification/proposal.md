## Why

The entity-first unknown-sender contract is specified but unreachable in the
production Switchboard pipeline: fleet wiring leaves identity resolution off
and supplies no owner-delivery callback. The inactive helper also points at a
retired contacts route and cannot make a durable, race-safe one-time
notification claim.

## What Changes

- Activate entity-first sender resolution only in Switchboard fleet wiring.
- Supply a deterministic owner-notification callback that uses the established
  `notify.v1` Switchboard-to-Messenger delivery boundary.
- Replace the legacy contacts target with the existing Unidentified Entities
  review route and keep notification content free of inbound message bodies or
  contact identifiers.
- Reserve a durable, atomic per-sender notification claim before delivery so
  concurrent ingress and delivery failures cannot create a notification storm.
- Reserve or reuse one transitory entity per sender before pipeline activation,
  so concurrent first messages cannot mint different entities during the gap
  before the relationship-owned channel-fact hook runs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `entity-identity`: Require an atomic durable claim for the one owner-facing
  unknown-sender notification attempt and define fail-open behavior when the
  claim cannot be persisted.
- `switchboard-identity`: Require Switchboard fleet wiring to activate the
  entity-first flow and inject its standard owner-delivery callback.

## Impact

- `src/butlers/switchboard_wiring.py` supplies the enabled pipeline settings
  and delivery callback.
- `roster/switchboard/tools/identity/inject.py` uses the existing
  Switchboard-local state store for both the atomic entity reservation and
  notification claim, then renders the safe entity review notice.
- Focused tests cover wiring, first/known/repeated senders, delivery and state
  failures, competing notification claims, and two concurrent pipeline runs
  sharing one entity-only routing context.
- No migration, contact-table restoration, frontend route change, or broad
  notification-policy redesign is included.
