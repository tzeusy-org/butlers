## Why

The owner cannot treat a backup as recoverable merely because it exists: the
only observed restore drill failed at `createdb` permission denial, yet that
failure currently resets the normal weekly cadence and produces neither durable
attention provenance nor a visible failure age. This change makes the restore
drill a bounded, least-privilege proof of recovery rather than an unverified
all-clear.

## What Changes

- Replace the dashboard-api's shared database credential for restore work with
  a dedicated restore-drill executor. Only that executor receives a distinct
  `LOGIN CREATEDB` role through a file-backed orchestration secret; the shared
  `POSTGRES_USER`, every butler/connector role, and the dashboard remain
  `NOCREATEDB`.
- Define the scratch-database restore lifecycle, its no-live-mutation
  prerequisite, and the current single-deployment guard; a future
  multi-replica deployment must add a cross-process concurrency guard before
  it may run the fixed-name drill concurrently.
- Make cadence result-aware: no recorded result is immediately due, a pass is
  due after seven days, and a recorded failure is due after 24 hours. A pass
  after failures closes the contiguous failure epoch.
- Persist stable failure stage/code and bounded sanitized detail, then emit a
  best-effort attention-ledger event with truthful `restore_drill` provenance;
  ledger trouble must not erase the drill result or imply an owner notification.
- Extend the system backup API and System page with failure-only
  `failing_since` visibility while preserving explicit pending and degraded
  states; prove the command path with a real PostgreSQL testcontainer.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-security`: define the purpose-bound executor credential, its
  explicitly accepted privileged-runtime threat boundary, and the unchanged
  least-privilege boundary for dashboard, butler, and connector roles.
- `deployment-hardening`: make the documented restore path operationally safe,
  lifecycle-bounded, and result-aware.
- `system-overview-page`: expose truthful restore result provenance, cadence,
  and current failure age in the API and dashboard.
- `core-notify`: extend the attention ledger vocabulary for a non-notification
  restore-drill failure event.
- `testing`: require environment-proof restore-drill integration evidence
  against PostgreSQL tooling in a testcontainer.

## Impact

Future implementation will touch privileged bootstrap provisioning, a dedicated
executor/service with a private secret mount, the restore-drill job and its
audit/attention records, the system API and frontend types/tile, operations
documentation, and focused unit, API/frontend, migration/role, compose, and
Docker testcontainer tests. This planning-only change does not execute a live
drill, alter a live role, deploy or restart services, or perform manual data
repair. The eventual implementation must not widen the dashboard, butler, or
connector runtime credentials; its sole new `CREATEDB` capability is the
separately isolated executor.
