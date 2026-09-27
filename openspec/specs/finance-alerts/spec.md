# Finance Alerts

## Purpose
Configurable alert system -- large transaction alerts, subscription price change detection, bill reminders from historical patterns, and automated periodic spending summaries. Scheduled intelligence (anomaly, bill, budget, and monthly-digest detection) is delivered as proactive insight candidates through the switchboard insight broker (dedup/cooldown/quiet-hours/owner-verbosity), not via direct `notify()` calls from prompt-mode cron tasks.

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
- **AND** the alert types accepted by `alert_configure` SHALL be: `large_transaction`, `budget_exceeded`, `new_merchant`, `price_change`
- **AND** subscription price-change detection, bill reminders, and anomaly digests are delivered through dedicated tools (`detect_price_changes`, `predict_bills`, `anomaly_scan`) invoked from the deterministic `insight-scan`, `anomaly-insight-scan`, and `bill-reconciliation-sweep` scheduled jobs (see "Alert Scheduled Task Definitions" below and the schedule inventory in `butler-finance/spec.md`) rather than as configurable `alert_configure` types

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
- **AND** if the transaction amount differs from the tracked amount by more than 5%, it SHALL flag a price change
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
- **AND** the routine "bill due within N days" reminder is intentionally NOT reproduced by this job — the daily `insight-scan` job already emits a `bill-due` candidate per bill due within 3 days on its own daily cadence, so repeating it here would double-notify

#### Scenario: Predicted bill accuracy feedback
- **WHEN** `predict_bills()` is called directly (e.g. via the `bill-reminder` skill)
- **THEN** each prediction SHALL include a `confidence` level
- **AND** high-confidence predictions (low amount variance, 6+ historical occurrences) SHALL be presented as likely upcoming bills
- **AND** medium-confidence predictions SHALL be presented as possible upcoming bills
- **AND** the `bill-reconciliation-sweep` job's `bill-predicted` insight candidate does NOT tier by confidence — it surfaces the untracked-pattern count and payee list only; confidence-tiered presentation is a direct-tool-call / skill behavior, not part of the scheduled insight candidate

### Requirement: Automated Periodic Summaries
The system SHALL generate periodic financial summaries incorporating intelligence data, proposed as insight candidates rather than sent via unconditional direct notification.

#### Scenario: Monthly finance digest
- **WHEN** the `monthly-finance-digest` job (`0 9 1 * *`, `run_monthly_finance_digest`) fires on the 1st of the month
- **THEN** it SHALL compose and propose a single `monthly-finance-digest` insight candidate (priority 55, 25-day cooldown, dedup key `finance:monthly-digest:{YYYY-MM}`) covering the prior calendar month
- **AND** the message SHALL include: total spend, the top 3 spending categories by amount, budget status (categories not `on_track`, or "all categories on track"), and a subscription audit summary (active subscription count, projected annual cost, and untracked-pattern count if any)
- **AND** the message SHALL additionally include a month-over-month trend segment when prior-month data is available (see "Month-over-month trend content" below)
- **AND** delivery is subject to the owner's insight verbosity/budget like any other candidate, not an unconditional send

#### Scenario: Month-over-month trend content
- **WHEN** the `monthly-finance-digest` job composes its candidate
- **THEN** it SHALL append a month-over-month trend segment comparing the covered month against the immediately preceding calendar month, aggregated per debit category
- **AND** the segment SHALL state the overall total-spend direction (`up`, `down`, or `flat`) and the absolute percentage change versus the prior month (labeled `YYYY-MM`)
- **AND** it SHALL list "notable changes": each debit category whose spend swings by more than 20% month-over-month (formatted `{category} {+/-}{pct}%`), each category that newly appeared this month (`{category} (new)`), and each category that had prior-month spend but none this month (`{category} (no spend)`)
- **AND** notable changes SHALL be ordered by the absolute size of the swing (largest first) and capped at 5, with any remainder summarized as `(+N more)`
- **AND** when there is insufficient prior-month data to compute a meaningful comparison (no prior-month debit spend), the trend bullet SHALL be omitted entirely rather than shown empty
- **AND** a failure to compute the trend SHALL never block the digest — the digest is proposed without the trend segment (graceful degradation)

#### Scenario: Category-level spending anomalies via the daily insight scan
- **WHEN** the `insight-scan` job (`0 7 * * *`, `run_insight_scan`) evaluates spending anomalies — its first evaluation step
- **THEN** it SHALL compare each debit category's current-month-to-date spend against that category's 3-month rolling monthly average, where a category is eligible only if it has debit activity in at least 3 distinct calendar months within the trailing 3-month window AND its rolling average is positive; categories with fewer than 3 months of history or a non-positive average are excluded
- **AND** it SHALL propose a `spending-anomaly` insight candidate for each eligible category whose current-month spend exceeds its rolling average by more than 30%; a category at or below +30% produces no candidate
- **AND** priority SHALL be 80 when spend is more than 100% above the average, 65 when more than 50% above, and 50 for the 30–50% band
- **AND** the candidate SHALL carry a month-scoped dedup key `finance:spending-anomaly:{category}:{YYYY-MM}` and expire at the end of the current calendar month; it sets NO explicit cooldown — the monthly dedup key alone bounds it to at most one candidate per category per month
- **AND** the message SHALL name the category, the percentage above the 3-month average, and the current and average amounts; metadata SHALL include `category`, `current`, and `average`
- **AND** this candidate is DISTINCT from the `anomaly-insight-scan` job's per-transaction `spending-anomaly-transaction` candidate (see "Per-transaction anomaly candidates via the daily anomaly insight scan"): this one is a monthly category-total-versus-rolling-average comparison emitted by `insight-scan` at `0 7 * * *`, whereas that one flags individual transaction outliers (`amount_anomaly`, `new_merchant`, `category_velocity_anomaly`) daily via `anomaly_scan()` at `0 21 * * *`; they use different candidate categories, dedup-key shapes, cooldowns, and priority scales, and neither supersedes the other

#### Scenario: Budget thresholds via the daily insight scan
- **WHEN** the `insight-scan` job (`0 7 * * *`) evaluates budget thresholds
- **THEN** it SHALL call `budget_status()` (which aligns each budget's spending window to its own period via `DATE_TRUNC`) and propose a `budget-threshold` insight candidate for every active budget — of any period (`weekly`, `monthly`, `quarterly`, `yearly`) — whose utilization is at or above that budget's own configured `warn_threshold` (default 0.80)
- **AND** priority SHALL be 70 when utilization is at or above the budget's `alert_threshold` (default 1.00), else 50
- **AND** the candidate SHALL include category, budget period, spent amount, budget amount, and utilization percentage, with a period-and-severity-scoped dedup key `finance:budget-threshold:{category}:{time-scope}-{status}` whose time-scope segment resets at that period's boundary (`weekly`→`YYYY-Www`, `monthly`→`YYYY-MM`, `quarterly`→`YYYY-Qn`, `yearly`→`YYYY`) and whose `{status}` suffix is `warning` or `exceeded` — the four time-scope formats are mutually unambiguous, so a monthly and a yearly budget for the same category never share a dedup key
- **AND** the candidate's cooldown SHALL span the remainder of that budget's current period window, so a crossing fires at most once per `(budget, window, severity)` and the next window's fresh dedup key re-fires
- **AND** because `{status}` is folded into the dedup key, a budget that crosses `warn_threshold` and later crosses `alert_threshold` within the same window SHALL surface BOTH a `warning` and an `exceeded` candidate under distinct keys — the earlier warning's cooldown SHALL NOT silence the escalation to exceeded (which otherwise, for a long window such as `yearly`, would leave the exceeded state unreported for the remainder of the window)
- **AND** if no budget is at or above its `warn_threshold`, no `budget-threshold` candidate SHALL be proposed

#### Scenario: Per-transaction anomaly candidates via the daily anomaly insight scan
- **WHEN** the `anomaly-insight-scan` job (`0 21 * * *`, `run_anomaly_insight_scan`) fires
- **THEN** it SHALL call `anomaly_scan(days_back=1, sensitivity="medium")` and propose each flagged anomaly as its own dedupeable, severity-scored `spending-anomaly-transaction` insight candidate (see "Large Transaction Alerts" above for the scan mechanics and cap)
- **AND** if no anomalies are found, no candidates SHALL be proposed

### Requirement: Alert Scheduled Task Definitions
Every finance intelligence alert SHALL be produced by a deterministic, job-mode (not prompt-mode) scheduled task that proposes insight candidates; the schedule names, crons, and handlers are owned by the "Finance Butler Schedules" inventory in `butler-finance/spec.md`, and this capability owns what each job evaluates and proposes.

#### Scenario: Daily insight scan evaluation order
- **WHEN** the `insight-scan` job runs
- **THEN** it SHALL evaluate, in order: spending anomalies (category-level, vs. 3-month rolling average), upcoming bills (3-day window), budget thresholds, subscription renewals (annual, 14-day window), and subscription price changes, each proposed via `propose_insight_candidate()`
- **AND** the category-level spending-anomaly step is specified in full by the "Category-level spending anomalies via the daily insight scan" scenario above (distinct from the per-transaction `spending-anomaly-transaction` candidate emitted by `anomaly-insight-scan`)

#### Scenario: Bill reconciliation sweep mutation and surfacing
- **WHEN** the `bill-reconciliation-sweep` job runs
- **THEN** `reconcile_bills()` SHALL remain a deterministic, always-run mutating step (not gated by insight verbosity)
- **AND** its results (auto-settled bills, ambiguous confirm-tier matches, untracked recurring patterns) SHALL be surfaced as insight candidates

#### Scenario: No direct-notify alert tasks
- **WHEN** enumerating the finance butler's schedules
- **THEN** no prompt-mode task SHALL deliver budget, bill, subscription-renewal, anomaly, or monthly-summary alerts via direct `notify()`
- **AND** budget-threshold and subscription-renewal/price-change alerts SHALL be covered by the daily `insight-scan` job rather than separate schedules

### Requirement: Finance proactive recurrence output does not infer missing payment state

Finance proactive output MUST keep forward-looking declared renewals and predicted bills distinct
from expected-signal absence. No current alert policy consumes an elapsed recurrence signal as a
missed renewal, failed payment, cancellation, pause, or stopped subscription.

ID: REQ-finance-alerts-001
Source: RFC 0011; RFC 0029 §Initial adoption

#### Scenario: Existing tracked annual renewal reminder remains forward-looking

- **WHEN** an active yearly tracked subscription has a declared `next_renewal` within 14 days
- **THEN** the existing `subscription-renewal` candidate policy MAY run with its current priority,
  deduplication, expiry, and wording
- **AND** that reminder MUST NOT claim whether the future charge was or will be observed

#### Scenario: Existing predicted-bill policy remains forward-looking

- **WHEN** an untracked regular payment has a predicted date within the existing 30-day horizon
- **THEN** the existing `bill-predicted` candidate policy MAY run unchanged
- **AND** the prediction MUST NOT be converted into tracked subscription, payment, or cancellation
  state

#### Scenario: Unmeasurable elapsed recurrence produces no candidate

- **WHEN** `next_expected_date` has elapsed and its producer is stale, dead/offline, unhealthy,
  missing, unsupported, mixed, or unreadable
- **THEN** Finance MUST NOT propose any missed-recurrence or missed-renewal candidate
- **AND** Finance MUST NOT attribute the gap to owner behavior or merchant/payment state

#### Scenario: Healthy absent recurrence has no implicit alert consumer

- **WHEN** a healthy/current single producer yields an `absent` recurrence signal
- **THEN** Finance MUST persist or expose that state only through the RFC 0029 contract
- **AND** it MUST NOT emit a candidate until a separately approved alert requirement names that
  consumer, wording, priority, deduplication, cooldown, and expiry
