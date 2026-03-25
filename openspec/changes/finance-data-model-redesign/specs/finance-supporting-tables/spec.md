# Finance Supporting Tables

## ADDED Requirements

### Requirement: Accounts table enhancements
The `finance.accounts` table SHALL be enhanced with lifecycle and sync tracking fields.

#### Scenario: Account lifecycle management
- **WHEN** an account is deactivated (e.g., closed bank account)
- **THEN** `is_active BOOLEAN` SHALL be set to `false` (default: `true`)
- **AND** the account SHALL NOT be deleted from the database
- **AND** `idx_accounts_active` SHALL index active accounts with partial condition `WHERE is_active = true`

#### Scenario: Account sync tracking
- **WHEN** data is imported for an account
- **THEN** `last_synced_at TIMESTAMPTZ` SHALL be updated to the current timestamp

#### Scenario: Extended account types
- **WHEN** an account is created
- **THEN** the `type` CHECK constraint SHALL accept: `'checking'`, `'savings'`, `'credit'`, `'investment'`, `'loan'`, `'other'`

### Requirement: Hierarchical category taxonomy
The `finance.categories` table SHALL provide a consistent hierarchical category taxonomy with tax-relevance tracking.

#### Scenario: Category creation
- **WHEN** a category is created
- **THEN** it SHALL have a unique `name TEXT` (lowercase, e.g., `'groceries'`), an optional `display_name TEXT` (human-readable, e.g., `'Groceries'`), and an optional `parent_id UUID` referencing another category for hierarchy

#### Scenario: Tax-relevant categories
- **WHEN** a category is marked as tax-relevant
- **THEN** `is_tax_relevant BOOLEAN` SHALL be `true`
- **AND** `tax_category TEXT` SHALL specify the mapped tax bucket (e.g., `'medical'`, `'charitable'`, `'education'`)

#### Scenario: System vs user categories
- **WHEN** a category is created by the system during initial seeding
- **THEN** `is_system BOOLEAN` SHALL be `true`
- **AND** system categories SHALL NOT be deleted by users

#### Scenario: Default category seeding
- **WHEN** the migration runs
- **THEN** it SHALL seed default categories: `groceries`, `dining`, `transport`, `subscriptions`, `utilities`, `housing`, `healthcare`, `entertainment`, `shopping`, `travel`, `education` (tax: education), `medical` (tax: medical), `charitable` (tax: charitable), `home_office` (tax: home_office), `business_expense` (tax: business_expense), `insurance`, `personal_care`, `gifts`, `income`, `transfer`, `fees`, `uncategorized`
- **AND** seeding SHALL be idempotent (`ON CONFLICT DO NOTHING`)

### Requirement: Merchant-to-category mapping table
The `finance.merchant_mappings` table SHALL store learned and manual merchant-to-category mappings for auto-categorization.

#### Scenario: Merchant mapping structure
- **WHEN** a merchant mapping is created
- **THEN** it SHALL include `raw_pattern TEXT` (the merchant string pattern for ILIKE matching), `normalized_merchant TEXT` (cleaned merchant name), `category TEXT` (default category), `confidence FLOAT` (ratio of most-frequent to total), and `learned_from_count INTEGER`

#### Scenario: Merchant mapping uniqueness
- **WHEN** a mapping is created for a merchant pattern
- **THEN** `uq_merchant_mapping_pattern` SHALL enforce `UNIQUE (lower(raw_pattern))` with partial condition `WHERE is_active = true`
- **AND** deactivated mappings SHALL NOT conflict with new active mappings

#### Scenario: Merchant mapping source tracking
- **WHEN** a merchant mapping is created
- **THEN** `source TEXT` SHALL record how the mapping was established: `'learned'` (from transaction history), `'manual'` (user-defined), or `'import'` (from external mapping file)

### Requirement: Recurring charge group tracking
The `finance.recurring_groups` table SHALL track detected recurring charge patterns and link them to individual transactions.

#### Scenario: Recurring group structure
- **WHEN** a recurring charge pattern is detected
- **THEN** it SHALL be recorded with `merchant TEXT`, `expected_amount NUMERIC(14,2)`, `currency CHAR(3)`, `frequency TEXT` (one of `'weekly'`, `'monthly'`, `'quarterly'`, `'yearly'`), `occurrence_count INTEGER`, `last_charge_date DATE`, `next_expected_date DATE`, and `confidence TEXT` (one of `'high'`, `'medium'`, `'low'`)

#### Scenario: Recurring group lifecycle
- **WHEN** a recurring group is tracked
- **THEN** `status TEXT` SHALL be one of: `'active'`, `'paused'`, `'stopped'`
- **AND** `is_subscription BOOLEAN` SHALL indicate whether the group is confirmed as a subscription
- **AND** `subscription_id UUID` SHALL optionally reference `finance.subscriptions(id)` when linked to a tracked subscription

### Requirement: Import batch audit trail
The `finance.import_batches` table SHALL track each data import operation for provenance and auditability.

#### Scenario: Import batch structure
- **WHEN** a bulk import is initiated
- **THEN** an import batch row SHALL be created with `source TEXT` (e.g., `'chase_csv'`, `'amex_csv'`, `'generic_csv'`), `filename TEXT`, `account_id UUID`, and `status TEXT` set to `'pending'`

#### Scenario: Import batch progress tracking
- **WHEN** an import batch is processed
- **THEN** `row_count`, `imported_count`, `skipped_count`, and `error_count` SHALL be updated as rows are processed
- **AND** `date_range_start DATE` and `date_range_end DATE` SHALL reflect the earliest and latest transaction dates in the batch
- **AND** `detected_format TEXT` and `column_mapping JSONB` SHALL record the auto-detected format and column mapping used

#### Scenario: Import batch completion
- **WHEN** an import batch finishes processing
- **THEN** `status` SHALL be updated to `'completed'`, `'completed_with_errors'`, or `'failed'`
- **AND** `completed_at TIMESTAMPTZ` SHALL be set
- **AND** `error_details JSONB` SHALL contain an array of `{row, reason}` objects for failed rows
- **AND** `baselines_computed BOOLEAN` SHALL indicate whether statistical baselines were recomputed after import
- **AND** `categories_learned INTEGER` SHALL record how many new merchant-category mappings were learned

### Requirement: Balance snapshots for net worth tracking
The `finance.balance_snapshots` table SHALL store periodic account balance records for net worth calculation.

#### Scenario: Balance snapshot structure
- **WHEN** a balance snapshot is recorded
- **THEN** it SHALL include `account_id UUID` (referencing `finance.accounts`), `balance NUMERIC(14,2)` (negative for credit/loan accounts), `currency CHAR(3)`, `as_of_date DATE`, and `source TEXT` (one of `'manual'`, `'import'`, `'statement'`)

#### Scenario: Balance snapshot uniqueness
- **WHEN** a balance snapshot is recorded for an account on a specific date
- **THEN** `uq_balance_snapshot_account_date` SHALL enforce `UNIQUE (account_id, as_of_date)`
- **AND** a duplicate snapshot for the same account and date SHALL upsert (update balance) rather than create a new row

### Requirement: Category-level budget targets
The `finance.budgets` table SHALL store budget targets with configurable thresholds for spending alerts.

#### Scenario: Budget structure
- **WHEN** a budget is created
- **THEN** it SHALL include `category TEXT`, `amount NUMERIC(14,2)`, `currency CHAR(3)` (default `'USD'`), `period TEXT` (one of `'weekly'`, `'monthly'`, `'quarterly'`, `'annual'`), `warn_threshold FLOAT` (default `0.8`), and `alert_threshold FLOAT` (default `1.0`)

#### Scenario: Budget uniqueness
- **WHEN** a budget is created for a category and period
- **THEN** `uq_budget_category_period` SHALL enforce `UNIQUE (category, period)` with partial condition `WHERE is_active = true`
- **AND** deactivated budgets SHALL NOT conflict with new active budgets

### Requirement: Transaction corrections audit trail
The `finance.transaction_corrections` table SHALL record all edits to transaction data for audit compliance.

#### Scenario: Correction structure
- **WHEN** a transaction field is modified
- **THEN** a correction row SHALL be created with `transaction_id UUID`, `field_name TEXT` (the column that changed), `old_value TEXT`, `new_value TEXT`, `reason TEXT` (optional), and `source TEXT` (one of `'user'`, `'rule'`, `'auto'`, `'merge'`)

#### Scenario: Correction indexing
- **WHEN** corrections are queried for a transaction
- **THEN** `idx_correction_txn` SHALL index `(transaction_id)` for efficient lookup
- **AND** `idx_correction_created` SHALL index `(created_at DESC)` for chronological audit review

### Requirement: Materialized spending summaries
The `finance.spending_summaries` materialized view SHALL pre-aggregate monthly spending for dashboard and trend queries.

#### Scenario: Spending summary structure
- **WHEN** the materialized view is refreshed
- **THEN** it SHALL aggregate from `finance.transactions WHERE deleted_at IS NULL`
- **AND** it SHALL group by `DATE_TRUNC('month', posted_at)::date` (as `period`), `account_id`, `category`, `direction`, and `currency`
- **AND** it SHALL compute `COUNT(*)` (transaction_count), `SUM(amount)` (total_amount), `AVG(amount)` (avg_amount), `MIN(amount)` (min_amount), `MAX(amount)` (max_amount)

#### Scenario: Spending summary refresh
- **WHEN** a bulk import completes or the daily anomaly digest runs
- **THEN** `REFRESH MATERIALIZED VIEW CONCURRENTLY finance.spending_summaries` SHALL be executed
- **AND** the refresh SHALL NOT block concurrent reads
- **AND** `uq_spending_summary_key` SHALL enforce `UNIQUE (period, account_id, category, direction, currency)` to support concurrent refresh
