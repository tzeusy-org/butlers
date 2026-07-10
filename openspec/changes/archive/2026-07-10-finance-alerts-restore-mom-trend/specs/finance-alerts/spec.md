## MODIFIED Requirements

### Requirement: Automated Periodic Summaries
The system SHALL generate periodic financial summaries incorporating intelligence data, proposed as insight candidates rather than sent via unconditional direct notification (bu-rvz2o, PR #2991).

#### Scenario: Monthly finance digest
- **WHEN** the `monthly-finance-digest` job (`0 9 1 * *`, `run_monthly_finance_digest`) fires on the 1st of the month
- **THEN** it SHALL compose and propose a single `monthly-finance-digest` insight candidate (priority 55, 25-day cooldown, dedup key `finance:monthly-digest:{YYYY-MM}`) covering the prior calendar month
- **AND** the message SHALL include: total spend, the top 3 spending categories by amount, budget status (categories not `on_track`, or "all categories on track"), and a subscription audit summary (active subscription count, projected annual cost, and untracked-pattern count if any)
- **AND** the message SHALL additionally include a month-over-month trend segment when prior-month data is available (see "Month-over-month trend content" below)
- **AND** this merges what were previously two separate always-fire tasks (`monthly-spending-summary` and `subscription-audit-monthly`) whose subscription-audit content was duplicated across both
- **AND** the net-worth-snapshot reminder and outstanding-obligations bullets from the old `monthly-spending-summary` task were dropped as low-value/redundant (disclosed in PR #2991)
- **AND** delivery is subject to the owner's insight verbosity/budget like any other candidate — it is no longer an unconditional send

#### Scenario: Month-over-month trend content
- **WHEN** the `monthly-finance-digest` job composes its candidate (the "notable changes" trend restored by the bu-7hogl RESTORE decision, shipped in PR #3024)
- **THEN** it SHALL append a month-over-month trend segment comparing the covered month against the immediately preceding calendar month, aggregated per debit category (`_month_over_month_trend`)
- **AND** the segment SHALL state the overall total-spend direction (`up`, `down`, or `flat`) and the absolute percentage change versus the prior month (labeled `YYYY-MM`)
- **AND** it SHALL list "notable changes": each debit category whose spend swings by more than 20% month-over-month (formatted `{category} {+/-}{pct}%`), each category that newly appeared this month (`{category} (new)`), and each category that had prior-month spend but none this month (`{category} (no spend)`)
- **AND** notable changes SHALL be ordered by the absolute size of the swing (largest first) and capped at 5, with any remainder summarized as `(+N more)`
- **AND** when there is insufficient prior-month data to compute a meaningful comparison (no prior-month debit spend), the trend bullet SHALL be omitted entirely rather than shown empty
- **AND** a failure to compute the trend SHALL never block the digest — the digest is proposed without the trend segment (graceful degradation)

#### Scenario: Budget thresholds via the daily insight scan
- **WHEN** the `insight-scan` job (`0 7 * * *`) evaluates budget thresholds
- **THEN** it SHALL call `budget_status()`-equivalent aggregation and propose a `budget-threshold` insight candidate per category whose utilization is at or above that budget's own configured `warn_threshold` (default 0.80)
- **AND** priority SHALL be 70 when utilization is at or above the budget's `alert_threshold` (default 1.00), else 50
- **AND** the candidate SHALL include category, spent amount, budget amount, and utilization percentage, with a month-scoped dedup key
- **AND** if no category is at or above its `warn_threshold`, no `budget-threshold` candidate SHALL be proposed
- **AND** this replaced the old weekly `budget-status-check` task (which hardcoded 80%/90% thresholds regardless of each budget's actual configured thresholds) — that task was removed as fully redundant with (and less accurate than) this step, and cadence moved from weekly to daily

#### Scenario: Per-transaction anomaly candidates via the daily anomaly insight scan
- **WHEN** the `anomaly-insight-scan` job (`0 21 * * *`, `run_anomaly_insight_scan`) fires
- **THEN** it SHALL call `anomaly_scan(days_back=1, sensitivity="medium")` and propose each flagged anomaly as its own dedupeable, severity-scored `spending-anomaly-transaction` insight candidate (see "Large Transaction Alerts" above for the scan mechanics and cap)
- **AND** if no anomalies are found, no candidates SHALL be proposed
- **AND** this replaced the old daily `anomaly-digest` task, which grouped all anomalies into one always-fire Telegram notification instead of individually dedupeable/prioritized candidates
