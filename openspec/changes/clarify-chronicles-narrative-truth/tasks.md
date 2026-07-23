## 1. Contract serialization (this OpenSpec-only PR)

- [x] 1.1 Define deterministic day-close prose admission, structured
  `date_label` binding, and invalid-cache containment in the
  `butler-chronicler` delta.
- [x] 1.2 Define `no_data`, covered `quiet`, unavailable/degraded precedence,
  authoritative archive coverage, and cache-bypass behavior in the
  `dashboard-chronicles` delta.
- [x] 1.3 Define the additive invalid-without-prose
  `/api/chronicler/aggregate/day-close` response in the `chronicler-api` delta.
- [x] 1.4 Record the `bu-imsks` ownership boundary and the no-reuse boundary for
  `chronicler-telemetry-distillation`.

## 2. Cache admission and containment (future cache slice)

- [ ] 2.1 Add a deterministic reusable day-close admission predicate that
  validates human-facing shape and structured `date_label` binding before
  a cache write or cache read can expose prose.
- [ ] 2.2 Make an invalid candidate a non-replacing cache outcome and preserve
  any raw row only for audit/recovery, never owner-facing rendering.
- [ ] 2.3 Add the explicit invalid-without-prose API union and test fresh,
  missing, stale, invalid-shape, and date-mismatch cases without an LLM call.
- [ ] 2.4 Add regression coverage for the observed tool/protocol/planning-trace
  class and for deterministic fallback from invalid cache content.

## 3. Authoritative coverage and archive truth (future coverage slice)

- [ ] 3.1 Introduce a durable Chronicler-owned, owner-timezone
  covered-local-day witness with auditable successful coverage evidence; do not
  derive it from registry dates, current feeder state/checkpoints, or
  `daily_rollups`.
- [ ] 3.2 Make the briefing resolve unavailable/degraded, `no_data`, and
  covered `quiet` in the specified precedence order before cache selection.
- [ ] 3.3 Bind `earliest_date`, recent-day rows, deep-link rendering, and
  backward navigation to covered-local-day witnesses; disable backward
  navigation when no truthful floor is available.
- [ ] 3.4 Add API and frontend behavior coverage for pre-coverage history,
  covered empty days, indeterminate coverage, degraded availability, and cache
  bypass in each non-content state.

## 4. Availability ownership and final reconciliation

- [ ] 4.1 Before availability implementation, the coordinator shall explicitly
  reconcile this contract with blocked `bu-imsks`; that bead remains the sole
  owner of query-failure/availability classification and its API/frontend
  implementation surface.
- [ ] 4.2 Ensure the availability implementation never maps owned query failure
  or coverage indeterminacy to `no_data` or `quiet`, and never duplicates a
  second classifier in a cache or coverage slice.
- [ ] 4.3 Do not amend, consume as coverage evidence, or otherwise extend the
  active `chronicler-telemetry-distillation` change without a new explicit
  ownership decision and change scope.
- [ ] 4.4 After implementation, run strict OpenSpec validation, focused
  contract/API/frontend tests, and an exact-head review before syncing or
  archiving this change.
