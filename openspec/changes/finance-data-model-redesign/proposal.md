## Why

The Finance butler stores transactions in two parallel systems: a dedicated `finance.transactions` table (migration `finance_001`) and the SPO fact layer (`shared.facts` with `predicate IN ('transaction_debit', 'transaction_credit')`). The upcoming finance-intelligence change commits to running all analytics (anomaly detection, trend analysis, budget enforcement, forecasting) as SQL aggregations over `shared.facts`, extracting amounts, merchants, and categories from JSONB `metadata` at query time. This defeats B-tree indexing, forces sequential scans with `::numeric` casts on every row, and will not scale at 50k+ transactions. The dedicated table already exists with proper typed columns and indexes but is sidelined. This change promotes it to the primary store and extends it with the columns and supporting tables the intelligence features require. It is a prerequisite for the finance-intelligence change.

## What Changes

- **Promote `finance.transactions` as the primary transactional data store** -- all intelligence queries target the dedicated table; SPO facts become a secondary mirror for memory/recall only.
- **Add 16 new columns to `finance.transactions`** -- `external_id`, `transaction_date`, `normalized_description`, `normalized_merchant`, `subcategory`, `tags TEXT[]`, `category_source`, `is_category_locked`, `type`, `is_recurring`, `recurring_group_id`, `is_duplicate`, `duplicate_of`, `import_batch_id`, `source`, `raw_data JSONB`, `notes`, `deleted_at`, `version`.
- **Enhance `finance.accounts`** -- add `is_active`, `last_synced_at`, expand `type` CHECK to include `'loan'` and `'other'`.
- **Add 8 new supporting tables**: `categories`, `merchant_mappings`, `recurring_groups`, `import_batches`, `balance_snapshots`, `budgets`, `transaction_corrections`, and a materialized `spending_summaries` view.
- **Implement tiered deduplication strategy** -- three UNIQUE partial indexes: `(account_id, external_id)`, `(source_message_id, merchant, amount, posted_at)`, `(account_id, posted_at, amount, merchant)` as fallback.
- **Comprehensive indexing** -- 18 indexes covering date-range scans, category spending, merchant lookup, amount range, recurring group, import batch, soft-delete filtering, tags (GIN), and a composite partial index for the debit-by-category hot path.
- **Four-phase migration path**: schema enhance (non-breaking) -> backfill from SPO facts -> dual-write transition -> deprecate SPO transaction writes.
- **Soft delete only** -- financial data is never hard-deleted; `deleted_at IS NOT NULL` marks retired records.
- **Audit trail** -- `transaction_corrections` table records every field change with old/new values, reason, and source.
- **BREAKING**: Intelligence queries (`spending_summary`, `list_transactions`, anomaly detection, trends, budgets) will read from `finance.transactions` instead of `shared.facts`. During dual-write (Phase 3), both stores are kept in sync. After Phase 4, SPO transaction writes stop.

## Capabilities

### New Capabilities
- `finance-transaction-schema`: Enhanced transaction table schema with 16 new columns, comprehensive indexing, tiered deduplication, soft-delete lifecycle, and optimistic locking.
- `finance-supporting-tables`: Eight new supporting tables -- categories (hierarchical taxonomy with tax-relevance), merchant_mappings (learned pattern-to-category lookup), recurring_groups (detected subscription patterns), import_batches (audit trail for data imports), balance_snapshots (net worth tracking), budgets (category-level budget targets), transaction_corrections (edit audit trail), and spending_summaries materialized view.
- `finance-data-migration`: Four-phase migration from SPO-primary to dedicated-table-primary: schema enhancement, SPO backfill, dual-write, SPO deprecation.
- `finance-crud-operations`: CRUD operations on the dedicated table -- create (single + bulk with dedup), read (filtered + aggregated), update (with audit trail and optimistic locking), soft-delete, merge duplicates, split transactions, bulk recategorize.

### Modified Capabilities
- `butler-finance`: Transaction tools (`record_transaction`, `list_transactions`, `spending_summary`, `bulk_record_transactions`) change their primary data source from `shared.facts` to `finance.transactions`. Deduplication strategy shifts from SPO-layer metadata matching to the tiered UNIQUE index approach on the dedicated table. The CRUD-to-SPO scenario requirements are superseded for transaction data (SPO becomes secondary mirror).

## Impact

- **Database**: New Alembic migration `finance_002` in `roster/finance/migrations/versions/` adding columns, tables, indexes, and materialized view. All within `finance` schema. No changes to `shared` schema.
- **Tools**: `record_transaction`, `list_transactions`, `spending_summary`, `bulk_record_transactions` change their query target from `shared.facts` to `finance.transactions`. New internal functions for dedup, merchant mapping lookup, correction recording, and materialized view refresh.
- **Migration data**: One-time backfill of existing SPO transaction facts into the dedicated table with dedup-safe INSERT. Existing SPO facts are preserved read-only.
- **Dependencies**: No new external dependencies. This change is a prerequisite for `finance-intelligence` -- the intelligence tools need typed columns, B-tree indexes, and the supporting tables (budgets, merchant_mappings, recurring_groups, etc.) that this change provides.
- **Backward compatibility**: During dual-write (Phase 3), both stores are kept in sync. Memory/recall tools continue reading SPO facts. After Phase 4, the SPO transaction write path is removed but existing facts remain for historical recall.
