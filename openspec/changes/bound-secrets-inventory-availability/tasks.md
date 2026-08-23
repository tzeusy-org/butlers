## 1. Contract and audit-read optimization

- [x] 1.1 Validate the added bounded-inventory availability requirement without modifying the active content-blind inventory requirement block.
- [x] 1.2 Add failing audit-helper assertions for a deduplicated lateral top-N query and preserve newest-first output.
- [x] 1.3 Replace the windowed audit lookup with the existing-index-backed lateral top-N lookup.
- [x] 1.4 Add an opt-in multi-target audit-index performance regression and run the focused API/performance tests.

## 2. Bounded truthful inventory fan-out

- [x] 2.1 Add deterministic failing tests for concurrent per-butler reads, source timeout omission, stable degraded metadata ordering, and partial counts.
- [x] 2.2 Implement the six-source concurrent fan-out, three-second source budget, ten-second request budget, and atomic omission of incomplete sources.
- [x] 2.3 Run the full Secrets inventory API suite and content-blind response-byte sentinels.

## 3. Partial-inventory passport state

- [x] 3.1 Add a failing passport rendering test for a partial zero inventory.
- [x] 3.2 Render the incomplete-inventory headline while retaining the existing named degraded banner.
- [x] 3.3 Run focused frontend tests, lint, Knip, and build.

## 4. Integrated verification and review

- [x] 4.1 Validate the OpenSpec change strictly and check no unarchived change duplicates the modified inventory requirement block.
- [x] 4.2 Run right-sized backend/frontend quality gates and review the diff for content-blind and degraded-envelope regressions.
- [ ] 4.3 After explicit deployment authorization, verify the dev inventory endpoint with a body-discarding request below ten seconds and confirm the browser route's complete and partial states.
