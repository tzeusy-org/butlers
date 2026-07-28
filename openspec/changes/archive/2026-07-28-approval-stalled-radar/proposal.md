## Why

An owner-approved action can remain unexecuted while falling outside the limited
approval-history window. The trust console must surface that whole-population
failure mode instead of treating a short, successful-looking history slice as
an all-clear.

## What Changes

- Extend the flat approvals endpoint with a derived `state=stalled` filter:
  an action is stalled exactly when its persisted status is `approved` and its
  `execution_result` is `NULL`.
- Add a limit-independent `meta.stalled_count` to every flat approvals
  response, computed across the same approval-source population as the list.
- Preserve degraded-source truth in the response metadata and make the
  approvals verdict opener use that whole-population count rather than its
  bounded history rows.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-approvals`: The flat approvals API and Trust Console verdict gain
  a derived stalled-approval radar with explicit degraded-source handling.

## Impact

- Backend: approval list aggregation, response metadata, and query validation
  in the dashboard approvals router.
- Frontend: flat approvals types/client hook and the approvals verdict opener.
- Tests: API aggregation/filter coverage and focused approvals-page verdict
  coverage.
- No persisted status, schema migration, recovery mutation, URL lane, or
  dossier redesign is introduced.
