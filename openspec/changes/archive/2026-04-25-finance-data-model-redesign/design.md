## Context

The Finance butler currently operates with dual storage: a dedicated `finance.transactions` table (created by migration `finance_001`) with typed columns and B-tree indexes, and the SPO fact layer (`public.facts`) where transaction data is packed into JSONB `metadata`. The CRUD-to-SPO migration (`openspec/changes/crud-to-spo-migration/`) routed all transaction tools through the fact layer, sidelining the dedicated table.

The finance-intelligence change (`openspec/changes/finance-intelligence/`) plans analytics features (anomaly detection, trend analysis, budget enforcement, forecasting) that require SQL aggregations with window functions (`STDDEV`, `PERCENTILE_CONT`, `LAG`), range scans, and per-merchant/per-category grouping. Running these over JSONB metadata extraction (`metadata->>'amount')::numeric`) defeats indexing and forces sequential scans at scale.

The dedicated `finance.transactions` table already has proper typed columns and indexes but needs enhancement: 16 new columns for intelligence features, 8 supporting tables, comprehensive deduplication indexes, and a migration path from SPO-primary to dedicated-table-primary.

Current state of `finance.transactions` (from `finance_001`): `id`, `account_id`, `source_message_id`, `posted_at`, `merchant`, `description`, `amount`, `currency`, `direction`, `category`, `payment_method`, `receipt_url`, `external_ref`, `metadata`, `created_at`, `updated_at`. Indexes on `posted_at`, `merchant`, `category`, `account_id`, `source_message_id`, `metadata` (GIN), plus a dedup partial index on `(source_message_id, merchant, amount, posted_at)`.

## Goals / Non-Goals

**Goals:**
- Promote `finance.transactions` as the primary query target for all transactional data access, including intelligence analytics
- Add columns needed for intelligence features (categorization tracking, recurring detection, duplicate handling, import provenance, soft-delete, versioning)
- Create supporting tables that intelligence tools need (budgets, merchant_mappings, recurring_groups, etc.)
- Provide a non-breaking migration path that preserves existing SPO data and maintains backward compatibility during transition
- Implement idempotent deduplication that handles multiple import sources (bank APIs, email extraction, CSV import)
- Establish an audit trail for all financial data mutations

**Non-Goals:**
- Multi-currency normalization or exchange rate conversion
- Table partitioning (defer until query latency measurably degrades at 200k+ rows)
- Real-time streaming analytics or trigger-based aggregation
- Changes to the SPO fact layer schema or `public.facts` table structure
- Double-entry accounting or formal bookkeeping compliance
- Changes to the subscription or bill tables (they remain as-is)

## Decisions

### 1. Dedicated table as primary, SPO as secondary mirror

All intelligence and CRUD queries target `finance.transactions`. The SPO fact layer receives a fire-and-forget mirror write for memory/recall compatibility -- the LLM runtime can still reference transaction facts during conversations via `memory_recall`. During Phase 3 (dual-write), both stores receive writes. After Phase 4, SPO transaction writes stop but existing facts remain read-only.

**Rationale**: The dedicated table has typed columns with B-tree indexes that support range queries, aggregations, and window functions. The SPO table requires JSONB extraction and casting at query time, which defeats indexing. Expression indexes on JSONB are a partial mitigation but do not provide type safety, require multiple indexes per extracted field, and add contention to a shared table used by all butlers.

**Alternative considered**: Add expression indexes to `public.facts` for `metadata->>'amount'`, `metadata->>'merchant'`, `metadata->>'category'`. Rejected because: (a) no type safety -- malformed values corrupt the index, (b) 7+ expression indexes needed on a shared table, (c) every query still pays JSONB extraction cost, (d) `public.facts` serves all butlers so heavy analytical queries affect non-finance consumers.

### 2. Merchant mappings as a dedicated lookup table, not SPO facts

The finance-intelligence design doc (Decision #2) chose SPO facts for merchant-to-category mappings. This change overrides that decision: `finance.merchant_mappings` is a dedicated table with `UNIQUE INDEX ON lower(raw_pattern) WHERE is_active = true`.

**Rationale**: Merchant mapping lookups are high-frequency (called on every transaction import for auto-categorization) and pattern-matching intensive (`ILIKE`). A dedicated table with a unique index on the normalized pattern is dramatically faster than scanning facts with `predicate='merchant_category_mapping'` and extracting patterns from metadata. The SPO fact layer is better suited for learned knowledge the LLM reasons about; merchant mappings are a lookup table the code queries programmatically.

### 3. Budget targets as a dedicated table, not SPO property facts

The finance-intelligence design doc (Decision #5) chose SPO property facts for budget targets. This change overrides that: `finance.budgets` is a dedicated table with `UNIQUE INDEX ON (category, period) WHERE is_active = true`.

**Rationale**: Budget status checks require joining actual spending against budget targets. With a dedicated table, this is a simple `JOIN` on `category`. With property facts, the query must extract `metadata->>'category'`, `metadata->>'amount'`, and `metadata->>'period'` from JSONB, making the join predicate non-indexable. Budget enforcement is a hot path (checked weekly, and post-insert when implemented).

### 4. Tiered deduplication via UNIQUE partial indexes

Three dedup keys in priority order, each enforced by a `UNIQUE` partial index:

| Priority | Key | Partial index condition | Source |
|----------|-----|-------------------------|--------|
| 1 | `(account_id, external_id)` | `WHERE external_id IS NOT NULL` | Bank APIs with stable IDs |
| 2 | `(source_message_id, merchant, amount, posted_at)` | `WHERE source_message_id IS NOT NULL` | Email-extracted transactions |
| 3 | `(account_id, posted_at, amount, merchant)` | `WHERE external_id IS NULL AND source_message_id IS NULL` | CSV imports without stable IDs |

**Rationale**: Different import sources provide different levels of dedup key quality. Bank APIs provide stable transaction IDs. Email extraction provides `source_message_id`. CSV imports from banks without stable IDs fall back to a composite key. The application layer checks in priority order and the database enforces uniqueness at each level. This replaces the single `sha256` composite hash approach from the current spec, which is harder to debug and does not support per-level fallback.

**Alternative considered**: Single composite hash (`sha256(posted_at|amount|merchant|account_id)`) stored in a `dedup_key` column. Rejected because: (a) opaque -- debugging dedup failures requires recomputing the hash, (b) does not distinguish between dedup key quality levels, (c) does not support the `(account_id, external_id)` fast path for banks that provide stable IDs.

### 5. Soft delete with `deleted_at` instead of hard delete

Financial data is never hard-deleted. `UPDATE SET deleted_at = now()` marks records as retired. All queries include `WHERE deleted_at IS NULL` (enforced by a partial index `idx_txn_active`).

**Rationale**: Financial records may be needed for tax compliance, dispute resolution, or audit. Hard deletion is irreversible. The `deleted_at` column plus a partial index keeps active-row queries efficient while preserving the full history.

### 6. Materialized spending_summaries view for dashboard performance

A materialized view `finance.spending_summaries` pre-aggregates monthly spending by account, category, direction, and currency. Refreshed via `REFRESH MATERIALIZED VIEW CONCURRENTLY` after bulk imports and on the daily anomaly digest schedule.

**Rationale**: Dashboard and trend queries repeatedly compute the same monthly aggregations. Pre-computing them avoids full-table scans for the most common query pattern. `CONCURRENTLY` refresh does not block reads. Estimated refresh time for 50k rows: under 1 second.

**Alternative considered**: Incrementally-updated summary table maintained by triggers. Rejected for v1 -- trigger-based maintenance adds schema complexity and the query volume does not justify it under 100k rows.

### 7. Optimistic locking via `version` column

Updates to `finance.transactions` include `WHERE version = $expected_version` and increment `version` on success. Version mismatch raises a conflict error.

**Rationale**: Concurrent edits to the same transaction (e.g., user manually recategorizes while a rule runs) must be detected. Optimistic locking is lightweight and does not require row-level locks for reads.

### 8. Category source tracking with lock-on-manual-override

The `category_source` column tracks how a transaction was categorized (`auto`, `manual`, `rule`, `import`). When a user manually overrides a category, `is_category_locked = true` is set, preventing automatic re-categorization by merchant mapping rules or ML.

**Rationale**: Users who manually correct a categorization expect it to stick. Without this flag, a bulk recategorize or merchant mapping update could silently undo their correction. The lock flag provides a clear contract: manual overrides are preserved.

## Risks / Trade-offs

- **[Dual-write consistency]** During Phase 3, writes go to both `finance.transactions` and `public.facts`. If one write succeeds and the other fails, the stores diverge. Mitigation: the dedicated table is the source of truth; SPO writes are fire-and-forget. If SPO write fails, log the error but do not roll back the dedicated table write. The backfill query can always re-sync from dedicated to SPO.

- **[Backfill data quality]** SPO facts may contain malformed JSONB metadata (amounts as non-numeric strings, missing required fields). Mitigation: the backfill query uses `COALESCE` and defensive casts. Rows that fail casting are logged and skipped, not treated as hard errors. A post-backfill report shows skipped row count and reasons.

- **[Index maintenance cost]** 18 indexes on the transactions table adds write overhead. Mitigation: most are partial indexes that only index a subset of rows (e.g., `WHERE deleted_at IS NULL`, `WHERE external_id IS NOT NULL`). For the expected insert volume (1-50 transactions/day, 500 per bulk import), the write overhead is negligible.

- **[Materialized view staleness]** The `spending_summaries` view is only as fresh as its last refresh. Between refresh cycles, new transactions are not reflected. Mitigation: refresh is triggered after bulk imports and on the daily schedule. For real-time queries, tools fall back to querying `finance.transactions` directly. The materialized view is an optimization, not the source of truth.

- **[Migration rollback]** Phase 1 (schema enhancement) is additive and non-breaking -- new columns have defaults, new tables are independent. Rollback is a `DROP TABLE` / `ALTER TABLE DROP COLUMN` migration. Phase 2 (backfill) is a one-time INSERT that can be rolled back by deleting rows with `source = 'bulk'`. Phase 3 (dual-write) can be reverted by routing tools back to SPO only. Phase 4 (deprecation) is the point of no return for SPO writes.

- **[Backward compatibility of tool surface]** The finance-intelligence change references SPO-based tool names and query patterns. This change must be merged and stable before finance-intelligence implementation begins. During dual-write, existing tool signatures remain unchanged -- only the internal query target changes.
