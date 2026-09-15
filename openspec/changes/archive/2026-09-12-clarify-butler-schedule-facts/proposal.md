## Why

The Butler detail header currently treats every enabled `next_run_at` as a
future fact, so a stale scheduler timestamp appears as a false "next" fire
time. That weakens the header's role as a trustworthy operational glance.

## What Changes

- Classify enabled, parseable schedule timestamps into two independent header
  facts: the oldest overdue schedule and the earliest future schedule.
- Render an overdue schedule as a named, accessible amber fact with its
  deterministic age and a deep link to the existing System Schedules section.
- Preserve an independently truthful future-next fact when overdue and future
  schedules coexist.
- Leave disabled, null, malformed, and unparsable timestamps unrepresented
  rather than fabricating certainty.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-butler-management`: Butler detail header schedule facts distinguish
  an overdue schedule from the earliest future schedule and provide a
  read-only schedules deep link.

## Impact

- Affected frontend: `ButlerDetailHeader`, its schedule query consumption, and
  its focused tests.
- No scheduler calculation, schedule persistence, API response shape, header
  mutation control, status-board activity derivation, migration, or dependency
  change is included.
