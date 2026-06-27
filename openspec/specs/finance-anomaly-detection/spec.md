# Finance Anomaly Detection

## Purpose
Transaction anomaly detection engine that flags unusual merchants, amounts, times, and frequencies against established statistical baselines. Includes duplicate charge detection.

## ADDED Requirements

### Requirement: Statistical Baseline Computation
The system SHALL compute rolling statistical baselines from `finance.transactions` to enable anomaly detection. Baselines SHALL be computed per-merchant (median amount, standard deviation) and per-category (weekly spending velocity, typical transaction count).

#### Scenario: Merchant amount baseline
- **WHEN** `compute_baselines()` is called or baselines are refreshed
- **THEN** the system SHALL compute per-merchant statistics from the last 6 months of transactions in `finance.transactions WHERE deleted_at IS NULL`
- **AND** for each merchant with 3+ transactions, it SHALL store `median_amount`, `stddev_amount`, `transaction_count`, and `last_computed` as a memory fact with `predicate='spending_baseline'` and `content=<merchant_name>` (baselines remain in the memory fact layer as learned knowledge the LLM reasons about)
- **AND** merchants with fewer than 3 transactions SHALL be excluded from baseline computation

#### Scenario: Category velocity baseline
- **WHEN** baselines are computed
- **THEN** the system SHALL compute per-category weekly spending velocity (average weekly spend) from the last 6 months
- **AND** it SHALL store `avg_weekly_spend`, `stddev_weekly_spend`, and `avg_transaction_count` as a memory fact with `predicate='spending_baseline'` and `content=<category>`

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
- **WHEN** the total spending in a category for the scan window exceeds the category's baseline weekly velocity by more than the `sensitivity` multiplier
- **THEN** `anomaly_scan()` SHALL flag the category with `type='category_velocity_anomaly'`
- **AND** the flag SHALL include: `category`, `amount`, `severity`, and `explanation`

#### Scenario: Configurable sensitivity threshold
- **WHEN** `anomaly_scan(sensitivity=<str>)` is called with one of `"high"`, `"medium"`, or `"low"`
- **THEN** the deviation threshold SHALL be set from the `_SENSITIVITY_MULTIPLIERS` table: `"high"` = 1.5, `"medium"` = 2.0 (default), `"low"` = 3.0 standard deviations
- **AND** `"high"` SHALL produce more flags (smaller multiplier), `"low"` SHALL produce fewer
- **AND** an unrecognized value SHALL fall back to `"medium"`

#### Scenario: Empty baseline graceful handling
- **WHEN** `anomaly_scan()` is called but no baselines exist (no historical data)
- **THEN** it SHALL return an empty anomaly list with a `status` field set to `"insufficient_data"`
- **AND** it SHALL include a `message` explaining that 3+ months of transaction history is recommended

### Requirement: Duplicate Transaction Detection
The system SHALL detect potential duplicate charges -- transactions with the same merchant, same amount, and posted on the same day or adjacent days.

#### Scenario: Same-day duplicate detection
- **WHEN** `anomaly_scan()` or `detect_duplicates(days_back=30)` is called
- **THEN** the system SHALL identify pairs of transactions with the same merchant, same amount, and same `posted_at` date
- **AND** each pair SHALL be flagged as a `potential_duplicate` with both transaction IDs and a `confidence` field

#### Scenario: Adjacent-day duplicate detection
- **WHEN** two transactions share the same merchant and amount but are posted on consecutive days (1 day apart)
- **THEN** the system SHALL flag them as `potential_duplicate` with `confidence='medium'`
- **AND** same-day duplicates SHALL have `confidence='high'`

#### Scenario: Legitimate recurring charges excluded
- **WHEN** a duplicate candidate matches a tracked subscription (same merchant, same amount, expected frequency)
- **THEN** it SHALL NOT be flagged as a duplicate
- **AND** subscriptions SHALL be checked by querying active subscription facts for matching merchant and amount

### Requirement: Anomaly Scan Response Shape
The `anomaly_scan` tool SHALL return a structured response with consistent shape.

#### Scenario: Response structure
- **WHEN** `anomaly_scan()` completes
- **THEN** it SHALL return a dict with keys: `anomalies` (list of anomaly dicts), `total_flagged` (count), `status` (one of `"ok"`, `"insufficient_data"`), `scanned_transactions` (count), and `as_of` (ISO timestamp)
- **AND** each anomaly dict SHALL contain: `type` (one of `"amount_anomaly"`, `"new_merchant"`, `"category_velocity_anomaly"`), `severity` (one of `"low"`, `"medium"`, `"high"`), `transaction_id` (where applicable), `explanation` (human-readable string), and type-specific detail fields
- **AND** duplicate-charge detection is a separate `detect_duplicates()` tool returning `{duplicates, total_found, status, as_of}` with `confidence='high'` (same day) or `'medium'` (adjacent day)
