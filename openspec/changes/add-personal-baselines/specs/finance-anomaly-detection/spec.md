## MODIFIED Requirements

### Requirement: Statistical Baseline Computation
The system SHALL compute rolling statistical baselines from `finance.transactions` to enable anomaly detection. Merchant baselines SHALL be computed per-merchant (median amount, standard deviation) and kept as inline computation. Category spending baselines SHALL be the weekly, currency-scoped personal baselines of the core scorer (`butlers.core.baselines`, one `spend.category:<category>:<CCY>` metric per category and currency, `bucket = week`, 26-week window, `min_n = 4`) stored in the finance schema's `metric_baselines`; no baseline SHALL be written as a `spending_baseline` memory fact.

#### Scenario: Merchant amount baseline
- **WHEN** `compute_baselines()` is called or baselines are refreshed
- **THEN** the system SHALL compute per-merchant statistics from the last 6 months of transactions in `finance.transactions WHERE deleted_at IS NULL`
- **AND** for each merchant with 3+ transactions, it SHALL compute `median_amount`, `stddev_amount`, and `transaction_count` inline for the scan and SHALL NOT store them as a memory fact
- **AND** merchants with fewer than 3 transactions SHALL be excluded from baseline computation

#### Scenario: Category velocity baseline
- **WHEN** the daily anomaly insight scan runs
- **THEN** the system SHALL compute, for each (category, currency) pair with debit spend, the median and MAD band of its weekly spend over up to 26 complete owner-local weeks (Monday start) and upsert it into `metric_baselines` under `spend.category:<category>:<CCY>`
- **AND** a week with no spend SHALL NOT be a sample, so the band reports `n_observed` of `n_expected` honestly and reports `insufficient_history` until 4 weeks of spend exist
- **AND** the same category in two currencies SHALL be two independent metrics, with no conversion
- **AND** a rerun with unchanged inputs SHALL leave every stored band unchanged

#### Scenario: Legacy spending_baseline facts retired
- **WHEN** the daily anomaly insight scan runs
- **THEN** it SHALL retract (`validity = 'retracted'`, rows kept) the `spending_baseline` facts of `source_butler = 'finance'` and `content_authority = 'system'`, and SHALL leave owner-authored facts of that predicate alone

#### Scenario: Baseline refresh on historical import
- **WHEN** `bulk_record_transactions` completes a batch import of 50+ transactions
- **THEN** the system SHALL trigger a baseline refresh to incorporate the newly imported data

### Requirement: Transaction Anomaly Scanning
The system SHALL provide an `anomaly_scan` tool that analyzes recent transactions against baselines and returns flagged anomalies with explanations.

#### Scenario: Amount anomaly detection
- **WHEN** `anomaly_scan(days_back=30)` is called
- **THEN** for each transaction in the scan window, the system SHALL compare the amount against the merchant's baseline
- **AND** if the amount deviates by more than the configured threshold (the `sensitivity` multiplier, default `"medium"` = 2.0 standard deviations), it SHALL flag the transaction with `type='amount_anomaly'`
- **AND** the flag SHALL include: `transaction_id`, `merchant`, `amount`, `type`, `severity`, and `explanation`

#### Scenario: New merchant detection
- **WHEN** `anomaly_scan()` encounters a transaction from a merchant with no baseline (first-time merchant)
- **THEN** it SHALL flag the transaction as a `new_merchant` anomaly type
- **AND** the flag SHALL include the merchant name, amount, and a note that no historical pattern exists

#### Scenario: Category velocity anomaly
- **WHEN** a category's spend so far in the current owner-local week, in one currency, scores `above` the stored personal baseline band for that category and currency
- **THEN** `anomaly_scan()` SHALL flag the category with `type='category_velocity_anomaly'`
- **AND** the flag SHALL include: `category`, `currency`, `amount`, `severity` (from the deviation in MAD units), `mad_units`, `band`, `baseline_evidence` and `explanation` stating the usual range and the weeks it is drawn from
- **AND** a category with insufficient history, a stale band (older than two weeks), no stored band or a week within its band SHALL NOT be flagged
- **AND** the `sensitivity` setting SHALL NOT move a category band

#### Scenario: An above-band category is raised once per week
- **WHEN** the daily anomaly insight scan proposes a candidate for a `category_velocity_anomaly`
- **THEN** its dedup key SHALL be built from the category, the currency and the owner-local week whose spend was scored, so that day after day of the same week yields the same key
- **AND** the candidate's cooldown SHALL cover that whole week, so an above-band category is delivered at most once per category, currency and week
- **AND** a later severity change within the same week SHALL NOT raise it again, and the next owner-local week SHALL be a new key
- **AND** per-transaction anomalies SHALL keep their per-transaction, per-day key

#### Scenario: Configurable sensitivity threshold
- **WHEN** `anomaly_scan(sensitivity=<str>)` is called with one of `"high"`, `"medium"`, or `"low"`
- **THEN** the deviation threshold SHALL be set from the `_SENSITIVITY_MULTIPLIERS` table: `"high"` = 1.5, `"medium"` = 2.0 (default), `"low"` = 3.0 standard deviations
- **AND** `"high"` SHALL produce more flags (smaller multiplier), `"low"` SHALL produce fewer
- **AND** an unrecognized value SHALL fall back to `"medium"`

#### Scenario: Empty baseline graceful handling
- **WHEN** `anomaly_scan()` is called but no baselines exist (no historical data)
- **THEN** it SHALL return an empty anomaly list with a `status` field set to `"insufficient_data"`
- **AND** it SHALL include a `message` explaining that 3+ months of transaction history is recommended
