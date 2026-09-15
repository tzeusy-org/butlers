## Why

The entity detail API already returns tombstoned records and their metadata, but the dashboard leaves an operator on a merged-away record. The merge-review surface also commits an irreversible merge immediately after survivor selection, without a final explicit confirmation.

## What Changes

- Treat a valid `metadata.merged_into` value on a loaded entity as a frontend navigation fact: replace-navigate to the surviving entity and announce the transition to assistive technology.
- Keep malformed, empty, and self-referential `merged_into` metadata on the current record, while visibly identifying the merge metadata inconsistency instead of risking a redirect loop.
- Add a final alert-style confirmation to the existing merge-review commit action. The confirmation names the survivor and absorbed entity; Cancel and Escape make no mutation, while Confirm retains the existing request payload and error handling.
- Preserve the existing comparison-before-merge requirement and all existing memory/relationship API contracts.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `dashboard-relationship`: entity detail navigation must handle tombstoned merge sources safely, and merge review must require an accessible final confirmation before committing.

## Impact

- Frontend only: `EntityDetailPage`, `MergeCompareDialog`, and their focused tests.
- No memory or relationship API, backend router, migration, database write, ACL, endpoint, or payload changes.
- Reuses the dashboard shell announcer and existing alert-dialog primitive; adds no generic confirmation framework or dependency.
