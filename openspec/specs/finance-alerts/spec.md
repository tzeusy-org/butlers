# Finance Alerts

## Purpose
Configurable alert system -- large transaction alerts, subscription price change detection, bill reminders from historical patterns, and automated periodic spending summaries. Scheduled intelligence (anomaly, bill, budget, and monthly-digest detection) is delivered as proactive insight candidates through the switchboard insight broker (dedup/cooldown/quiet-hours/owner-verbosity), not via direct `notify()` calls from prompt-mode cron tasks (bu-rvz2o, PR #2991).
## Requirements
### Requirement: Alert Configuration
The system SHALL allow configuring financial alert preferences stored as memory facts.

#### Scenario: Setting a large transaction alert threshold
- **WHEN** `alert_configure(type="large_transaction", threshold=500, currency="USD", enabled=true)` is called
- **THEN** the system SHALL store a memory fact with `predicate='alert_config'`, `content='large_transaction'`, and `metadata={threshold, currency, enabled}`
- **AND** if an alert config for the same type already exists, it SHALL be superseded

#### Scenario: Listing active alert configurations
- **WHEN** `alert_list()` is called
- **THEN** the system SHALL return all active alert_config facts
- **AND** each alert SHALL include: `type`, configuration parameters, and `enabled` status
- **AND** the alert types accepted by `alert_configure` (validated against `_VALID_ALERT_TYPES`) SHALL be: `large_transaction`, `budget_exceeded`, `new_merchant`, `price_change`
- **AND** subscription price-change detection, bill reminders, and anomaly digests are delivered through dedicated tools (`detect_price_changes`, `predict_bills`, `anomaly_scan`) invoked from the deterministic `insight-scan`, `anomaly-insight-scan`, and `bill-reconciliation-sweep` scheduled jobs (see "Alert Scheduled Task Definitions" below) rather than as configurable `alert_configure` types

#### Scenario: Disabling an alert
- **WHEN** `alert_configure(type="large_transaction", enabled=false)` is called
- **THEN** the alert config SHALL be updated with `enabled=false`
- **AND** the scheduled check for that alert type SHALL skip processing when disabled

### Requirement: Large Transaction Alerts
The system SHALL flag transactions exceeding a configurable amount threshold.

#### Scenario: Transaction exceeds threshold
- **WHEN** a new transaction is recorded (via `record_transaction` or `bulk_record_transactions`) and a `large_transaction` alert is configured and enabled
- **THEN** if the transaction amount exceeds the configured threshold, the system SHALL include a `large_transaction_alert` flag in the transaction recording response
- **AND** the flag SHALL include: `threshold`, `amount`, `merchant`, `exceeds_by` (amount - threshold)

#### Scenario: Large transactions surfaced via the daily anomaly insight scan
- **WHEN** the `anomaly-insight-scan` job (`0 21 * * *`, `run_anomaly_insight_scan`) runs
- **THEN** it SHALL call `anomaly_scan(days_back=1, sensitivity="medium")`, which flags unusually large transactions as `amount_anomaly` entries (amount exceeds the merchant's baseline median by a sensitivity-scaled multiple of stddev) alongside `new_merchant` and `category_velocity_anomaly` entries
- **AND** each flagged anomaly SHALL be proposed as its own `spending-anomaly-transaction` insight candidate via `propose_insight_candidate()` — not compiled into a single always-fire Telegram digest
- **AND** severity (`high`/`medium`/`low`, from the anomaly's z-score) SHALL map to priority 75/55/35 respectively
- **AND** a single run SHALL propose at most 10 candidates (most severe first); any additional anomalies found SHALL be reported in the job's `truncated` count rather than silently dropped
- **AND** this scan is independent of any `large_transaction` `alert_configure` threshold — it flags statistical outliers relative to per-merchant history, not a fixed configured amount

### Requirement: Subscription Price Change Detection
The system SHALL detect when a recurring charge changes amount compared to the tracked subscription or historical median.

#### Scenario: Price increase detection
- **WHEN** `detect_price_changes(days_back=60)` is called directly, or as part of the daily `insight-scan` job's subscription-price-change step
- **THEN** the system SHALL compare recent transaction amounts for tracked subscription merchants against the subscription's recorded amount
- **AND** if the transaction amount differs from the tracked amount by more than the `_PRICE_CHANGE_THRESHOLD` (5%), it SHALL flag a price change
- **AND** the flag SHALL include: `service`, `previous_amount` (`tracked_amount`), `new_amount` (`recent_charge`), `change_pct`, `change_direction` (one of `increase`, `decrease`)

#### Scenario: Price change proposed as an insight candidate
- **WHEN** the `insight-scan` job (`0 7 * * *`) detects a price change via `detect_price_changes(days_back=60)`
- **THEN** it SHALL propose a `subscription-price-change` insight candidate (not call `notify()` directly) with a message naming the service, old amount, new amount, and percentage change
- **AND** priority SHALL be 45 for a 5–10% change, 60 for 10–20%, and 75 for >=20% (or when `change_pct` is unavailable — a newly observed charge amount)
- **AND** the candidate SHALL use a month-scoped dedup key (`finance:subscription-price-change:{service-slug}:{YYYY-MM}`) and a 30-day cooldown
- **AND** delivery (or suppression, digesting, and cooldown/dedup) is governed by the insight broker per the owner's verbosity setting, same as every other insight candidate

### Requirement: Bill Reminders from Historical Patterns
The system SHALL generate bill reminders based on historical payment patterns, supplementing the existing `upcoming_bills` tool.

#### Scenario: Historical bill reminder
- **WHEN** the weekly `bill-reconciliation-sweep` job (`15 21 * * 0`, `run_bill_reconciliation_sweep`) runs
- **THEN** in addition to running `reconcile_bills(lookback_days=90)`, it SHALL call `predict_bills(days_ahead=30)` to identify predicted bills from historical patterns
- **AND** predicted bills not already tracked (`is_tracked=false`) SHALL be proposed as a single `bill-predicted` insight candidate (priority 30, 7-day cooldown, 30-day expiry) naming the untracked payees, rather than included in an LLM-composed digest
- **AND** the routine "bill due within N days" reminder is intentionally NOT reproduced by this job — the daily `insight-scan` job already emits a `bill-due` candidate per bill due within 3 days on its own (now-daily) cadence, so repeating it here would double-notify

#### Scenario: Predicted bill accuracy feedback
- **WHEN** `predict_bills()` is called directly (e.g. via the `bill-reminder` skill)
- **THEN** each prediction SHALL include a `confidence` level
- **AND** high-confidence predictions (low amount variance, 6+ historical occurrences) SHALL be presented as likely upcoming bills
- **AND** medium-confidence predictions SHALL be presented as possible upcoming bills
- **AND** the `bill-reconciliation-sweep` job's `bill-predicted` insight candidate does NOT tier by confidence — it surfaces the untracked-pattern count and payee list only; confidence-tiered presentation is a direct-tool-call / skill behavior, not part of the scheduled insight candidate

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

### Requirement: Alert Scheduled Task Definitions
The finance butler SHALL register deterministic, job-mode (not prompt-mode) scheduled tasks that propose insight candidates for intelligence-driven alerts, per the bu-rvz2o migration (PR #2991, merged 678a29596).

#### Scenario: Daily insight scan schedule
- **WHEN** the finance butler daemon starts
- **THEN** it SHALL register a `dispatch_mode = "job"` schedule named `insight-scan` with cron `0 7 * * *` (daily at 7 AM) resolving to `run_insight_scan`
- **AND** this job evaluates, in order: spending anomalies (category-level, vs. 3-month rolling average), upcoming bills (3-day window), budget thresholds, subscription renewals (annual, 14-day window), and subscription price changes — each proposed via `propose_insight_candidate()`
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

