## 1. Durable approval vocabulary

- [x] 1.1 Add failing module and migration regressions for the `abandoned`
  status, required reason, immutable `action_abandoned` event, retention, and
  fresh/upgraded schema constraints.
- [x] 1.2 Implement the approvals-chain migration and align action/event models,
  state transition validation, event recording, and retention predicates.
- [x] 1.3 Make retry dispatch and abandonment use one persisted race-safe claim
  so only one operation can begin or record a terminal outcome.

## 2. Dashboard recovery boundary

- [x] 2.1 Add failing API regressions for dashboard-only abandonment eligibility,
  reason validation, stale-count truthfulness, and retry/abandon races.
- [x] 2.2 Implement the authenticated abandon endpoint, API request/response
  vocabulary, event/audit emission, and exact conflict mapping.
- [x] 2.3 Update frontend types, client mutation, and approval UI so the
  explicit-reason Abandon control appears only for approved/null-execution
  actions and disappears after a durable terminal result.

## 3. Contract and verification

- [x] 3.1 Update canonical approval specs and frontend/backend contract docs for
  status, event, retention, endpoint, and dashboard-only restrictions.
- [x] 3.2 Add focused frontend regressions for eligibility, reason submission,
  and Retry/Abandon exclusion.
- [x] 3.3 Run strict OpenSpec validation, targeted backend/migration/frontend
  tests, lint/format/type checks, and the final repository quality gate.
