# Finance Pattern Recognition

## Purpose
Recurring charge auto-detection from transaction history, merchant auto-categorization with learned mappings, and bill prediction from historical payment patterns.

## ADDED Requirements

### Requirement: Recurring Charge Auto-Detection
The system SHALL analyze transaction history to identify subscription-like patterns and surface them as suggestions for subscription tracking.

#### Scenario: Detecting monthly recurring charges
- **WHEN** `detect_recurring(min_occurrences=3)` is called
- **THEN** the system SHALL query `finance.transactions WHERE deleted_at IS NULL` grouped by merchant
- **AND** for each merchant with `min_occurrences` or more transactions, it SHALL check for regular intervals (monthly: 25-35 day gaps, quarterly: 80-100 day gaps, yearly: 350-380 day gaps)
- **AND** it SHALL check for amount consistency (within 10% variance of the median amount for that merchant)
- **AND** matching patterns SHALL be returned as `recurring_pattern` dicts

#### Scenario: Recurring pattern response shape
- **WHEN** a recurring pattern is detected
- **THEN** each pattern dict SHALL contain: `merchant`, `estimated_frequency` (one of `weekly`, `monthly`, `quarterly`, `yearly`), `median_amount`, `amount_variance_pct`, `occurrence_count`, `last_charge_date`, `predicted_next_date`, `confidence` (one of `high`, `medium`, `low`), and `already_tracked` (boolean)
- **AND** `confidence` SHALL be `high` when occurrence_count >= 6 and amount_variance <= 5%, `medium` when occurrence_count >= 3 and amount_variance <= 10%, and `low` otherwise

#### Scenario: Excluding already-tracked subscriptions
- **WHEN** a recurring pattern matches an existing active subscription fact (same merchant)
- **THEN** the pattern SHALL still be returned but with `already_tracked=true`
- **AND** if the detected amount differs from the tracked subscription amount by more than 5%, a `price_change_detected` flag SHALL be set to `true`

#### Scenario: Filtering out non-subscription recurring charges
- **WHEN** a merchant appears frequently but with high amount variance (>25%)
- **THEN** it SHALL NOT be flagged as a recurring subscription pattern
- **AND** examples include grocery stores, gas stations, and restaurants where amounts vary significantly

### Requirement: Merchant Auto-Categorization
The system SHALL maintain a learned merchant-to-category mapping and apply it to new transactions that lack a category.

#### Scenario: Learning categories from historical data
- **WHEN** `learn_merchant_categories()` is called or a new transaction is recorded with a category
- **THEN** the system SHALL update the merchant category mapping by counting category assignments per merchant across all transactions in `finance.transactions WHERE deleted_at IS NULL`
- **AND** the most frequent category for each merchant SHALL be upserted into `finance.merchant_mappings` with `raw_pattern=<merchant_name>`, `normalized_merchant=<cleaned_merchant>`, `category=<most_frequent>`, `confidence=<ratio>`, `learned_from_count=<total>`, and `source='learned'`

#### Scenario: Confidence calculation
- **WHEN** a merchant category mapping is computed
- **THEN** `confidence` SHALL be calculated as `(most_frequent_count / total_count)` for that merchant and stored in the `confidence FLOAT` column of `finance.merchant_mappings`
- **AND** mappings with confidence < 0.6 SHALL be stored but queryable via `recall_merchant_mappings()` with a low-confidence filter

#### Scenario: Suggesting categories for uncategorized transactions
- **WHEN** `suggest_categories(transaction_ids=[...])` is called with a list of uncategorized transaction IDs
- **THEN** for each transaction, the system SHALL look up the merchant in `finance.merchant_mappings` using `ILIKE` pattern matching on `raw_pattern WHERE is_active = true`
- **AND** if a mapping is found, it SHALL return `{transaction_id, merchant, suggested_category, confidence}`
- **AND** if no mapping is found, it SHALL return `{transaction_id, merchant, suggested_category: null}`

#### Scenario: User corrections feed back into mappings
- **WHEN** a transaction's category is updated (via `update_transaction` with a corrected category)
- **THEN** the merchant category mapping in `finance.merchant_mappings` SHALL be refreshed to incorporate the correction
- **AND** the `learned_from_count` column SHALL increment

### Requirement: Bill Prediction from Historical Patterns
The system SHALL predict upcoming bills based on historical payment patterns to payees.

#### Scenario: Predicting next bill from payment history
- **WHEN** `predict_bills(days_ahead=30)` is called
- **THEN** the system SHALL analyze `finance.transactions WHERE deleted_at IS NULL` to identify payees with regular payment patterns (3+ payments at consistent intervals)
- **AND** for each identified payee, it SHALL compute the predicted next payment date based on the median interval between past payments
- **AND** it SHALL return predictions only for dates within the `days_ahead` window

#### Scenario: Bill prediction response shape
- **WHEN** a bill prediction is generated
- **THEN** each prediction SHALL contain: `payee`, `predicted_date`, `predicted_amount` (median of past amounts), `amount_range` (min and max of past amounts), `frequency`, `confidence`, `last_payment_date`, `payment_count`, and `is_tracked` (whether a bill fact already exists for this payee)

#### Scenario: Predictions do not duplicate tracked bills
- **WHEN** a predicted bill matches an existing pending bill fact (same payee, due date within 7 days of prediction)
- **THEN** the prediction SHALL be returned with `is_tracked=true`
- **AND** if the predicted amount differs from the tracked bill amount by more than 10%, a `amount_drift` field SHALL be set with the percentage difference
