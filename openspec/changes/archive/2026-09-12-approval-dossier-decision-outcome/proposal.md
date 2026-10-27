## Why

An approval dossier currently tells the owner who decided an action, but not the
recorded reason for a denial or the safe outcome of an approved execution. That
leaves terminal decisions hard to audit and makes an unexecuted approved action
look indistinguishable from a completed one.

## What Changes

- Extend approval-detail responses with an event-backed `denial_reason` and a
  redacted `execution_result`.
- Render those decision and execution outcomes in the read-only approval
  dossier without removing its existing decision provenance.
- Offer dossier Retry dispatch only while an action is still `approved` and
  has no execution result.
- Preserve legacy and degraded-pool readability by returning a null denial
  reason when a rejection event cannot be read; do not add duplicate storage or
  a schema migration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-approvals`: The approval-detail API and dossier now expose the
  immutable denial reason and redacted execution outcome, with exact retry
  eligibility.

## Impact

- `src/butlers/api/models/approval.py` and
  `src/butlers/api/routers/approvals.py` detail mapping.
- `frontend/src/api/` approval types and `frontend/src/pages/ApprovalsPage.tsx`.
- Focused API and frontend regression coverage; no new dependency, migration,
  retry semantics, or approval-state transition is introduced.
