## Why

The daily `insight-scan` job's **first** evaluation step proposes a category-level
`spending-anomaly` insight candidate (a monthly category-total-vs-3-month-rolling-average
comparison, `run_insight_scan` step 1 in `roster/finance/jobs/finance_jobs.py`).
Every other candidate type `insight-scan`/`anomaly-insight-scan` emit — budget
thresholds, subscription price changes, monthly digest, and the per-transaction
`spending-anomaly-transaction` — has a dedicated normative scenario in the
`finance-alerts` spec (added by PR #3012, bu-451j0). This one does **not**: its
priority thresholds (`_SPENDING_ANOMALY_PRIORITY_HIGH`/`MID`/`LOW` = 80/65/50),
its trigger (>30% above the 3-month rolling average), and its dedup key
(`finance:spending-anomaly:{category}:{YYYY-MM}`) survive only as a passing
"spending anomalies (category-level, vs. 3-month rolling average)" mention in the
"Daily insight scan schedule" scenario's evaluation-order list.

This is a completeness gap surfaced in the PR #3012 review (bu-451j0) — nothing
currently asserted is false; the shipped behavior simply has no scenario of its
own. Because the category-level candidate is easily confused with the
per-transaction `spending-anomaly-transaction` candidate (both about "spending
anomalies"), the missing scenario also leaves that distinction undocumented.

## What Changes

- **`finance-alerts`: add a dedicated "Category-level spending anomalies via the
  daily insight scan" scenario** under the "Automated Periodic Summaries"
  requirement — alongside the sibling insight-scan-step scenarios (budget
  thresholds, per-transaction anomaly) PR #3012 placed there. It documents,
  verified line-by-line against `run_insight_scan` step 1: the trigger
  (current-month category spend >30% above the category's 3-month rolling monthly
  average, eligibility gated on ≥3 months of history and a positive average), the
  80/65/50 priority mapping and the >100% / >50% / 30–50% bands that drive it, the
  month-scoped dedup key shape, the end-of-month expiry, the absence of an explicit
  cooldown (the monthly dedup key alone bounds it), and — explicitly — how it
  differs from the per-transaction `spending-anomaly-transaction` candidate.

- **`finance-alerts`: add one cross-reference bullet** to the "Daily insight scan
  schedule" scenario pointing at the new dedicated scenario, so the evaluation-order
  mention is discoverable from the schedule definition.

## Impact

- Specs only. No code, no migrations, no schema changes.
- `openspec/specs/finance-alerts/spec.md`: two requirements modified — "Automated
  Periodic Summaries" (one scenario added, siblings carried forward unchanged) and
  "Alert Scheduled Task Definitions" (one cross-reference bullet added to the
  "Daily insight scan schedule" scenario).
- No behavior change — the code already implements the described behavior. This
  change makes the spec document what ships.

## Out of Scope

- **The budget-threshold scenario.** PRs #3048 (merged) and #3054 (in flight)
  own the budget-threshold scenario keys; this change does not touch that scenario.
- **The per-transaction `spending-anomaly-transaction` scenario.** It is already
  documented (PR #3012); this change only references it for contrast.
