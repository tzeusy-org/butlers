## 1. Regression Coverage

- [x] 1.1 Add focused failing Spawner tests for the exact
  `schedule:consolidation` episode-write exclusion and an ordinary scheduled
  positive control.
- [x] 1.2 Run the focused episode-storage tests and confirm the new exclusion
  fails before production code changes.

## 2. Spawner Write Boundary

- [x] 2.1 Add the narrow exact-trigger guard to the successful episode-write
  boundary without changing session lifecycle or memory-context reads.
- [x] 2.2 Re-run the focused Spawner tests and confirm the new cases pass.

## 3. Verification

- [x] 3.1 Validate the OpenSpec change and run the relevant lint and test
  quality gates.
- [x] 3.2 Review the final diff for scope compliance and record verification
  evidence for handoff.
