## Why

The Audit Log page defaults to `kind=privileged`, but that allowlist has no schedule family, so
an owner cannot see who created, changed, deleted, triggered or toggled a scheduled task without
switching on "show all noise" (bu-q7vx1q.57, deferred item (d) of bu-q7vx1q.6).

## What Changes

- `kind=privileged` on `GET /api/audit-log` also returns the owner schedule mutations
  `schedule.create`, `schedule.update`, `schedule.delete`, `schedule.trigger` and
  `schedule.toggle` (one `action LIKE 'schedule.%'` predicate).
- The spec enumeration also names `spend.*`, which the code already included.
- No new audit writes, no migration, no UI change. Scheduler tick and cadence rows do not use the
  `schedule.` prefix and stay out.

## Capabilities

### Modified Capabilities

- `dashboard-audit-log`: Audit Log Read API privileged allowlist.
