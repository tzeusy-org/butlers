## 1. URL-backed stalled lane

- [x] 1.1 Add focused failing frontend coverage for the stalled radar link,
  direct `state=stalled` load, retained lane URL, and waiting-only keyboard
  verbs.
- [x] 1.2 Implement the minimal URL-backed rail state, native lane navigation,
  and truthful stalled verdict destination.

## 2. Safe retry reconciliation

- [x] 2.1 Add focused failing coverage for strict null-result Retry eligibility,
  pending behavior, successful read-cache invalidation, and failure without
  optimistic invalidation.
- [x] 2.2 Implement the smallest Retry completion handler change to report the
  server result and invalidate the complete approval read family only after
  success.

## 3. Verification and handoff

- [x] 3.1 Run focused frontend tests and the existing flat approvals API
  predicate test; run strict OpenSpec validation.
- [x] 3.2 Review the final diff against the derived-state, no-new-retry,
  no-abandon, no-persisted-status, and accessibility boundaries before PR
  handoff.
