# RFC 0012: Finance Transaction Data Model

**Status:** Draft
**Date:** 2026-03-25

## Summary

The finance butler stores transactions in a dedicated `finance.transactions` table with typed columns, B-tree indexes, and tiered deduplication -- not in the SPO fact layer. The SPO fact layer (`shared.facts`) receives a fire-and-forget mirror write for memory/recall compatibility but is never the primary query target for financial analytics. Eight supporting tables (`accounts`, `categories`, `merchant_mappings`, `recurring_groups`, `import_batches`, `balance_snapshots`, `budgets`, `transaction_corrections`) and a materialized `spending_summaries` view provide the infrastructure for intelligence features. A 4-phase migration path transitions from SPO-primary to dedicated-table-primary storage without data loss.

## Motivation

The finance butler currently stores transactional data in two parallel systems: a dedicated `finance.transactions` table (migration `finance_001`) with typed columns and proper indexes, and the SPO fact layer (`shared.facts`) where all financial fields are packed into a JSONB `metadata` column. The `CRUD-to-SPO` migration routed intelligence tools to query the fact layer, sidelining the dedicated table.

This creates a fundamental scaling problem. The intelligence features -- anomaly detection, trend analysis, budget enforcement, forecasting -- require SQL aggregations with window functions (`STDDEV`, `PERCENTILE_CONT`, `LAG`), range scans, per-merchant/per-category grouping, and month-over-month comparisons across full transaction history. Running these over JSONB metadata extraction (`(metadata->>'amount')::numeric`) defeats B-tree indexing, forces sequential scans with casts on every row, and breaks `NUMERIC` type safety.

For a user with 50,000 transactions (roughly 5 years of history), a single `spending_trends(comparison="mom", months=6)` call must extract `metadata->>'amount'`, cast to `NUMERIC`, extract `metadata->>'category'` for grouping, and apply `DATE_TRUNC`/`SUM`/`LAG` window functions -- all without index support. The dedicated table already has typed columns and B-tree indexes that make these queries efficient. It needs targeted enhancements, not replacement.

### Why Not Expression Indexes on Facts?

Expression indexes on JSONB (e.g., `CREATE INDEX ON facts ((metadata->>'amount')::numeric)`) are an incomplete mitigation:

1. **No type safety.** Expression indexes do not enforce type constraints. A malformed `metadata->>'amount'` value (e.g., `"N/A"`) corrupts the index or silently produces NULLs.
2. **Multiple indexes needed.** Each extracted field needs its own expression index. The intelligence spec requires indexes on `merchant`, `category`, `amount::numeric`, `direction`, `account_id`, `normalized_merchant`, and `inferred_category` -- seven or more expression indexes on a single shared table.
3. **Shared table contention.** The `facts` table serves all butlers (health, relationship, finance). Finance-specific expression indexes and heavy analytical queries affect all fact consumers.
4. **Query complexity persists.** Every query still pays JSONB extraction cost. Compare:

```sql
-- SPO facts: every query pays extraction cost
SELECT DATE_TRUNC('month', valid_at), SUM((metadata->>'amount')::numeric)
FROM facts
WHERE predicate = 'transaction_debit' AND validity = 'active' AND scope = 'finance'
  AND valid_at >= $1 AND valid_at <= $2 AND metadata->>'category' = $3
GROUP BY 1;

-- Dedicated table: clean, indexable, type-safe
SELECT DATE_TRUNC('month', posted_at), SUM(amount)
FROM finance.transactions
WHERE direction = 'debit' AND posted_at >= $1 AND posted_at <= $2 AND category = $3
GROUP BY 1;
```

## Design

### Schema Topology

All tables reside in the `finance` schema, following RFC 0006's per-butler schema isolation model. No changes to the `shared` schema.

```
finance schema
  |
  +-- transactions          (enhanced: 16 new columns, 18 indexes)
  +-- accounts              (enhanced: is_active, last_synced_at, expanded type CHECK)
  +-- categories            (new: hierarchical taxonomy with tax-relevance)
  +-- merchant_mappings     (new: learned pattern-to-category lookup)
  +-- recurring_groups      (new: detected subscription patterns)
  +-- import_batches        (new: import audit trail)
  +-- balance_snapshots     (new: net worth tracking)
  +-- budgets               (new: category-level budget targets)
  +-- transaction_corrections (new: edit audit trail)
  +-- spending_summaries    (new: materialized view, monthly aggregates)
  +-- subscriptions         (existing, unchanged)
  +-- bills                 (existing, unchanged)
```

### Design Principles

1. **NUMERIC, not float.** All monetary values use `NUMERIC(14,2)` to prevent floating-point precision loss.
2. **Soft delete only.** Financial data is never hard-deleted. `deleted_at IS NOT NULL` marks retired records. All normal queries include `WHERE deleted_at IS NULL`.
3. **Audit trail.** All mutations are tracked via `updated_at`, `version`, and the `transaction_corrections` table.
4. **Idempotent imports.** Composite dedup keys prevent duplicate ingestion from any source.
5. **Separation of concerns.** Raw imported data is preserved in `raw_data JSONB`; normalized fields are typed columns.

### `finance.transactions` Table

The table already exists (migration `finance_001`). Enhancement adds 16 new columns for intelligence features. All new columns have defaults; no existing columns are removed or renamed.

```sql
CREATE TABLE IF NOT EXISTS finance.transactions (
    -- Identity
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id         TEXT,                          -- Bank's transaction ID (dedup Priority 1)

    -- Account linkage
    account_id          UUID REFERENCES finance.accounts(id) ON DELETE SET NULL,

    -- Core financial data
    amount              NUMERIC(14, 2) NOT NULL,       -- Absolute value
    currency            CHAR(3) NOT NULL,              -- ISO 4217
    direction           TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),

    -- Temporal
    posted_at           TIMESTAMPTZ NOT NULL,          -- When the transaction posted
    transaction_date    DATE,                          -- Original transaction date (may differ)

    -- Description
    description         TEXT,                          -- Raw bank description
    normalized_description TEXT,                       -- Cleaned/standardized description

    -- Merchant
    merchant            TEXT NOT NULL,                 -- Raw merchant name
    normalized_merchant TEXT,                          -- Cleaned merchant name

    -- Classification
    category            TEXT NOT NULL DEFAULT 'uncategorized',
    subcategory         TEXT,
    tags                TEXT[] DEFAULT '{}',
    category_source     TEXT DEFAULT 'auto'
                        CHECK (category_source IN ('auto', 'manual', 'rule', 'import')),
    is_category_locked  BOOLEAN NOT NULL DEFAULT false,

    -- Transaction type
    type                TEXT DEFAULT 'purchase'
                        CHECK (type IN ('purchase', 'refund', 'transfer',
                                        'payment', 'fee', 'interest', 'atm',
                                        'deposit', 'other')),

    -- Recurring detection
    is_recurring        BOOLEAN NOT NULL DEFAULT false,
    recurring_group_id  UUID REFERENCES finance.recurring_groups(id) ON DELETE SET NULL,

    -- Duplicate handling
    is_duplicate        BOOLEAN NOT NULL DEFAULT false,
    duplicate_of        UUID REFERENCES finance.transactions(id) ON DELETE SET NULL,

    -- Provenance
    payment_method      TEXT,
    receipt_url         TEXT,
    external_ref        TEXT,
    source_message_id   TEXT,                          -- Email/message ID (dedup Priority 2)
    import_batch_id     UUID REFERENCES finance.import_batches(id) ON DELETE SET NULL,
    source              TEXT DEFAULT 'manual'
                        CHECK (source IN ('manual', 'email', 'csv_import', 'api', 'bulk')),
    raw_data            JSONB DEFAULT '{}'::jsonb,     -- Original import row for audit

    -- User annotations
    notes               TEXT,

    -- Metadata
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Lifecycle
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at          TIMESTAMPTZ,                   -- Soft delete
    version             INTEGER NOT NULL DEFAULT 1     -- Optimistic locking
);
```

### Indexing Strategy

Eighteen indexes cover five primary query patterns plus deduplication.

| Query Pattern | Index | Definition |
|---------------|-------|------------|
| Date range scan | `idx_txn_posted_at` | `(posted_at DESC)` |
| Date range scan | `idx_txn_transaction_date` | `(transaction_date) WHERE transaction_date IS NOT NULL` |
| Date range scan | `idx_txn_active` | `(posted_at DESC) WHERE deleted_at IS NULL` |
| Category spending | `idx_txn_category` | `(category)` |
| Category spending | `idx_txn_category_posted` | `(category, posted_at DESC)` |
| Category spending | `idx_txn_debit_category_posted` | `(category, posted_at) WHERE direction = 'debit' AND deleted_at IS NULL` |
| Merchant lookup | `idx_txn_merchant` | `(merchant)` |
| Merchant lookup | `idx_txn_normalized_merchant` | `(normalized_merchant) WHERE normalized_merchant IS NOT NULL` |
| Amount range | `idx_txn_amount` | `(amount)` |
| Account scoping | `idx_txn_account_id` | `(account_id) WHERE account_id IS NOT NULL` |
| Direction filter | `idx_txn_direction_posted` | `(direction, posted_at DESC)` |
| Recurring group | `idx_txn_recurring_group` | `(recurring_group_id) WHERE recurring_group_id IS NOT NULL` |
| Import batch | `idx_txn_import_batch` | `(import_batch_id) WHERE import_batch_id IS NOT NULL` |
| Tags | `idx_txn_tags_gin` | `GIN (tags)` |
| Metadata | `idx_txn_metadata_gin` | `GIN (metadata)` |

Most indexes are partial, indexing only the relevant subset of rows. For the expected insert volume (1-50 transactions/day, 500 per bulk import), write overhead from 18 indexes is negligible.

### Tiered Deduplication Strategy

Three UNIQUE partial indexes enforce idempotent ingestion at the database level. The application layer checks in priority order; the database enforces uniqueness at each level independently.

| Priority | Key | Partial Index Condition | Source |
|----------|-----|-------------------------|--------|
| 1 | `(account_id, external_id)` | `WHERE external_id IS NOT NULL` | Bank APIs with stable transaction IDs |
| 2 | `(source_message_id, merchant, amount, posted_at)` | `WHERE source_message_id IS NOT NULL` | Email-extracted transactions |
| 3 | `(account_id, posted_at, amount, merchant)` | `WHERE external_id IS NULL AND source_message_id IS NULL` | CSV imports without stable IDs |

The application-layer dedup function checks in priority order:

```python
async def _deduplicate(pool, txn: dict) -> UUID | None:
    """Return existing transaction ID if duplicate found, else None."""
    # Priority 1: external_id (bank APIs)
    if txn.get("external_id") and txn.get("account_id"):
        row = await pool.fetchrow(
            "SELECT id FROM transactions WHERE account_id = $1 AND external_id = $2",
            txn["account_id"], txn["external_id"]
        )
        if row: return row["id"]

    # Priority 2: source_message_id (email extraction)
    if txn.get("source_message_id"):
        row = await pool.fetchrow(
            """SELECT id FROM transactions
               WHERE source_message_id = $1 AND merchant = $2
                 AND amount = $3 AND posted_at = $4""",
            txn["source_message_id"], txn["merchant"],
            txn["amount"], txn["posted_at"]
        )
        if row: return row["id"]

    # Priority 3: composite fallback (CSV without stable IDs)
    if txn.get("account_id"):
        row = await pool.fetchrow(
            """SELECT id FROM transactions
               WHERE account_id = $1 AND posted_at = $2
                 AND amount = $3 AND merchant = $4
                 AND external_id IS NULL AND source_message_id IS NULL""",
            txn["account_id"], txn["posted_at"],
            txn["amount"], txn["merchant"]
        )
        if row: return row["id"]

    return None
```

This replaces the previous `sha256` composite hash approach, which was opaque (debugging dedup failures required recomputing the hash), did not distinguish between key quality levels, and did not support the `(account_id, external_id)` fast path for banks with stable IDs.

### Supporting Tables

#### `finance.accounts` (Enhanced)

Adds lifecycle management and sync tracking to the existing table.

| Column | Type | Purpose |
|--------|------|---------|
| `is_active` | `BOOLEAN DEFAULT true` | Deactivate closed accounts without deletion |
| `last_synced_at` | `TIMESTAMPTZ` | When data was last imported for this account |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | Last modification time |

The `type` CHECK constraint is expanded to accept `'loan'` and `'other'` in addition to `'checking'`, `'savings'`, `'credit'`, `'investment'`.

#### `finance.categories` (New)

Hierarchical category taxonomy with tax-relevance tracking. Seeded with 22 default categories at migration time via idempotent `ON CONFLICT (name) DO NOTHING`.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `UUID PK` | Primary key |
| `name` | `TEXT NOT NULL UNIQUE` | Lowercase canonical name (e.g., `'groceries'`) |
| `parent_id` | `UUID FK -> categories(id)` | Hierarchical parent |
| `display_name` | `TEXT` | Human-readable label |
| `icon` | `TEXT` | Optional emoji or icon name |
| `is_tax_relevant` | `BOOLEAN DEFAULT false` | Flag for tax categorization |
| `tax_category` | `TEXT` | Mapped tax bucket (e.g., `'medical'`, `'charitable'`) |
| `is_system` | `BOOLEAN DEFAULT false` | System-defined vs. user-created |
| `sort_order` | `INTEGER DEFAULT 0` | Display ordering |
| `metadata` | `JSONB DEFAULT '{}'` | Extensible fields |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Creation time |

Tax-relevant default categories: `education`, `medical`, `charitable`, `home_office`, `business_expense`.

#### `finance.merchant_mappings` (New)

Learned merchant-to-category lookup table. Replaces the `predicate='merchant_category_mapping'` fact storage approach.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `UUID PK` | Primary key |
| `raw_pattern` | `TEXT NOT NULL` | Merchant string pattern for ILIKE matching |
| `normalized_merchant` | `TEXT NOT NULL` | Cleaned merchant name |
| `category` | `TEXT NOT NULL` | Default category for this merchant |
| `confidence` | `FLOAT DEFAULT 1.0` | Ratio of most-frequent to total count |
| `learned_from_count` | `INTEGER DEFAULT 0` | How many transactions informed this mapping |
| `source` | `TEXT DEFAULT 'learned'` | `'learned'`, `'manual'`, or `'import'` |
| `is_active` | `BOOLEAN DEFAULT true` | Active/inactive toggle |
| `metadata` | `JSONB DEFAULT '{}'` | Extensible fields |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Creation time |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | Last modification time |

Uniqueness: `UNIQUE (lower(raw_pattern)) WHERE is_active = true`. Deactivated mappings do not conflict with new active ones.

**Rationale for dedicated table over SPO facts:** Merchant mapping lookups are high-frequency (called on every transaction import for auto-categorization) and pattern-matching intensive (`ILIKE`). A dedicated table with a unique index on the normalized pattern is dramatically faster than scanning facts with `predicate='merchant_category_mapping'` and extracting patterns from metadata. The SPO fact layer is suited for learned knowledge the LLM reasons about; merchant mappings are a lookup table the code queries programmatically.

#### `finance.recurring_groups` (New)

Detected recurring charge patterns linking multiple transactions.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `UUID PK` | Primary key |
| `merchant` | `TEXT NOT NULL` | Raw merchant name |
| `normalized_merchant` | `TEXT` | Cleaned merchant name |
| `expected_amount` | `NUMERIC(14,2) NOT NULL` | Expected charge amount |
| `amount_variance_pct` | `FLOAT DEFAULT 0.0` | Observed percentage variance |
| `currency` | `CHAR(3) DEFAULT 'USD'` | ISO 4217 |
| `frequency` | `TEXT NOT NULL` | `'weekly'`, `'monthly'`, `'quarterly'`, `'yearly'` |
| `occurrence_count` | `INTEGER DEFAULT 0` | How many charges observed |
| `last_charge_date` | `DATE` | Most recent charge |
| `next_expected_date` | `DATE` | Predicted next charge |
| `confidence` | `TEXT DEFAULT 'medium'` | `'high'`, `'medium'`, `'low'` |
| `is_subscription` | `BOOLEAN DEFAULT false` | Confirmed subscription flag |
| `subscription_id` | `UUID FK -> subscriptions(id)` | Link to tracked subscription |
| `status` | `TEXT DEFAULT 'active'` | `'active'`, `'paused'`, `'stopped'` |
| `metadata` | `JSONB DEFAULT '{}'` | Extensible fields |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Creation time |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | Last modification time |

#### `finance.import_batches` (New)

Audit trail for each data import operation.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `UUID PK` | Primary key |
| `source` | `TEXT NOT NULL` | Format identifier (e.g., `'chase_csv'`, `'amex_csv'`, `'generic_csv'`) |
| `filename` | `TEXT` | Original filename |
| `account_id` | `UUID FK -> accounts(id)` | Target account |
| `row_count` | `INTEGER DEFAULT 0` | Total rows in source |
| `imported_count` | `INTEGER DEFAULT 0` | Successfully imported |
| `skipped_count` | `INTEGER DEFAULT 0` | Duplicates skipped |
| `error_count` | `INTEGER DEFAULT 0` | Failed rows |
| `date_range_start` | `DATE` | Earliest transaction in batch |
| `date_range_end` | `DATE` | Latest transaction in batch |
| `detected_format` | `TEXT` | Auto-detected format name |
| `column_mapping` | `JSONB` | Column mapping used |
| `status` | `TEXT DEFAULT 'pending'` | `'pending'`, `'processing'`, `'completed'`, `'completed_with_errors'`, `'failed'` |
| `error_details` | `JSONB DEFAULT '[]'` | Array of `{row, reason}` objects |
| `baselines_computed` | `BOOLEAN DEFAULT false` | Whether baselines were recomputed |
| `categories_learned` | `INTEGER DEFAULT 0` | New merchant-category mappings learned |
| `metadata` | `JSONB DEFAULT '{}'` | Extensible fields |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Creation time |
| `completed_at` | `TIMESTAMPTZ` | When processing finished |

#### `finance.balance_snapshots` (New)

Periodic account balance records for net worth tracking.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `UUID PK` | Primary key |
| `account_id` | `UUID NOT NULL FK -> accounts(id) ON DELETE CASCADE` | Which account |
| `balance` | `NUMERIC(14,2) NOT NULL` | Balance (negative for credit/loan) |
| `currency` | `CHAR(3) DEFAULT 'USD'` | ISO 4217 |
| `as_of_date` | `DATE NOT NULL` | When this balance was valid |
| `source` | `TEXT DEFAULT 'manual'` | `'manual'`, `'import'`, `'statement'` |
| `metadata` | `JSONB DEFAULT '{}'` | Extensible fields |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Creation time |

Uniqueness: `UNIQUE (account_id, as_of_date)`. Duplicate snapshot for the same account and date upserts rather than creating a new row.

#### `finance.budgets` (New)

Category-level budget targets with configurable alert thresholds.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `UUID PK` | Primary key |
| `category` | `TEXT NOT NULL` | Target category |
| `amount` | `NUMERIC(14,2) NOT NULL` | Budget cap |
| `currency` | `CHAR(3) DEFAULT 'USD'` | ISO 4217 |
| `period` | `TEXT NOT NULL` | `'weekly'`, `'monthly'`, `'quarterly'`, `'annual'` |
| `warn_threshold` | `FLOAT DEFAULT 0.8` | Warn at 80% utilization |
| `alert_threshold` | `FLOAT DEFAULT 1.0` | Alert at 100% utilization |
| `is_active` | `BOOLEAN DEFAULT true` | Active toggle |
| `metadata` | `JSONB DEFAULT '{}'` | Extensible fields |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | Creation time |
| `updated_at` | `TIMESTAMPTZ DEFAULT now()` | Last modification time |

Uniqueness: `UNIQUE (category, period) WHERE is_active = true`. Deactivated budgets do not conflict with new active ones.

**Rationale for dedicated table over SPO property facts:** Budget status checks require joining actual spending against budget targets. With a dedicated table, this is a simple `JOIN` on `category`. With property facts, the query must extract `metadata->>'category'`, `metadata->>'amount'`, and `metadata->>'period'` from JSONB, making the join predicate non-indexable. Budget enforcement is a hot path (checked weekly and post-insert).

#### `finance.transaction_corrections` (New)

Audit trail for all edits to transaction data.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | `UUID PK` | Primary key |
| `transaction_id` | `UUID NOT NULL FK -> transactions(id) ON DELETE CASCADE` | Which transaction |
| `field_name` | `TEXT NOT NULL` | Which column was changed |
| `old_value` | `TEXT` | Previous value (as text) |
| `new_value` | `TEXT` | New value (as text) |
| `reason` | `TEXT` | Why the change was made |
| `source` | `TEXT DEFAULT 'user'` | `'user'`, `'rule'`, `'auto'`, `'merge'` |
| `created_at` | `TIMESTAMPTZ DEFAULT now()` | When the change happened |

Indexed on `(transaction_id)` and `(created_at DESC)` for efficient lookup and chronological review.

#### `finance.spending_summaries` (Materialized View)

Pre-computed monthly spending aggregates for dashboard and trend queries.

```sql
CREATE MATERIALIZED VIEW IF NOT EXISTS finance.spending_summaries AS
SELECT
    DATE_TRUNC('month', posted_at)::date AS period,
    account_id,
    category,
    direction,
    currency,
    COUNT(*) AS transaction_count,
    SUM(amount) AS total_amount,
    AVG(amount) AS avg_amount,
    MIN(amount) AS min_amount,
    MAX(amount) AS max_amount
FROM finance.transactions
WHERE deleted_at IS NULL
GROUP BY 1, 2, 3, 4, 5;
```

Unique index `uq_spending_summary_key` on `(period, account_id, category, direction, currency)` enables `REFRESH MATERIALIZED VIEW CONCURRENTLY` without blocking reads. Estimated refresh time for 50k rows: under 1 second.

Refresh triggers:
- Completion of `import_transactions` (bulk import)
- Daily anomaly digest scheduled task
- Explicit `compute_baselines()` calls

### CRUD Operations Contract

#### Create

**Single transaction** (`record_transaction`):
1. Check dedup via tiered key hierarchy.
2. Apply merchant mapping (auto-categorize if category not provided or `'uncategorized'`).
3. `INSERT INTO finance.transactions ... RETURNING *`.
4. If updating existing (bank re-statement via `external_id` match): record in `transaction_corrections`, increment `version`.
5. Post-insert: check large_transaction alert threshold.
6. Post-insert: check budget utilization.
7. Mirror to SPO fact layer (fire-and-forget, for memory/recall).

**Bulk import** (`import_transactions`):
1. Create `import_batches` row with `status = 'processing'`.
2. Detect format, parse CSV, normalize dates/amounts/merchant names.
3. Process in batches of 500.
4. Per row: dedup check, merchant mapping lookup, INSERT.
5. Update `import_batches` with final counts and status.
6. If 50+ imported: trigger `compute_baselines()`.
7. `REFRESH MATERIALIZED VIEW CONCURRENTLY finance.spending_summaries`.

#### Read

**Filtered listing** (`list_transactions`): Query `finance.transactions WHERE deleted_at IS NULL` with optional filters on `posted_at` range, `category`, `merchant` (ILIKE), `account_id`, `direction`, amount min/max, `tags` (array containment). Ordered by `posted_at DESC` with `LIMIT`/`OFFSET` pagination.

**Spending aggregation** (`spending_summary`): Aggregate from `finance.transactions WHERE direction = 'debit' AND deleted_at IS NULL` with grouping by `category`, `merchant`, `week`, or `month`. Response shape is identical to the current SPO-based implementation.

#### Update

**Field modification** (`update_transaction`):
1. Fetch current row.
2. For each changed field: `INSERT INTO transaction_corrections` with old value, new value, reason, source.
3. `UPDATE transactions SET ... WHERE id = $1 AND version = $expected_version` (optimistic lock).
4. Increment `version`, set `updated_at = now()`.
5. If category changed by user: set `category_source = 'manual'`, `is_category_locked = true`.
6. Version mismatch raises a conflict error; the update is not applied.

#### Soft Delete

```sql
UPDATE finance.transactions
SET deleted_at = now(), updated_at = now(), version = version + 1
WHERE id = $1 AND deleted_at IS NULL
RETURNING *;
```

No `DELETE FROM finance.transactions` statement exists anywhere in the codebase.

#### Merge Duplicates

`merge_duplicates(keep_id, duplicate_ids)`: Mark each duplicate with `is_duplicate = true`, `duplicate_of = keep_id`. Soft-delete duplicates. Record corrections for audit trail. Return the canonical transaction.

#### Split

`split_transaction(txn_id, parts)`: Validate `sum(parts.amount) == original.amount`. Soft-delete the original. Create new transaction rows for each part, inheriting the original's non-split fields, with `metadata.split_from = txn_id`. Record corrections.

#### Bulk Recategorize

`bulk_recategorize(merchant_pattern, new_category, create_rule)`: `UPDATE SET category = $1 WHERE merchant ILIKE $2 AND is_category_locked = false AND deleted_at IS NULL`. Record corrections for each updated row. If `create_rule = true`, upsert `merchant_mappings` with the pattern and category. Return count of updated transactions.

### SPO Mirror Strategy

The dedicated table is the primary store. The SPO fact layer is a secondary mirror for memory/recall compatibility.

**During Phase 3 (dual-write):**
- `record_transaction` writes to `finance.transactions` first, then fires a background task to mirror to `shared.facts` with `predicate='transaction_{direction}'`, `valid_at=posted_at`, `entity_id=owner_entity_id`, `scope='finance'`, and metadata containing all transaction fields.
- Mirror write is fire-and-forget. If it fails, the error is logged but the dedicated table write is not rolled back.
- Intelligence tools query `finance.transactions` exclusively.
- Memory tools (`memory_recall`, `memory_search`) continue querying `shared.facts`.

**After Phase 4 (deprecation):**
- SPO mirror writes stop.
- Existing facts remain in `shared.facts` read-only for historical recall.
- SPO-based transaction tool functions are removed from the MCP surface.

### Dividing Line: Dedicated Table vs. SPO Facts

| Data Type | Storage | Rationale |
|-----------|---------|-----------|
| Transactions | **Dedicated table** | High-volume, typed-column queries, range scans, aggregation |
| Subscriptions | **Dedicated table** | Already has `finance.subscriptions` |
| Bills | **Dedicated table** | Already has `finance.bills` |
| Accounts | **Dedicated table** | Already has `finance.accounts` |
| Budget targets | **Dedicated table** | Hot-path joins on `category` |
| Balance snapshots | **Dedicated table** | Net worth tracking with date uniqueness |
| Merchant mappings | **Dedicated table** | High-frequency ILIKE lookup |
| Alert configurations | **SPO facts** | Low-volume, rarely queried, fits property-fact pattern |
| Spending baselines | **SPO facts** | Computed statistics referenced by LLM for context |
| Anomaly thresholds | **SPO facts** | User preferences, fits property-fact pattern |
| User spending habits | **SPO facts** | Learned knowledge for LLM reasoning |

The dividing line: if the data is queried programmatically with SQL aggregation, range scans, or pattern matching at volume, it belongs in a dedicated table. If the data is contextual knowledge the LLM references during conversation, it belongs in the SPO fact layer.

### Migration Path

#### Phase 1: Schema Enhancement (Non-breaking)

Alembic migration `finance_002` at `roster/finance/migrations/versions/002_intelligence_tables.py` (`revision = "finance_002"`, `down_revision = "finance_001"`).

- Add 16 new columns to `finance.transactions` via `ALTER TABLE ADD COLUMN IF NOT EXISTS`. All have defaults; no existing columns removed.
- Create 8 new tables: `categories`, `merchant_mappings`, `recurring_groups`, `import_batches`, `balance_snapshots`, `budgets`, `transaction_corrections`.
- Create `spending_summaries` materialized view.
- Create all new indexes (18 total on transactions, plus per-table indexes).
- Seed default categories idempotently.
- `downgrade()` drops all new objects in reverse dependency order.

#### Phase 2: Backfill from SPO Facts

One-time INSERT of existing transaction facts into `finance.transactions`:

```sql
INSERT INTO finance.transactions (
    posted_at, merchant, amount, currency, direction, category,
    description, payment_method, account_id, source_message_id,
    source, metadata
)
SELECT
    f.valid_at,
    f.metadata->>'merchant',
    (f.metadata->>'amount')::numeric(14,2),
    COALESCE(f.metadata->>'currency', 'USD'),
    f.metadata->>'direction',
    COALESCE(f.metadata->>'category', 'uncategorized'),
    f.metadata->>'description',
    f.metadata->>'payment_method',
    (f.metadata->>'account_id')::uuid,
    f.metadata->>'source_message_id',
    'bulk',
    f.metadata
FROM shared.facts f
WHERE f.predicate IN ('transaction_debit', 'transaction_credit')
  AND f.validity = 'active'
  AND f.scope = 'finance'
  AND NOT EXISTS (
    SELECT 1 FROM finance.transactions t
    WHERE t.posted_at = f.valid_at
      AND t.merchant = f.metadata->>'merchant'
      AND t.amount = (f.metadata->>'amount')::numeric(14,2)
  );
```

Rows that fail JSONB extraction or casting are logged and skipped (not hard errors). Backfilled rows have `source = 'bulk'` for identification.

#### Phase 3: Dual-Write Transition

Both stores receive writes. `record_transaction` writes to `finance.transactions` (primary) then mirrors to `shared.facts` (fire-and-forget). Intelligence tools query the dedicated table exclusively. Memory tools continue reading facts. If the SPO mirror write fails, the error is logged but the primary write is not rolled back.

#### Phase 4: Deprecate SPO Transaction Writes

Remove the SPO mirror write from `record_transaction`. Existing facts remain read-only in `shared.facts`. Remove `record_transaction_fact`, `list_transaction_facts`, and other SPO-based transaction tools from the MCP surface.

**Timeline:** Phases 1-2 execute in a single migration. Phase 3 runs for 1-2 weeks for validation. Phase 4 is a cleanup task after validation.

### Performance Considerations

**Partitioning:** Deferred. For volumes under 200k rows, PostgreSQL handles a single table with proper indexes efficiently. Monitor with `EXPLAIN ANALYZE`; partition by year only when sequential scans exceed 100ms on the hot path.

**Materialized view staleness:** The `spending_summaries` view is only as fresh as its last refresh. Between cycles, tools fall back to querying `finance.transactions` directly. The materialized view is an optimization, not the source of truth.

**JSONB vs. dedicated columns:** Dedicated typed columns are used for all fields that participate in `WHERE`, `GROUP BY`, `ORDER BY`, type-safe arithmetic, or uniqueness constraints. The `metadata` JSONB column is retained for truly extensible fields that vary per source. The `raw_data` JSONB preserves the original import row for audit.

## Integration

- **RFC 0006:** All tables reside in the `finance` schema, following per-butler schema isolation. The database connection's `search_path` includes `finance` and `shared`. Migration `finance_002` is a butler-specific chain at `roster/finance/migrations/versions/` with `down_revision = "finance_001"`.
- **RFC 0002:** The finance module declares migration chain `"finance"` via `migration_revisions()`. New CRUD tools (`update_transaction`, `delete_transaction`, `merge_duplicates`, `split_transaction`, `bulk_recategorize`, `import_transactions`) are registered in the finance module's `register_tools()` method.
- **RFC 0004:** The SPO mirror write uses `entity_id = owner_entity_id` from the shared identity tables. No changes to shared schema structure.
- **RFC 0007:** Dashboard queries can read from `finance.spending_summaries` for pre-aggregated data. The `transaction_corrections` table provides audit history for the dashboard's transaction detail view.

## Alternatives Considered

**Keep SPO facts as primary with expression indexes.** Rejected because expression indexes on JSONB do not provide type safety, require 7+ indexes on a shared table, and every query still pays JSONB extraction cost. The dedicated table already exists with proper typed columns.

**Single composite hash for deduplication.** A `sha256(posted_at|amount|merchant|account_id)` stored in a `dedup_key` column. Rejected because: (a) opaque -- debugging dedup failures requires recomputing the hash, (b) does not distinguish between dedup key quality levels, (c) does not support the `(account_id, external_id)` fast path for banks with stable IDs.

**Trigger-based incremental summary table instead of materialized view.** Rejected for v1. Trigger maintenance adds schema complexity and the query volume does not justify it under 100k rows. `REFRESH MATERIALIZED VIEW CONCURRENTLY` is simpler and sufficient.

**Per-butler database for finance instead of schema isolation.** Rejected per RFC 0006 -- the operational overhead of a separate database outweighs the isolation benefit for a single-user deployment.
