## MODIFIED Requirements

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
- **THEN** it SHALL call `budget_status()` (which aligns each budget's spending window to its own period via `DATE_TRUNC`-equivalent owner-calendar bounds) and propose a `budget-threshold` insight candidate for every active budget — of any period (`weekly`, `monthly`, `quarterly`, `yearly`) — whose utilization is at or above that budget's own configured `warn_threshold` (default 0.80)
- **AND** priority SHALL be 70 when utilization is at or above the budget's `alert_threshold` (default 1.00), else 50
- **AND** the candidate SHALL include category, budget period, spent amount, budget amount, and utilization percentage, with a period-and-severity-scoped dedup key `finance:budget-threshold:{category}:{time-scope}-{status}` whose time-scope segment resets at that period's boundary (`daily`→`YYYY-MM-DD`, `weekly`→`YYYY-Www`, `monthly`→`YYYY-MM`, `quarterly`→`YYYY-Qn`, `yearly`→`YYYY`) and whose `{status}` suffix is `warning` or `exceeded` — the five time-scope formats are mutually unambiguous, so a monthly and a yearly budget for the same category never share a dedup key
- **AND** the candidate's cooldown SHALL span the remainder of that budget's current period window, so a crossing fires at most once per `(budget, window, severity)` and the next window's fresh dedup key re-fires
- **AND** because `{status}` is folded into the dedup key, a budget that crosses `warn_threshold` and later crosses `alert_threshold` within the same window SHALL surface BOTH a `warning` and an `exceeded` candidate under distinct keys — the earlier warning's cooldown SHALL NOT silence the escalation to exceeded (which otherwise, for a long window such as `yearly`, would leave the exceeded state unreported for the remainder of the window)
- **AND** if no budget is at or above its `warn_threshold`, no `budget-threshold` candidate SHALL be proposed
- **AND** daily SHALL also be a supported budget period and SHALL receive the same per-budget threshold evaluation
- **AND** a daily budget candidate SHALL expire at the next owner-local midnight, use a one-day cooldown, and retain the distinct `warning`/`exceeded` identities; budget-pressure evidence SHALL share that day window

#### Scenario: Per-transaction anomaly candidates via the daily anomaly insight scan
- **WHEN** the `anomaly-insight-scan` job (`0 21 * * *`, `run_anomaly_insight_scan`) fires
- **THEN** it SHALL call `anomaly_scan(days_back=1, sensitivity="medium")` and propose each flagged anomaly as its own dedupeable, severity-scored `spending-anomaly-transaction` insight candidate (see "Large Transaction Alerts" above for the scan mechanics and cap)
- **AND** if no anomalies are found, no candidates SHALL be proposed
