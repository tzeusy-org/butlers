## 1. Governing contract

- [x] 1.1 Create and strictly validate the successor OpenSpec artifacts for the
  read-only structured decision-context projection.
- [x] 1.2 Sync the canonical Dashboard API and new Dashboard Decisions specs
  without altering the completed carrier's historical scope.

## 2. Read-only backend projection

- [x] 2.1 Add RED tests for valid context, missing/malformed structured
  metadata, invalid/missing deadline, and existing export degradation.
- [x] 2.2 Extend `compute_decision_digest` with validated per-record context
  and explicit structured-details availability/reason fields.
- [x] 2.3 Project the new fields through the typed `/api/decisions` response
  while preserving ordering, escalation, and the global degraded envelope.

## 3. Decisions page detail and deep link

- [x] 3.1 Add RED page tests for valid read-only detail, malformed details,
  known `?bead=` expansion, and unknown deep-link usability.
- [x] 3.2 Extend API types and the existing Decisions page to render trusted
  context, named detail degradation, and safe query-backed selection.
- [x] 3.3 Add/update accessibility coverage for structured, malformed,
  degraded, and deep-link states.

## 4. Verification

- [x] 4.1 Run focused backend and frontend tests through red-green-refactor,
  then the required lint/type/build checks.
- [x] 4.2 Run strict OpenSpec validation and the offline decision-linter
  marker check; review the final diff for the no-mutation/no-live-bridge
  boundary.
