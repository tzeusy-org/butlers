# Finance CRUD Operations

## ADDED Requirements

### Requirement: Single transaction creation with auto-categorization
Creating a single transaction SHALL check for duplicates, apply merchant mapping, and record the transaction with post-insert hooks.

#### Scenario: Deduplication check on create
- **WHEN** `record_transaction` is called
- **THEN** it SHALL check for an existing duplicate using the tiered dedup key hierarchy: (1) `(account_id, external_id)`, (2) `(source_message_id, merchant, amount, posted_at)`, (3) `(account_id, posted_at, amount, merchant)` as fallback
- **AND** if a duplicate is found, the existing transaction ID SHALL be returned without creating a new row

#### Scenario: Auto-categorization via merchant mapping
- **WHEN** a transaction is created without an explicit category (or with category `'uncategorized'`)
- **THEN** the system SHALL look up the merchant in `finance.merchant_mappings` using `ILIKE` pattern matching
- **AND** if a mapping is found, the category SHALL be set from the mapping with `category_source = 'auto'`
- **AND** if no mapping is found, the category SHALL remain `'uncategorized'`

#### Scenario: Post-insert SPO mirror write
- **WHEN** a transaction is successfully inserted into `finance.transactions`
- **THEN** a background task SHALL mirror the transaction to `public.facts` with the appropriate predicate (`'transaction_debit'` or `'transaction_credit'`)
- **AND** the mirror write SHALL be fire-and-forget (failure does not roll back the primary insert)

#### Scenario: Inline bill reconciliation hook on debit insert
- **WHEN** a fresh debit transaction is inserted, the system SHALL call `match_transaction_to_bills()` (Track C inline matcher in `roster/finance/tools/reconciliation.py`) against open bills
- **THEN** if the match tier is `'auto_settle'` (single in-window candidate with an exact payee match), the system SHALL call `_settle_bill()` and include `bill_reconciliation.auto_settled` (with `bill_id`, `payee`, `amount`, `paid_at`, `txn_id`) in the `record_transaction` response
- **AND** if the match tier is `'confirm'` (multiple candidates), the response SHALL include `bill_reconciliation.candidates` (a list of `{bill_id, payee, due_date, amount}`) for user confirmation
- **AND** the hook SHALL be best-effort: any reconciliation failure is logged but never rolls back or fails the primary insert
- **AND** the batch counterpart is the `reconcile_bills()` MCP sweep tool, used as a backstop by the weekly `upcoming-bills-check` task

### Requirement: Bulk transaction import with batch correlation
Bulk imports SHALL generate an ephemeral `import_batch_id` correlator, process rows in batches, stamp the correlator onto each inserted transaction, and trigger post-import analytics. There is no persisted import-batch record; the correlator and result counts exist only in memory and in the returned result.

#### Scenario: Correlator generation and processing
- **WHEN** `import_transactions` is called with a file path and account ID
- **THEN** it SHALL generate a single in-memory `import_batch_id` (a UUIDv4 string) for the run
- **AND** it SHALL detect the CSV format, parse rows, normalize dates/amounts/merchant names
- **AND** it SHALL process rows in batches of 500
- **AND** for each row, it SHALL run dedup check, apply merchant mapping, and INSERT
- **AND** each inserted transaction's `metadata` JSONB SHALL carry the run's `import_batch_id`

#### Scenario: Post-import analytics triggers
- **WHEN** a bulk import completes with 50 or more imported transactions
- **THEN** it SHALL trigger `compute_baselines()` to update statistical baselines
- **AND** it SHALL trigger `REFRESH MATERIALIZED VIEW CONCURRENTLY finance.spending_summaries`

#### Scenario: Import result reporting
- **WHEN** a bulk import finishes
- **THEN** it SHALL return a result reporting `total`, `imported`, `skipped`, `errors`, `import_batch_id`, and `detected_format`
- **AND** the result SHALL include `import_batch_id` even when the import fails or encounters per-row errors
- **AND** these counts SHALL NOT be persisted to a dedicated batch table

### Requirement: Transaction read with filters and aggregation
Reading transactions SHALL support filtering by date range, category, merchant, account, amount bounds, and direction, with soft-delete exclusion.

#### Scenario: Filtered transaction listing
- **WHEN** `list_transactions` is called with optional filters
- **THEN** it SHALL query `finance.transactions WHERE deleted_at IS NULL`
- **AND** it SHALL support filters: `posted_at` date range, `category`, `merchant` (ILIKE), `account_id`, `direction`, `amount` min/max, `tags` (array containment)
- **AND** results SHALL be ordered by `posted_at DESC` with pagination via `LIMIT`/`OFFSET`

#### Scenario: Spending aggregation
- **WHEN** `spending_summary` is called for a date range
- **THEN** it SHALL aggregate from `finance.transactions WHERE direction = 'debit' AND deleted_at IS NULL`
- **AND** it SHALL support grouping by `category`, `merchant`, `week` (`DATE_TRUNC('week', posted_at)`), or `month` (`DATE_TRUNC('month', posted_at)`)
- **AND** the response shape SHALL be identical to the current SPO-based implementation

### Requirement: Transaction update with audit trail
Updating a transaction SHALL record all field changes in the corrections table and enforce optimistic locking.

#### Scenario: Field modification with correction logging
- **WHEN** a transaction field is updated
- **THEN** the system SHALL fetch the current row
- **AND** for each changed field, it SHALL INSERT a row into `finance.transaction_corrections` with the old value, new value, reason, and source
- **AND** the UPDATE query SHALL include `WHERE id = $1 AND version = $expected_version`
- **AND** the `version` column SHALL be incremented
- **AND** `updated_at` SHALL be set to `now()`

#### Scenario: Optimistic lock conflict
- **WHEN** an update is attempted with a stale version number
- **THEN** the system SHALL raise a conflict error indicating the row was modified concurrently
- **AND** it SHALL NOT apply the update

#### Scenario: Category override locks categorization
- **WHEN** a user manually changes a transaction's category
- **THEN** `category_source` SHALL be set to `'manual'`
- **AND** `is_category_locked` SHALL be set to `true`
- **AND** future automatic re-categorization SHALL skip this transaction

### Requirement: Soft delete only for financial data
Financial transactions SHALL only be soft-deleted, never hard-deleted.

#### Scenario: Soft delete execution
- **WHEN** a transaction is deleted
- **THEN** the system SHALL execute `UPDATE SET deleted_at = now(), updated_at = now(), version = version + 1 WHERE id = $1 AND deleted_at IS NULL`
- **AND** it SHALL return the soft-deleted row
- **AND** it SHALL NOT execute `DELETE FROM` on the transactions table

### Requirement: Duplicate merge operation
Merging duplicate transactions SHALL preserve the canonical transaction and soft-delete duplicates with an audit trail.

#### Scenario: Merge execution
- **WHEN** `merge_duplicates` is called with a `keep_id` and a list of `duplicate_ids`
- **THEN** each duplicate SHALL be marked with `is_duplicate = true` and `duplicate_of = keep_id`
- **AND** each duplicate SHALL be soft-deleted (`deleted_at = now()`)
- **AND** corrections SHALL be recorded for the audit trail
- **AND** the canonical transaction SHALL be returned

### Requirement: Transaction split operation
Splitting a transaction SHALL create child transactions and soft-delete the original.

#### Scenario: Split execution
- **WHEN** `split_transaction` is called with a transaction ID and a list of parts (each with `amount` and `category`)
- **THEN** the sum of parts' amounts SHALL equal the original transaction's amount (validation)
- **AND** the original transaction SHALL be soft-deleted
- **AND** new transactions SHALL be created for each part, inheriting the original's non-split fields (merchant, posted_at, account_id, etc.)
- **AND** each child transaction SHALL have `metadata.split_from` set to the original transaction's ID
- **AND** corrections SHALL be recorded for the audit trail

### Requirement: Bulk recategorization
Bulk recategorization SHALL update all matching transactions that are not category-locked.

#### Scenario: Bulk recategorize execution
- **WHEN** `bulk_recategorize` is called with a `merchant_pattern` and `new_category`
- **THEN** it SHALL execute `UPDATE SET category = $1 WHERE merchant ILIKE $2 AND is_category_locked = false AND deleted_at IS NULL`
- **AND** corrections SHALL be recorded for each updated row
- **AND** the count of updated transactions SHALL be returned

#### Scenario: Merchant mapping creation on recategorize
- **WHEN** `bulk_recategorize` is called with `create_rule = true`
- **THEN** it SHALL upsert a `finance.merchant_mappings` row with the merchant pattern and new category
