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

- [x] 2.1 Add a deterministic reusable day-close admission predicate that
  validates human-facing shape and structured `date_label` binding before
  a cache write or cache read can expose prose. (`prose_admission.py`;
  `date_label` recovered from the `chronicler_day_close_bundle` tool call's
  echoed `date`, wired into `day_close_writer.write_day_close_cache`.)
- [x] 2.2 Make an invalid candidate a non-replacing cache outcome and preserve
  any raw row only for audit/recovery, never owner-facing rendering. (An
  invalid candidate never overwrites an existing admissible row; with no
  existing admissible row it is persisted with `invalid_reason` set and never
  served as prose by either read path.)
- [x] 2.3 Add the explicit invalid-without-prose API union and test fresh,
  missing, stale, invalid-shape, and date-mismatch cases without an LLM call.
  (`DayCloseInvalidResponse`; `GET /aggregate/day-close` checks admission
  before staleness; `GET /briefing`'s `_voice_paragraph_from_cache` gates on
  `invalid_reason` too.)
- [x] 2.4 Add regression coverage for the observed tool/protocol/planning-trace
  class and for deterministic fallback from invalid cache content.
  (`tests/chronicler/test_prose_admission.py`,
  `tests/chronicler/test_day_close_writer.py`,
  `tests/chronicler/test_day_close_reader_api.py`,
  `tests/chronicler/test_editorial_api.py`.)

## 3. Authoritative coverage and archive truth (future coverage slice)

- [x] 3.1 Introduce a durable Chronicler-owned, owner-timezone
  covered-local-day witness with auditable successful coverage evidence; do not
  derive it from registry dates, current feeder state/checkpoints, or
  `daily_rollups`. (`covered_local_days` table, migration `chronicler_023`;
  `editorial.record_coverage_witness`, called from the `chronicler_day_close`
  completion hook on every successful dispatch, independent of output
  emptiness — a covered quiet day has no episode.)
- [x] 3.2 Make the briefing resolve unavailable/degraded, `no_data`, and
  covered `quiet` in the specified precedence order before cache selection.
  (`editorial.compose_briefing_payload`, scoped to settled/past days;
  `BriefingPayload.covered_and_available` gates cache lookup in
  `GET /briefing`.)
- [x] 3.3 Bind `earliest_date`, recent-day rows, deep-link rendering, and
  backward navigation to covered-local-day witnesses; disable backward
  navigation when no truthful floor is available. (`earliest_date` now
  sourced from `_fetch_earliest_covered_date`; frontend `earliest_date: null`
  handling was already coverage-floor-shaped and needed no change.)
- [x] 3.4 Add API and frontend behavior coverage for pre-coverage history,
  covered empty days, indeterminate coverage, degraded availability, and cache
  bypass in each non-content state. (`tests/chronicler/test_editorial.py`,
  `tests/integration/test_chronicler_coverage_witness_integration.py`
  (real Postgres), `frontend/src/pages/ChroniclesPage.test.tsx`.)

## 4. Availability ownership and final reconciliation

- [ ] 4.1 Before availability implementation, the coordinator shall explicitly
  reconcile this contract with blocked `bu-imsks`; that bead remains the sole
  owner of query-failure/availability classification and its API/frontend
  implementation surface. **Not done by this slice.** `compose_briefing_payload`
  accepts an `availability: str | None` parameter (`"unavailable"` |
  `"degraded"`) as the integration point, but nothing populates it yet — no
  second classifier was built. `unavailable` in this slice is triggered only
  by this change's own coverage-floor/owned-query-failure logic, never by
  inspecting source/feeder health.
- [x] 4.2 Ensure the availability implementation never maps owned query failure
  or coverage indeterminacy to `no_data` or `quiet`, and never duplicates a
  second classifier in a cache or coverage slice. (Verified: owned-query
  failure and coverage-evidence gaps both resolve `unavailable`, never
  `no_data`/`quiet`; see `test_compose_unavailable_on_owned_query_failure`,
  `test_compose_unavailable_on_coverage_evidence_gap`.)
- [x] 4.3 Do not amend, consume as coverage evidence, or otherwise extend the
  active `chronicler-telemetry-distillation` change without a new explicit
  ownership decision and change scope. (Not touched; coverage evidence is the
  new `covered_local_days` table only.)
- [x] 4.4 After implementation, run strict OpenSpec validation, focused
  contract/API/frontend tests, and an exact-head review before syncing or
  archiving this change.
