## Why

The Timeline and Dashboard Now surfaces already derive safe summaries from
structured session triggers, but they still give high-volume maintenance runs
the same visual weight as owner-meaningful activity. This obscures household
events during a maintenance burst and makes a harmless scheduled session look
like an owner-facing action.

## What Changes

- Add a bounded `machine_class` presentation field to Timeline events with the
  values `owner`, `heartbeat`, and `maintenance`; retain `is_heartbeat` as a
  compatible projection.
- Classify maintenance only from an exact, reviewed trigger-source taxonomy.
  Unknown and other scheduled sources remain owner activity rather than being
  silently demoted.
- Add an accessible Internal lens, off by default, that exposes grouped,
  expandable maintenance activity in the Timeline and grouped maintenance
  activity in Dashboard Now.
- Keep failed maintenance sessions visible as errors even when the Internal
  lens is off.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-visibility`: Timeline event presentation gains a bounded machine
  classification and an Internal maintenance lens.
- `dashboard-overview`: Dashboard Now gains an Internal maintenance rollup
  while preserving its owner-focused default.

## Impact

- Backend: `src/butlers/api/session_presentation.py`, Timeline response
  models, and the Timeline router.
- Frontend: Timeline event types, Timeline ledger/page, and Overview Now
  derivation/control.
- No persistence, migration, lifecycle, notification, retention, or
  activity-feed changes; no new dependency is introduced.
