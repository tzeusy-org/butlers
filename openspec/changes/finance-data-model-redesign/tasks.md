## 1. Alembic Migration -- Schema Enhancement (Phase 1)

- [ ] 1.1 Create migration file `roster/finance/migrations/versions/002_intelligence_tables.py` with `revision = "finance_002"`, `down_revision = "finance_001"`
- [ ] 1.2 Add new columns to `finance.transactions` via `ALTER TABLE ADD COLUMN IF NOT EXISTS`: `external_id`, `transaction_date`, `normalized_description`, `normalized_merchant`, `subcategory`, `tags TEXT[]`, `category_source`, `is_category_locked`, `type`, `is_recurring`, `recurring_group_id`, `is_duplicate`, `duplicate_of`, `import_batch_id`, `source`, `raw_data`, `notes`, `deleted_at`, `version`
- [ ] 1.3 Add new columns to `finance.accounts`: `is_active BOOLEAN DEFAULT true`, `last_synced_at TIMESTAMPTZ`, `updated_at TIMESTAMPTZ DEFAULT now()`, expand `type` CHECK to include `'loan'` and `'other'`
- [ ] 1.4 Create `finance.categories` table with default category seeding (idempotent `ON CONFLICT DO NOTHING`)
- [ ] 1.5 Create `finance.merchant_mappings` table with `uq_merchant_mapping_pattern` unique index on `lower(raw_pattern)`
- [ ] 1.6 Create `finance.recurring_groups` table with FK to `finance.subscriptions`
- [ ] 1.7 Create `finance.import_batches` table with status tracking columns
- [ ] 1.8 Create `finance.balance_snapshots` table with `uq_balance_snapshot_account_date` unique index
- [ ] 1.9 Create `finance.budgets` table with `uq_budget_category_period` unique index
- [ ] 1.10 Create `finance.transaction_corrections` table with indexes on `transaction_id` and `created_at`
- [ ] 1.11 Create all new indexes on `finance.transactions`: `idx_txn_posted_at`, `idx_txn_transaction_date`, `idx_txn_category_posted`, `idx_txn_normalized_merchant`, `idx_txn_direction_posted`, `idx_txn_amount`, `idx_txn_active`, `idx_txn_recurring_group`, `idx_txn_import_batch`, `idx_txn_tags_gin`, `idx_txn_debit_category_posted`
- [ ] 1.12 Create tiered deduplication UNIQUE partial indexes: `uq_txn_external_id_account`, `uq_txn_source_dedupe` (replace existing), `uq_txn_composite_dedupe`
- [ ] 1.13 Create `finance.spending_summaries` materialized view with unique index for concurrent refresh
- [ ] 1.14 Add FKs on `finance.transactions`: `recurring_group_id` -> `recurring_groups(id)`, `duplicate_of` -> `transactions(id)`, `import_batch_id` -> `import_batches(id)`
- [ ] 1.15 Implement `downgrade()` function that drops all new objects in reverse dependency order

## 2. Migration Tests

- [ ] 2.1 Write test for `upgrade()`: verify all new columns exist on `finance.transactions` with correct defaults
- [ ] 2.2 Write test for `upgrade()`: verify all 8 new tables are created with correct columns and constraints
- [ ] 2.3 Write test for `upgrade()`: verify all new indexes exist (including partial index conditions)
- [ ] 2.4 Write test for `upgrade()`: verify default categories are seeded and seeding is idempotent
- [ ] 2.5 Write test for `upgrade()`: verify materialized view `spending_summaries` is created and refreshable
- [ ] 2.6 Write test for `downgrade()`: verify clean rollback to `finance_001` state
- [ ] 2.7 Write test for tiered dedup indexes: verify each UNIQUE constraint rejects duplicates and allows non-duplicates

## 3. Deduplication Logic

- [ ] 3.1 Implement `_deduplicate(pool, txn)` function in `roster/finance/tools/` -- checks tiered dedup keys in priority order, returns existing transaction ID or None
- [ ] 3.2 Write tests for dedup: Priority 1 match (external_id + account_id), Priority 2 match (source_message_id), Priority 3 fallback (composite), no match (new transaction)
- [ ] 3.3 Write tests for dedup edge cases: NULL fields at each priority level, partial key availability

## 4. Transaction CRUD Operations

- [ ] 4.1 Implement `record_transaction()` targeting `finance.transactions` -- dedup check, merchant mapping lookup, INSERT, post-insert SPO mirror (fire-and-forget)
- [ ] 4.2 Implement `list_transactions()` querying `finance.transactions WHERE deleted_at IS NULL` with filter parameters: date range, category, merchant ILIKE, account_id, direction, amount min/max, tags containment, LIMIT/OFFSET
- [ ] 4.3 Implement `spending_summary()` aggregating from `finance.transactions` with typed column grouping (category, merchant, week, month)
- [ ] 4.4 Implement `update_transaction()` with optimistic locking (`WHERE version = $expected`), correction logging to `transaction_corrections`, and category lock on manual override
- [ ] 4.5 Implement `delete_transaction()` as soft delete (`UPDATE SET deleted_at = now()`)
- [ ] 4.6 Implement `merge_duplicates(keep_id, duplicate_ids)` -- mark duplicates, soft-delete, record corrections
- [ ] 4.7 Implement `split_transaction(txn_id, parts)` -- validate sum, soft-delete original, create children with `metadata.split_from`
- [ ] 4.8 Implement `bulk_recategorize(merchant_pattern, new_category, create_rule)` -- update unlocked transactions, record corrections, optionally upsert merchant mapping

## 5. Bulk Import Pipeline

- [ ] 5.1 Implement `import_transactions(file_path, account_id, currency, column_map, dry_run)` -- create import batch, detect format, parse CSV, normalize, process in batches of 500
- [ ] 5.2 Implement merchant mapping auto-apply during import -- look up each merchant in `merchant_mappings`, auto-categorize if match found
- [ ] 5.3 Implement post-import triggers: refresh `spending_summaries` materialized view, trigger `compute_baselines()` if 50+ imported
- [ ] 5.4 Implement dry run mode -- parse, validate, detect duplicates, return preview of first 10 transactions without inserting
- [ ] 5.5 Write tests for bulk import: format detection, normalization, dedup, batch processing, dry run, post-import triggers

## 6. SPO Mirror and Backward Compatibility

- [ ] 6.1 Implement fire-and-forget SPO mirror write in `record_transaction()` -- write to `shared.facts` with `predicate='transaction_{direction}'` after primary insert succeeds
- [ ] 6.2 Ensure `bulk_record_transactions()` routes through the new `record_transaction()` for per-row dedup and mirror
- [ ] 6.3 Verify `spending_summary` response shape matches the current SPO-based implementation (backward compatibility)
- [ ] 6.4 Write tests for SPO mirror: verify fact is created, verify primary insert is not rolled back on mirror failure

## 7. Backfill Script (Phase 2)

- [ ] 7.1 Implement backfill script/function to INSERT existing SPO transaction facts into `finance.transactions` with defensive JSONB extraction and dedup via `NOT EXISTS`
- [ ] 7.2 Implement backfill error reporting: log skipped rows with reasons (malformed amounts, missing required fields)
- [ ] 7.3 Write test for backfill: verify correct extraction from JSONB metadata, verify dedup against existing rows, verify skipped row logging

## 8. Tool Registration and Wiring

- [ ] 8.1 Register new tool functions (`update_transaction`, `delete_transaction`, `merge_duplicates`, `split_transaction`, `bulk_recategorize`, `import_transactions`) in the finance module's `register_tools()` method
- [ ] 8.2 Update `roster/finance/tools/__init__.py` to export all new tool functions
- [ ] 8.3 Update existing tool registrations (`record_transaction`, `list_transactions`, `spending_summary`, `bulk_record_transactions`) to use dedicated table implementations

## 9. Validation

- [ ] 9.1 Run lint: `uv run ruff check src/ tests/ roster/ --output-format concise`
- [ ] 9.2 Run format check: `uv run ruff format --check src/ tests/ roster/ -q`
- [ ] 9.3 Run full test suite: `uv run pytest tests/ -q --tb=short`
- [ ] 9.4 Verify soft delete: confirm no `DELETE FROM finance.transactions` appears anywhere in the codebase
- [ ] 9.5 Verify backward compatibility: `spending_summary` and `list_transactions` return identical response shapes to the current implementation
