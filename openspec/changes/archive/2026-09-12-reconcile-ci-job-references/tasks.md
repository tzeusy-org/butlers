## 1. Reconcile the testing contract

- [x] 1.1 Rebuild the `MODIFIED` block for `Smoke Tests Run In CI As A Fast Gate`
  from the merged baseline, preserving every scenario name and current clause.
- [x] 1.2 Leave the live testing baseline untouched until archive.

## 2. Reconcile operational guidance

- [x] 2.1 Update the orphaned-testcontainers note with the jobs that set the
  hosted Ryuk override.
- [x] 2.2 Update AGENTS guidance to distinguish the local gates, hosted test
  jobs, and fail-closed `check` fan-in.
- [x] 2.3 Update test-condensation discovery and timing guidance for the split
  hosted jobs.

## 3. Verification

- [x] 3.1 Validate this change with strict OpenSpec validation and the spec-
  overwrite guard.
- [x] 3.2 Run the relevant documentation/spec and CI-workflow contract tests.
- [x] 3.3 Confirm stale in-scope job claims are gone and no workflow execution
  file changed.
- [x] 3.4 Run the diff and session-link hygiene gates.
