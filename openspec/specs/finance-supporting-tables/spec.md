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
- **THEN** it SHALL seed default categories: `groceries`, `dining`, `transport`, `subscriptions`, `utilities`, `housing`, `healthcare` (tax: medical), `entertainment`, `shopping`, `travel`, `education` (tax: education), `medical` (tax: medical), `charitable` (tax: charitable), `fees`, `income`, `transfer`, `uncategorized` (17 system categories seeded by `finance_006`)
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
- **THEN** `source TEXT` SHALL record how the mapping was established: `'manual'` (user-defined) or `'learned'` (from transaction history), enforced by the `merchant_mappings_source_check` CHECK constraint in `finance_006`

### Requirement: Recurring charge group tracking
The `finance.recurring_groups` table SHALL track detected recurring charge patterns and link them to individual transactions.

#### Scenario: Recurring group structure
- **WHEN** a recurring charge pattern is detected and persisted
- **THEN** it SHALL be recorded with `merchant TEXT` (UNIQUE, backing an upsert on conflict), `avg_amount NUMERIC(14,2)`, `currency CHAR(3)`, `estimated_frequency TEXT` (NULL or one of `'weekly'`, `'monthly'`, `'quarterly'`, `'yearly'`, `'custom'`), `last_seen_date DATE`, and `next_expected_date DATE`
- **AND** the `id UUID` primary key SHALL be referenced by `finance.transactions.recurring_group_id`

#### Scenario: Recurring group lifecycle
- **WHEN** a recurring group is tracked
- **THEN** `is_active BOOLEAN` SHALL indicate whether the group is currently considered active
- **AND** detecting the same merchant again SHALL upsert the existing row (ON CONFLICT on `merchant`) rather than create a duplicate

### Requirement: Import batch correlation
Each bulk import operation SHALL stamp an ephemeral `import_batch_id` correlator onto every transaction it inserts, so that rows ingested in the same import can be grouped after the fact. There is no persisted `finance.import_batches` audit-trail table; the correlator lives in the transaction's `metadata` JSONB.

#### Scenario: Import batch correlator generation
- **WHEN** a bulk import is initiated
- **THEN** the import operation SHALL generate a single in-memory `import_batch_id` (a UUIDv4 string) for that run
- **AND** the same `import_batch_id` SHALL be used for every row inserted by that import

#### Scenario: Import batch correlator stamping
- **WHEN** a transaction row is inserted during a bulk import
- **THEN** the row's `metadata` JSONB SHALL include `import_batch_id` (the run's correlator) alongside `raw_merchant`
- **AND** transactions sharing an `import_batch_id` in their metadata SHALL be groupable as a single import

#### Scenario: Import result reporting
- **WHEN** a bulk import finishes processing
- **THEN** the import result SHALL report `total`, `imported`, `skipped`, `errors`, `import_batch_id`, and `detected_format`
- **AND** the result's `import_batch_id` SHALL be returned even when the import fails or encounters per-row errors
- **AND** these counts SHALL NOT be persisted to a dedicated batch table — they exist only in the returned result

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
- **THEN** it SHALL include `category TEXT`, `amount NUMERIC(14,2)`, `currency CHAR(3)` (default `'USD'`), `period TEXT` (one of `'weekly'`, `'monthly'`, `'yearly'`, `'daily'`, enforced by the `budgets_period_check` CHECK constraint in `finance_006`), `warn_threshold FLOAT` (default `0.8`), and `alert_threshold FLOAT` (default `1.0`)

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
