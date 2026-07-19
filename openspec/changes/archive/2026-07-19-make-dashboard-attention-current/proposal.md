## Why

The Overview is meant to answer whether the system is working now, but it currently
elevates historical audit errors, all-time notification failures, and completed QA
dispatches into current attention. That made a manually reset QA breaker appear urgent
until the briefing cache expired and made healthy delivery/model state look broken.

## What Changes

- Define current attention as live state or a time-bounded recent failure; retain older
  audit and notification records on their dedicated history surfaces.
- Limit audit-derived Overview and briefing attention to groups last seen within a
  12-hour operational horizon.
- Limit notification delivery pressure to failures from the prior 24 hours.
- Treat active QA cases as the current QA signal; present completed dispatches as
  time-bounded activity rather than active failure attention.
- Invalidate cached owner briefings after a successful QA circuit-breaker reset so the
  next read reflects the reset immediately.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-briefing`: Compose and cache the briefing from current attention only.
- `dashboard-overview`: Render current attention separately from historical issue and
  notification aggregates.

## Impact

- Backend briefing composition and QA reset route.
- Existing notification statistics query parameters; no new endpoint or response field.
- Overview attention-model composition, query wiring, and wording.
- Dashboard attention contract fixtures and focused backend/frontend regression tests.
