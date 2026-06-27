# Finance Transaction Schema

## ADDED Requirements

### Requirement: Enhanced transaction table columns
The `finance.transactions` table SHALL include columns for intelligence features beyond the base ledger fields.

#### Scenario: External ID for bank deduplication
- **WHEN** a transaction is imported from a bank that provides stable transaction identifiers
- **THEN** the `external_id TEXT` column SHALL store the bank-provided transaction ID
- **AND** it SHALL be used as the highest-priority deduplication key in combination with `account_id`

#### Scenario: Transaction date vs posted date
- **WHEN** a transaction has a different original transaction date from its posted date
- **THEN** the `transaction_date DATE` column SHALL store the original transaction date
- **AND** `posted_at TIMESTAMPTZ` SHALL continue to store the posted/settlement date

#### Scenario: Normalized description and merchant
- **WHEN** a transaction is recorded
- **THEN** the `normalized_description TEXT` column SHALL store a cleaned/standardized version of the raw `description`
- **AND** the `normalized_merchant TEXT` column SHALL store a cleaned version of the raw `merchant` (stripped of trailing transaction IDs, card numbers, date stamps, location codes)

#### Scenario: Subcategory and tags
- **WHEN** a transaction is categorized
- **THEN** the `subcategory TEXT` column SHALL support hierarchical categorization beneath the primary `category`
- **AND** the `tags TEXT[]` column SHALL store zero or more user-defined labels (e.g., `'tax-deductible'`, `'reimbursable'`)
- **AND** `tags` SHALL default to an empty array `'{}'`

#### Scenario: Category source tracking
- **WHEN** a transaction's category is set
- **THEN** the `category_source TEXT` column SHALL record how the category was determined
- **AND** it SHALL accept values: `'auto'`, `'manual'`, `'ml'`, `'rule'` (enforced by the `transactions_category_source_check` CHECK constraint in `finance_006`)
- **AND** it SHALL default to `'auto'`

#### Scenario: Category lock on manual override
- **WHEN** a user manually overrides a transaction's category
- **THEN** `is_category_locked BOOLEAN` SHALL be set to `true`
- **AND** automatic re-categorization (via merchant mappings or rules) SHALL skip transactions where `is_category_locked = true`
- **AND** `is_category_locked` SHALL default to `false`

#### Scenario: Transaction type classification
- **WHEN** a transaction is recorded
- **THEN** the `type TEXT` column SHALL classify the transaction as one of: `'purchase'`, `'refund'`, `'transfer'`, `'fee'`, `'adjustment'`, `'other'`
- **AND** it SHALL default to `'purchase'`

#### Scenario: Recurring charge detection flags
- **WHEN** a transaction is identified as part of a recurring charge pattern
- **THEN** `is_recurring BOOLEAN` SHALL be set to `true`
- **AND** `recurring_group_id UUID` SHALL reference the `finance.recurring_groups` row that groups the pattern
- **AND** `is_recurring` SHALL default to `false`

#### Scenario: Duplicate handling flags
- **WHEN** a transaction is identified as a duplicate of another transaction
- **THEN** `is_duplicate BOOLEAN` SHALL be set to `true`
- **AND** `duplicate_of UUID` SHALL reference the canonical transaction's `id`
- **AND** `is_duplicate` SHALL default to `false`

#### Scenario: Import provenance
- **WHEN** a transaction is created via a bulk import
- **THEN** the row's `metadata` JSONB SHALL carry the import run's ephemeral `import_batch_id` correlator (there is no `finance.import_batches` table to reference; it was dropped by `finance_007`)
- **AND** a legacy `import_batch_id UUID` column SHALL remain on the table (created by `finance_006`) but SHALL no longer be FK-linked or populated by the importer
- **AND** `source TEXT` SHALL record the ingestion channel: `'manual'`, `'csv_import'`, `'email'`, `'api'`, `'bank_sync'` (enforced by the `transactions_source_check` CHECK constraint in `finance_006`)
- **AND** `source` SHALL default to `'manual'`
- **AND** `raw_data JSONB` SHALL preserve the original import row for audit purposes
- **AND** `raw_data` SHALL default to `'{}'::jsonb`

#### Scenario: User annotations
- **WHEN** a user adds notes to a transaction
- **THEN** the `notes TEXT` column SHALL store the annotation text

#### Scenario: Soft delete lifecycle
- **WHEN** a transaction is deleted
- **THEN** `deleted_at TIMESTAMPTZ` SHALL be set to the current timestamp
- **AND** the transaction SHALL NOT be hard-deleted from the database
- **AND** all normal queries SHALL exclude rows where `deleted_at IS NOT NULL`

#### Scenario: Optimistic locking
- **WHEN** a transaction is updated
- **THEN** `version INTEGER` SHALL be incremented
- **AND** the update query SHALL include `WHERE version = $expected_version` to detect concurrent modifications
- **AND** `version` SHALL default to `1`

### Requirement: Transaction table indexing strategy
The `finance.transactions` table SHALL have indexes covering the five primary query patterns: date-range scans, category spending, merchant lookup, amount range, and account scoping.

#### Scenario: Date-range scan indexes
- **WHEN** transactions are queried by date range
- **THEN** `idx_txn_posted_at` SHALL index `(posted_at DESC)`
- **AND** `idx_txn_transaction_date` SHALL index `(transaction_date)` with partial condition `WHERE transaction_date IS NOT NULL`
- **AND** `idx_txn_active` SHALL index `(posted_at DESC)` with partial condition `WHERE deleted_at IS NULL`

#### Scenario: Category spending indexes
- **WHEN** transactions are aggregated by category
- **THEN** `idx_txn_category` SHALL index `(category)`
- **AND** `idx_txn_category_posted` SHALL index `(category, posted_at DESC)`
- **AND** `idx_txn_debit_category_posted` SHALL index `(category, posted_at)` with partial condition `WHERE direction = 'debit' AND deleted_at IS NULL`

#### Scenario: Merchant lookup indexes
- **WHEN** transactions are queried by merchant
- **THEN** `idx_txn_merchant` SHALL index `(merchant)`
- **AND** `idx_txn_normalized_merchant` SHALL index `(normalized_merchant)` with partial condition `WHERE normalized_merchant IS NOT NULL`

#### Scenario: Amount and account indexes
- **WHEN** transactions are queried by amount range or scoped to an account
- **THEN** `idx_txn_amount` SHALL index `(amount)`
- **AND** `idx_txn_account_id` SHALL index `(account_id)` with partial condition `WHERE account_id IS NOT NULL`

#### Scenario: Direction filtering index
- **WHEN** transactions are filtered by direction (debit/credit)
- **THEN** `idx_txn_direction_posted` SHALL index `(direction, posted_at DESC)`

#### Scenario: Auxiliary indexes
- **WHEN** transactions are queried by recurring group, import batch, or tags
- **THEN** `idx_txn_recurring_group` SHALL index `(recurring_group_id)` with partial condition `WHERE recurring_group_id IS NOT NULL`
- **AND** `idx_txn_import_batch` SHALL index the legacy `(import_batch_id)` column with partial condition `WHERE import_batch_id IS NOT NULL` (the column is no longer populated by the importer, which carries the correlator in `metadata` instead)
- **AND** `idx_txn_tags_gin` SHALL use a GIN index on `(tags)` for array containment queries
- **AND** `idx_txn_metadata_gin` SHALL use a GIN index on `(metadata)` for extensible JSONB queries

### Requirement: Tiered deduplication indexes
The `finance.transactions` table SHALL enforce idempotent ingestion via three tiered UNIQUE partial indexes.

#### Scenario: Bank external ID deduplication (Priority 1)
- **WHEN** a transaction has both `account_id` and `external_id`
- **THEN** `uq_txn_external_id_account` SHALL enforce `UNIQUE (account_id, external_id)` with partial condition `WHERE external_id IS NOT NULL`
- **AND** a duplicate insert SHALL be rejected or detected by the application layer

#### Scenario: Source message deduplication (Priority 2)
- **WHEN** a transaction has a `source_message_id`
- **THEN** `uq_txn_source_dedupe` SHALL enforce `UNIQUE (source_message_id, merchant, amount, posted_at)` with partial condition `WHERE source_message_id IS NOT NULL`

#### Scenario: Composite fallback deduplication (Priority 3)
- **WHEN** a transaction has neither `external_id` nor `source_message_id`
- **THEN** `uq_txn_composite_dedupe` SHALL enforce `UNIQUE (account_id, posted_at, amount, merchant)` with partial condition `WHERE external_id IS NULL AND source_message_id IS NULL`
