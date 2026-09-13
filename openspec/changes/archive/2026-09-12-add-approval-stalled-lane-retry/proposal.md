## Why

The whole-population stalled radar can now truthfully report owner-approved
actions that never ran, but its count is not yet a door into the affected
records. The Trust Console needs a shareable stalled lane so the owner can
inspect and use the already-safe dispatch retry without mistaking an executed
failure for an unrun action.

## What Changes

- Make the Trust Console's rail state URL-backed: `?state=stalled` reads the
  existing flat stalled filter and renders that lane; the default remains the
  waiting queue.
- Give the stalled radar clause a real `/approvals?state=stalled` destination
  once that lane exists, with keyboard-operable lane navigation and explicit
  active-lane semantics.
- Reuse the existing Retry dispatch action only for `status = approved` with
  `execution_result = null`; keep the record visible while the request is in
  flight and report the server's actual result.
- After a confirmed retry response, invalidate every flat approvals cache plus
  the affected history and dossier reads. Do not invalidate or remove rows
  optimistically.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `dashboard-approvals`: The Trust Console gains a truthful URL-backed stalled
  lane and documents its bounded Retry and post-confirmation refresh behavior.

## Impact

- Frontend: `ApprovalsPage` query state, rail controls, Retry completion
  handling, and the stalled verdict clause.
- Tests: focused Trust Console UI/keyboard/cache behavior and the existing
  flat approvals API predicate contract.
- No backend retry semantics, schema, persisted approval status, owner
  decision, abandonment action, or live approval mutation changes.
