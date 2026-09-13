## Why

The QA dashboard currently treats several persisted patrol outcomes as clean because its
presentation falls through a green default. An operator can therefore mistake suppressed,
running, or overlap-skipped patrols for a healthy patrol, which undermines the dashboard's
truthfulness at a high-glance operational surface. The direct patrol views now fail closed,
but their aggregate consumers can still derive `staffer_status = "healthy"` and compose a
calm overview or briefing from a future, malformed, or legacy persisted status.

## What Changes

- Define the existing six-value QA patrol-status vocabulary as one explicit contract for the
  writer, patrol-list filter, API type consumers, and dashboard presentation.
- Give every accepted status a human-readable label and a non-conflicting semantic treatment:
  only `clean` is healthy green; dispatched findings and suppressed work are amber attention;
  errors are destructive; running and overlap-skipped patrols are visibly non-success.
- Preserve raw, unknown persisted status values for read-only display, but render them with a
  destructive unknown-state fallback rather than a healthy default.
- Derive an explicit non-success `unknown_patrol_status` summary condition for an unknown
  latest persisted status, and surface a textual, non-raw explanation in the QA verdict,
  dashboard attention list, and briefing.
- Add regression coverage for filter acceptance/rejection and for every supported status in both
  the overview strip and patrol-detail caption.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-dashboard`: QA patrol status display and filter semantics become an explicit, total
  dashboard contract.
- `dashboard-overview`: the aggregate QA attention row fails closed when the summary
  reports an unknown persisted patrol status.
- `dashboard-briefing`: the briefing's QA attention source fails closed when its recent
  patrol read encounters an unknown persisted status.

## Impact

- Backend: `src/butlers/modules/qa/__init__.py`, the shared QA status vocabulary,
  `src/butlers/api/routers/qa.py`, and dashboard briefing composition.
- Frontend: QA API types, patrol-list filter typing, patrol renderers, the QA verdict,
  and the Overview attention model.
- Tests: QA API, briefing, shared cross-surface contract, and Vitest page/model coverage.
- No database migration, patrol-dispatch policy change, new persisted patrol status, overview
  action, or case-rail change is introduced.
