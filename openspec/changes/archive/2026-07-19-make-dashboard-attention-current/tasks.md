## 1. Regression Protection

- [x] 1.1 Add failing backend regressions for briefing cache invalidation after a committed QA reset and for no-op reset preservation.
- [x] 1.2 Add failing briefing and shared attention-contract scenarios for a 15-hour-old audit group, all-time-only notification failures, and completed versus active QA work.
- [x] 1.3 Add failing frontend model/page regressions for the 12-hour issue horizon, 24-hour notification query, active-QA attention, and time-bounded dispatch activity copy.

## 2. Backend Attention Composition

- [x] 2.1 Bound briefing audit attention by group `last_seen_at` within 12 hours and exclude historical groups from state classification.
- [x] 2.2 Request 24-hour notification statistics and use active QA cases plus recent patrol failures as the only non-breaker QA attention signals.
- [x] 2.3 Invalidate the briefing cache only after a QA breaker-reset marker commits successfully.

## 3. Overview Attention Composition

- [x] 3.1 Render current issue rows only when `last_seen_at` is within 12 hours while retaining older-history rollups.
- [x] 3.2 Request and label notification pressure within a stable 24-hour window, preserving that boundary in the notification link.
- [x] 3.3 Render active QA cases as current attention and label completed dispatches as bounded activity rather than active follow-up work.

## 4. Contract and Verification

- [x] 4.1 Update dashboard briefing and Overview delta specifications to document the selected current-attention semantics.
- [x] 4.2 Run focused backend/frontend tests, lint, type/build, OpenSpec validation, and a targeted review of the changed contract scenarios.
- [x] 4.3 Align terminal-failure drill-down and notification verdict predicates and
  exact windows with the bounded stats count.
