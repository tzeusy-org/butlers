## 1. Contract and backend regression coverage

- [x] 1.1 Add failing unit coverage that table-drives all six accepted patrol-list
  filters and rejects an invalid filter with the canonical vocabulary in the response.
- [x] 1.2 Add failing unit coverage that pins the canonical writer vocabulary and
  confirms an unknown stored status remains readable through the patrol API.
- [x] 1.3 Add the shared core QA status literal, immutable vocabulary, and validation
  helper; wire the QA module writes and patrol-list filter to it without changing
  lifecycle policy.

## 2. Dashboard status presentation

- [x] 2.1 Add failing table-driven Vitest coverage for every canonical status and an
  unknown value in the overview strip, asserting semantic dot token and accessible
  human label.
- [x] 2.2 Add failing table-driven Vitest coverage for every canonical status and an
  unknown value in the patrol-detail caption, asserting the same human label.
- [x] 2.3 Add typed frontend status/filter types and one pure total presentation
  mapping; use it from the overview, patrol detail, and QA butler patrol cadence
  stripe without adding motion or overview actions.

## 3. Verification and handoff

- [x] 3.1 Run strict OpenSpec validation before production-code changes and mark each
  implementation task complete only after its focused red-green test cycle passes.
- [x] 3.2 Run targeted Python and Vitest suites, frontend type/build/lint gates, and
  the repository-required final quality gates appropriate to the diff.
- [x] 3.3 Review the final diff against the QA dashboard and design-language specs,
  commit the scoped change, push `agent/bu-kqnum.9.3`, and open a focused PR.

## 4. Aggregate false-calm correction

- [x] 4.1 Amend the unarchived patrol-status truth contract for the summary, QA verdict,
  dashboard Overview, and briefing; pass strict OpenSpec validation before code changes.
- [x] 4.2 Add focused RED regression coverage showing a persisted `future_status` remains
  raw but derives an explicit non-success condition across each aggregate consumer.
- [x] 4.3 Implement the derived fail-closed condition without changing stored values or
  dispatch policy; retain canonical status semantics and make the focused regressions GREEN.
- [x] 4.4 Run focused API, verdict, Overview, briefing, QA, lint/build, and final quality
  gates; review the scoped diff and update this handoff task state.
