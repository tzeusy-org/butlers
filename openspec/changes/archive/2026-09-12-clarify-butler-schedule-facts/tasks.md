## 1. Regression coverage

- [x] 1.1 Add focused ButlerDetailHeader tests for earliest future, named
  overdue, mixed facts, disabled/null/malformed timestamps, and the schedules
  deep-link destination under a fixed clock.

## 2. Read-only header facts

- [x] 2.1 Classify enabled parseable schedule rows into stable overdue and
  future header facts, refreshing the wall-clock boundary without a write.
- [x] 2.2 Render the accessible amber overdue link and independently truthful
  future-next fact using existing time and System Schedules primitives.

## 3. Verification and handoff

- [x] 3.1 Strictly validate the OpenSpec delta, run focused frontend tests,
  lint, typecheck/build, and review the final diff for the read-only boundary.
- [x] 3.2 Commit, push the focused branch, and open a PR for exact-head review.
