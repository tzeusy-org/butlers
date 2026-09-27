## 1. Contract and behavior tests

- [x] 1.1 Add focused editorial behavior tests for expected optional relation
  absence, named per-subquery PostgreSQL failures, high source-error attention,
  and cache-safe non-content states.
- [x] 1.2 Add API behavior tests for the additive availability response and for
  cache bypass when a named source is unavailable or degraded.
- [x] 1.3 Add source-state badge tests for failed-without-data,
  failed-with-retained-data, retry, and successful cold-boot empty behavior.
- [x] 1.4 Add Chronicles page tests for source-error attention/retry and an
  explicitly unavailable archive boundary.

## 2. Briefing availability implementation

- [x] 2.1 Introduce typed briefing availability values and classify expected
  optional/cold-boot relation absence separately from owned query failures.
- [x] 2.2 Collect individual content-query outcomes, expose every failed
  concern safely, and feed the existing availability precedence hook.
- [x] 2.3 Emit high source-error attention and deterministic cache-bypassing
  non-content payloads for named failures.
- [x] 2.4 Extend Chronicler API models/router and frontend types/hooks with the
  additive availability contract.

## 3. Reader truth and accessibility

- [x] 3.1 Render named source-error attention with the existing semantic retry
  affordance, without a calm empty or KPI/recent-day claim.
- [x] 3.2 Disable an unavailable archive boundary with an accessible explanation.
- [x] 3.3 Render source-state request failure as a named alert; visibly qualify
  retained badges and make retry keyboard-operable.

## 4. Verification and handoff

- [x] 4.1 Run strict OpenSpec validation and the focused backend/API/frontend
  behavior suites, then run appropriate format, lint, and diff checks.
- [x] 4.2 Review response copy and failure labels for safe disclosure,
  deterministic cache precedence, and no source-health all-clear regression.
