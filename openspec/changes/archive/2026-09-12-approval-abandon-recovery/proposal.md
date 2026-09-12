## Why

An approval that was explicitly granted but never executed is presently an
open recovery decision indefinitely. Reusing rejection would falsify the
owner's prior decision, while retaining an approved tombstone makes the stalled
radar and Retry control lie about an action that the owner deliberately ended.

## What Changes

- Add a durable `abandoned` terminal outcome for an approved action that has no
  execution result.
- Require a non-blank owner reason and append an immutable `action_abandoned`
  event when the dashboard abandons an eligible action.
- Make Retry dispatch and Abandon mutually exclusive compare-and-set recovery
  operations, so concurrent requests yield one durable outcome.
- Update approval retention, API, dashboard vocabulary, and contracts so an
  abandoned action is neither stalled nor retryable.
- Keep abandonment dashboard-only: no Telegram callback, automatic, scheduled,
  or bulk abandonment path is introduced.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `module-approvals`: Extend the approval lifecycle, immutable event
  vocabulary, retention policy, and recovery concurrency contract.
- `dashboard-approvals`: Add the dashboard-only Abandon endpoint and affordance
  with exact eligibility and reason requirements.

## Impact

- Approvals models, transition logic, executor recovery seam, retention, and
  approvals-chain migration.
- Dashboard API models/router, frontend API types/client hooks, and approval
  dossier/stalled-lane rendering.
- Focused module, migration, API, and frontend race/eligibility regressions;
  no new dependency or delivery-channel behavior.
