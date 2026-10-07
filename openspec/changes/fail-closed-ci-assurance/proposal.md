## Why

The required `check` currently ignores failed/cancelled preflight verdicts under `always()`. Static checks can therefore fail without making the required fan-in red. Restore truthful assurance while preserving the affected-only planner and full merge-group terminal population.

## What Changes

- M1 reads all declared needed-job results, validates the exact event/classifier/planner policy, and gates preflight independently of heavy-shard consistency.
- M1 moves the existing lock, lint, format and SQL-safety checks into mandatory guards and adds guards as a direct check prerequisite.
- The complete approved original also retains M2 independent merge-group coverage/no PR coverage, M3 finite timeouts and bounded browser installation, and M4 timing/long-tail observations. Their implementation is not released in this M1 source stage.
- Preserve every original clause and checker survivor. No new required context, test selection, worker, service, migration or doctrine target.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing`: complete needed-job verdicts, static-check survivors, preserved smoke event policy, independent reporting and finite execution guarantees. Future mandatory portions remain visibly incomplete.

## Impact

M1 changes `.github/workflows/ci.yml`, Makefile checker recipes, the existing CI contract test file, and CI documentation. Coverage stays in check and all current test/timeouts remain until later explicit source release. This active change is not synchronized or archived after M1 because whole original implementation/observation tasks remain incomplete. Source intent is released by closed bu-7lh5ew and canonical bu-ly3lv5.1; M1 claims no wall-clock gain.
