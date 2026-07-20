## Why

The QA dashboard currently treats several persisted patrol outcomes as clean because its
presentation falls through a green default. An operator can therefore mistake suppressed,
running, or overlap-skipped patrols for a healthy patrol, which undermines the dashboard's
truthfulness at a high-glance operational surface.

## What Changes

- Define the existing six-value QA patrol-status vocabulary as one explicit contract for the
  writer, patrol-list filter, API type consumers, and dashboard presentation.
- Give every accepted status a human-readable label and a non-conflicting semantic treatment:
  only `clean` is healthy green; dispatched findings and suppressed work are amber attention;
  errors are destructive; running and overlap-skipped patrols are visibly non-success.
- Preserve raw, unknown persisted status values for read-only display, but render them with a
  destructive unknown-state fallback rather than a healthy default.
- Add regression coverage for filter acceptance/rejection and for every supported status in both
  the overview strip and patrol-detail caption.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `qa-dashboard`: QA patrol status display and filter semantics become an explicit, total
  dashboard contract.

## Impact

- Backend: `src/butlers/modules/qa/__init__.py`, the shared QA status vocabulary, and
  `src/butlers/api/routers/qa.py`.
- Frontend: QA API types, patrol-list filter typing, and the overview, detail, and QA butler
  patrol renderers.
- Tests: QA API, module, and Vitest page coverage.
- No database migration, patrol-dispatch policy change, new patrol status, overview action, or
  case-rail change is introduced.
