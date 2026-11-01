## 1. Implementation

- [x] 1.1 Add `action LIKE 'schedule.%'` to `_PRIVILEGED_CONSEQUENCE_SQL`
- [x] 1.2 Spec delta for the privileged allowlist scenario

## 2. Verification

- [x] 2.1 New unit test that the predicate selects the schedule family and stays an allowlist
- [ ] 2.2 Real-Postgres test executes in CI (authoring host has no Docker)
