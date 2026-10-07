## Why

The original required `check` ignored failed/cancelled preflight verdicts under `always()`. M1 restored truthful assurance and static survivors. M2 removes coverage work from that required verdict path while preserving the affected-only planner and full merge-group terminal population.

## What Changes

- M1 reads all declared needed-job results, validates the exact event/classifier/planner policy, and gates preflight independently of heavy-shard consistency.
- M1 moves the existing lock, lint, format and SQL-safety checks into mandatory guards and adds guards as a direct check prerequisite.
- M2 makes check verdict-only and moves complete same-run reporting/badge work into a visible non-required merge-group job. PR shards preserve their selected tests and sanitized evidence without coverage; standalone calls keep coverage by default.
- M3 finite timeouts/bounded browser installation and M4 timing/long-tail observations remain mandatory and unreleased. M2 actual hosted uploads and timing observations remain pending.
- Preserve every original clause and checker survivor. No new required context, test selection, worker, service, migration or doctrine target.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing`: complete needed-job verdicts, static-check survivors, preserved smoke event policy, independent reporting and finite execution guarantees. Future mandatory portions remain visibly incomplete.

## Impact

M1 changed the workflow, Makefile checker recipes, existing CI contracts and documentation. M2 changes the workflow/shard runner, adds per-input CoverageData validation and producer metadata, and extends existing contract tests with actual local traced reporting controls. All current test/timeouts remain; M3 is unreleased. This active change is not synchronized or archived because whole original implementation/observation tasks remain incomplete. Source intent is released by closed bu-7lh5ew and canonical bu-ly3lv5.1; M1/M2 claim no wall-clock gain.
