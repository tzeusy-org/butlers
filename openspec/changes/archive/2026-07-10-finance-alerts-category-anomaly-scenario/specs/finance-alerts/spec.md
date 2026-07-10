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

#### Scenario: Category-level spending anomalies via the daily insight scan
- **WHEN** the `insight-scan` job (`0 7 * * *`, `run_insight_scan`) evaluates spending anomalies — its first evaluation step
- **THEN** it SHALL compare each debit category's current-month-to-date spend against that category's 3-month rolling monthly average, where a category is eligible only if it has debit activity in at least 3 distinct calendar months within the trailing 3-month window AND its rolling average is positive; categories with fewer than 3 months of history or a non-positive average are excluded
- **AND** it SHALL propose a `spending-anomaly` insight candidate for each eligible category whose current-month spend exceeds its rolling average by more than 30% (`_ANOMALY_THRESHOLD_LOW`); a category at or below +30% produces no candidate
- **AND** priority SHALL be 80 (`_SPENDING_ANOMALY_PRIORITY_HIGH`) when spend is more than 100% above the average (`_ANOMALY_THRESHOLD_HIGH`), 65 (`_SPENDING_ANOMALY_PRIORITY_MID`) when more than 50% above (`_ANOMALY_THRESHOLD_MID`), and 50 (`_SPENDING_ANOMALY_PRIORITY_LOW`) for the 30–50% band
- **AND** the candidate SHALL carry a month-scoped dedup key `finance:spending-anomaly:{category}:{YYYY-MM}` and expire at the end of the current calendar month; it sets NO explicit cooldown — the monthly dedup key alone bounds it to at most one candidate per category per month
- **AND** the message SHALL name the category, the percentage above the 3-month average, and the current and average amounts; metadata SHALL include `category`, `current`, and `average`
- **AND** this candidate is DISTINCT from the `anomaly-insight-scan` job's per-transaction `spending-anomaly-transaction` candidate (see "Per-transaction anomaly candidates via the daily anomaly insight scan"): this one is a monthly category-total-versus-rolling-average comparison emitted by `insight-scan` at `0 7 * * *`, whereas that one flags individual transaction outliers (`amount_anomaly`, `new_merchant`, `category_velocity_anomaly`) daily via `anomaly_scan()` at `0 21 * * *`; they use different candidate categories, dedup-key shapes, cooldowns, and priority scales, and neither supersedes the other

#### Scenario: Budget thresholds via the daily insight scan
- **WHEN** the `insight-scan` job (`0 7 * * *`) evaluates budget thresholds
- **THEN** it SHALL call `budget_status()` (which aligns each budget's spending window to its own period via `DATE_TRUNC`) and propose a `budget-threshold` insight candidate for every active budget — of any period (`weekly`, `monthly`, `quarterly`, `yearly`) — whose utilization is at or above that budget's own configured `warn_threshold` (default 0.80) (bu-hovqz: previously the scan filtered `period = 'monthly'` only, silently excluding weekly/quarterly/yearly budgets)
- **AND** priority SHALL be 70 when utilization is at or above the budget's `alert_threshold` (default 1.00), else 50
- **AND** the candidate SHALL include category, budget period, spent amount, budget amount, and utilization percentage, with a period-scoped dedup key whose time-scope segment resets at that period's boundary (`weekly`→`YYYY-Www`, `monthly`→`YYYY-MM`, `quarterly`→`YYYY-Qn`, `yearly`→`YYYY`) — the four formats are mutually unambiguous, so a monthly and a yearly budget for the same category never share a dedup key
- **AND** the candidate's cooldown SHALL span the remainder of that budget's current period window, so a crossing fires at most once per window and the next window's fresh dedup key re-fires
- **AND** if no budget is at or above its `warn_threshold`, no `budget-threshold` candidate SHALL be proposed
- **AND** this replaced the old weekly `budget-status-check` task (which hardcoded 80%/90% thresholds regardless of each budget's actual configured thresholds) — that task was removed as fully redundant with (and less accurate than) this step, and cadence moved from weekly to daily

#### Scenario: Per-transaction anomaly candidates via the daily anomaly insight scan
- **WHEN** the `anomaly-insight-scan` job (`0 21 * * *`, `run_anomaly_insight_scan`) fires
- **THEN** it SHALL call `anomaly_scan(days_back=1, sensitivity="medium")` and propose each flagged anomaly as its own dedupeable, severity-scored `spending-anomaly-transaction` insight candidate (see "Large Transaction Alerts" above for the scan mechanics and cap)
- **AND** if no anomalies are found, no candidates SHALL be proposed
- **AND** this replaced the old daily `anomaly-digest` task, which grouped all anomalies into one always-fire Telegram notification instead of individually dedupeable/prioritized candidates

### Requirement: Alert Scheduled Task Definitions
The finance butler SHALL register deterministic, job-mode (not prompt-mode) scheduled tasks that propose insight candidates for intelligence-driven alerts, per the bu-rvz2o migration (PR #2991, merged 678a29596).

#### Scenario: Daily insight scan schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a `dispatch_mode = "job"` schedule named `insight-scan` with cron `0 7 * * *` (daily at 7 AM) resolving to `run_insight_scan`
- **AND** this job evaluates, in order: spending anomalies (category-level, vs. 3-month rolling average), upcoming bills (3-day window), budget thresholds, subscription renewals (annual, 14-day window), and subscription price changes — each proposed via `propose_insight_candidate()`
- **AND** the category-level spending-anomaly step is specified in full by the "Category-level spending anomalies via the daily insight scan" scenario above (distinct from the per-transaction `spending-anomaly-transaction` candidate emitted by `anomaly-insight-scan`)
- **AND** this schedule's cron was previously `"0 7 30 * *"` (day-30-of-month — a bug that silently never fired in February); it was corrected to the current daily cron as part of PR #2991

#### Scenario: Weekly bill reconciliation sweep schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a `dispatch_mode = "job"` schedule named `bill-reconciliation-sweep` with cron `15 21 * * 0` (Sunday at 9:15 PM) resolving to `run_bill_reconciliation_sweep`
- **AND** this replaces the old prompt-mode `upcoming-bills-check` task; `reconcile_bills()` itself remains a deterministic, always-run mutating step (not gated by insight verbosity), while its results (auto-settled bills, ambiguous confirm-tier matches, untracked recurring patterns) are surfaced as insight candidates

#### Scenario: Daily anomaly insight scan schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a `dispatch_mode = "job"` schedule named `anomaly-insight-scan` with cron `0 21 * * *` (daily at 9 PM) resolving to `run_anomaly_insight_scan`
- **AND** this replaces the old prompt-mode `anomaly-digest` task

#### Scenario: Monthly finance digest schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a `dispatch_mode = "job"` schedule named `monthly-finance-digest` with cron `0 9 1 * *` (1st of month at 9 AM) resolving to `run_monthly_finance_digest`
- **AND** this replaces the old prompt-mode `monthly-spending-summary` (previously also `0 9 1 * *`) and `subscription-audit-monthly` (previously `0 10 1 * *`) tasks, merged into one job

#### Scenario: Tasks absorbed without a dedicated replacement schedule
- **WHEN** enumerating the finance butler's schedules
- **THEN** the old weekly `budget-status-check` (`0 9 * * 1`) and weekly `subscription-renewal-alerts` (`20 21 * * 0`) prompt-mode tasks SHALL NOT appear as separate schedules
- **AND** their behavior SHALL instead be covered by the daily `insight-scan` job's budget-threshold and subscription-renewal/price-change steps respectively
