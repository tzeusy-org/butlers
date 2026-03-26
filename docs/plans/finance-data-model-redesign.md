# Finance Data Model Redesign: From SPO Facts to Dedicated Transaction Tables

## Status

**Proposal** -- Awaiting review.

## Problem Statement

The Finance butler currently stores all transactional data in two parallel systems:

1. **Dedicated `finance.transactions` table** (migration `finance_001`) -- relational schema with typed columns, indexes on `posted_at`, `merchant`, `category`, `account_id`, and a dedup partial index on `source_message_id`.
2. **SPO fact layer** (`public.facts` table via `facts.py`) -- bitemporal facts with `predicate IN ('transaction_debit', 'transaction_credit')`, where all financial fields are packed into a JSONB `metadata` column.

The design document for the finance-intelligence OpenSpec change (`openspec/changes/finance-intelligence/design.md`) explicitly commits to Decision #1: "All intelligence tools query the existing transaction facts table with SQL window functions." This means all new analytics -- anomaly detection, trend analysis, budget enforcement, forecasting -- will run as SQL aggregations over the `public.facts` table, extracting values from JSONB metadata.

This approach has fundamental scaling and query-performance problems for high-volume transactional data (10k+ transactions/year, 50k+ historical). The intelligence spec requires:

- Rolling 6-month statistical baselines with `STDDEV`, `PERCENTILE_CONT`, and per-merchant grouping
- Per-category weekly velocity calculations with window functions
- Duplicate detection requiring self-joins on amount + merchant + date
- Month-over-month and year-over-year trend comparisons across full history
- Linear projection forecasting with daily aggregation
- Budget status checks requiring `DATE_TRUNC`-aligned aggregation by category

All of these require extracting amounts, merchants, categories, and dates from JSONB `metadata` at query time via `metadata->>'amount'` casts, which:
- Prevents the planner from using B-tree indexes on those fields
- Forces sequential scans or GIN containment checks that are not suitable for range queries
- Makes `ORDER BY`, `GROUP BY`, and window functions over extracted values expensive
- Breaks `NUMERIC` type safety (amounts stored as strings in JSONB require `::numeric` casts)

### Quantifying the Problem

For a user with 5 years of transaction history (roughly 50,000 rows), a single `spending_trends(comparison="mom", months=6)` call would need to:
1. Filter `facts` by `predicate IN ('transaction_debit')` and `validity = 'active'` and `scope = 'finance'`
2. Extract `metadata->>'amount'` and cast to `NUMERIC` for every matching row
3. Extract `metadata->>'category'` for grouping
4. Apply `DATE_TRUNC('month', valid_at)` for period bucketing
5. Compute `SUM`, `LAG`, and percentage changes via window functions

Steps 2-3 defeat indexing entirely. The partial B-tree index on `(predicate, valid_at)` helps step 1 but the aggregation still scans and casts every row's JSONB.

## Current Architecture Assessment

### What Exists Today

| Layer | Table | Schema | Purpose | Status |
|-------|-------|--------|---------|--------|
| Dedicated tables | `finance.transactions` | `finance` schema | Typed columns, proper indexes | **Active but unused by intelligence spec** |
| Dedicated tables | `finance.accounts` | `finance` schema | Bank account registry | Active |
| Dedicated tables | `finance.subscriptions` | `finance` schema | Subscription tracking | Active |
| Dedicated tables | `finance.bills` | `finance` schema | Bill obligations | Active |
| SPO facts | `public.facts` | `public` schema | Bitemporal fact store | **Primary store for intelligence tools** |

### The Duplication Problem

The finance butler currently writes to **both** systems. `record_transaction` writes to `finance.transactions`, while `record_transaction_fact` writes to `public.facts`. The `__init__.py` exports both, and `AGENTS.md` instructs the runtime to use the fact-layer tools. The dedicated `finance.transactions` table has proper typed columns and indexes but is sidelined in favor of the fact layer.

### Current Facts Table Schema (Relevant Columns)

```sql
-- From public.facts (memory module baseline + bitemporal migration)
CREATE TABLE facts (
    id              UUID PRIMARY KEY,
    subject         TEXT NOT NULL,          -- e.g., 'owner'
    predicate       TEXT NOT NULL,          -- 'transaction_debit' | 'transaction_credit'
    content         TEXT NOT NULL,          -- 'Merchant 123.45 USD' (human-readable)
    embedding       vector(384),            -- semantic search embedding (NULL for bulk imports)
    search_vector   tsvector,               -- full-text search
    importance      FLOAT,
    confidence      FLOAT,
    permanence      TEXT,                   -- 'stable' | 'standard' | 'volatile'
    scope           TEXT,                   -- 'finance'
    entity_id       UUID,                   -- owner entity FK
    valid_at        TIMESTAMPTZ,            -- posted_at (temporal dimension)
    validity        TEXT,                   -- 'active' | 'superseded'
    metadata        JSONB,                  -- ALL financial fields packed here
    created_at      TIMESTAMPTZ,
    -- ... other memory-module columns ...
);
```

Where `metadata` contains:
```json
{
    "merchant": "Trader Joe's",
    "amount": "45.67",
    "currency": "USD",
    "category": "groceries",
    "direction": "debit",
    "description": "Weekly groceries",
    "payment_method": "Amex",
    "account_id": "uuid-string",
    "source_message_id": "email-id",
    "normalized_merchant": "Trader Joe's",
    "inferred_category": "groceries"
}
```

### Current Indexes on Facts (Finance-Relevant)

```sql
-- Partial B-tree for transaction predicates
CREATE INDEX idx_facts_transaction_predicate_valid
    ON facts (predicate, valid_at)
    WHERE predicate >= 'transaction_' AND predicate < 'transaction`'
      AND validity = 'active';

-- GIN on metadata (containment queries only, not range)
CREATE INDEX idx_facts_metadata_gin ON facts USING gin(metadata);

-- Subject + predicate B-tree
CREATE INDEX idx_facts_subject_predicate ON facts (subject, predicate);
```

**Missing for intelligence workloads**: No index on `metadata->>'merchant'`, `metadata->>'category'`, or `(metadata->>'amount')::numeric`. GIN indexes support `@>` containment but not `>=`, `<=`, `LIKE`, or `ORDER BY`.

## Recommendation: Dedicated Transaction Table as the Primary Store

### Core Thesis

The existing `finance.transactions` table should be promoted to the **primary transactional data store** for the finance butler. The SPO fact layer should continue to receive transaction data for memory/recall purposes, but all intelligence queries -- anomaly detection, trend analysis, budget enforcement, forecasting, duplicate detection -- should query the dedicated table with proper typed columns and B-tree indexes.

This is not a new table design. The `finance.transactions` table already exists and is already indexed. It needs targeted enhancements to support the intelligence features.

### Why Not Just Add Expression Indexes to Facts?

Expression indexes on JSONB (e.g., `CREATE INDEX ON facts ((metadata->>'amount')::numeric)`) could partially solve the performance problem, but:

1. **Type safety**: Expression indexes do not enforce type constraints. A malformed `metadata->>'amount'` value (e.g., `"N/A"`) would corrupt the index or silently produce NULLs.
2. **Multiple indexes needed**: Each extracted field needs its own expression index. For the intelligence spec, that means indexes on `merchant`, `category`, `amount::numeric`, `direction`, `account_id`, and potentially `normalized_merchant` and `inferred_category` -- 7+ expression indexes on a single table shared with non-finance data.
3. **Shared table contention**: The `facts` table serves all butlers (health, relationship, finance). Adding finance-specific expression indexes and running heavy analytical queries on it affects all other fact consumers.
4. **Query complexity**: Every query must cast, extract, and filter JSONB. Compare:

```sql
-- SPO facts: every query pays extraction cost
SELECT DATE_TRUNC('month', valid_at), SUM((metadata->>'amount')::numeric)
FROM facts
WHERE predicate = 'transaction_debit'
  AND validity = 'active'
  AND scope = 'finance'
  AND valid_at >= $1 AND valid_at <= $2
  AND metadata->>'category' = $3
GROUP BY 1;

-- Dedicated table: clean, indexable, type-safe
SELECT DATE_TRUNC('month', posted_at), SUM(amount)
FROM finance.transactions
WHERE direction = 'debit'
  AND posted_at >= $1 AND posted_at <= $2
  AND category = $3
GROUP BY 1;
```

---

## Proposed Schema

### Design Principles

1. **NUMERIC, not float** -- All monetary values use `NUMERIC(14,2)` to prevent floating-point precision loss.
2. **Soft delete only** -- Financial data is never hard-deleted. `deleted_at IS NOT NULL` marks retired records.
3. **Audit trail** -- All mutations are tracked via `updated_at`, `version`, and the `transaction_corrections` table.
4. **Idempotent imports** -- Composite dedup keys prevent duplicate ingestion from any source.
5. **Separation of concerns** -- Raw imported data is preserved in `raw_data` JSONB; normalized fields are typed columns.

### 1. `finance.transactions` (Enhanced)

This table already exists. The following schema includes additions needed for the intelligence spec.

```sql
CREATE TABLE IF NOT EXISTS finance.transactions (
    -- Identity
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id         TEXT,                          -- Bank's transaction ID (for dedup)

    -- Account linkage
    account_id          UUID REFERENCES finance.accounts(id) ON DELETE SET NULL,

    -- Core financial data
    amount              NUMERIC(14, 2) NOT NULL,       -- Absolute value
    currency            CHAR(3) NOT NULL,              -- ISO 4217
    direction           TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),

    -- Temporal
    posted_at           TIMESTAMPTZ NOT NULL,          -- Transaction date (when it posted)
    transaction_date    DATE,                          -- Original transaction date (may differ from posted)

    -- Description
    description         TEXT,                          -- Raw bank description
    normalized_description TEXT,                       -- Cleaned/standardized description

    -- Merchant
    merchant            TEXT NOT NULL,                 -- Raw merchant name
    normalized_merchant TEXT,                          -- Cleaned merchant name

    -- Classification
    category            TEXT NOT NULL DEFAULT 'uncategorized',
    subcategory         TEXT,
    tags                TEXT[] DEFAULT '{}',            -- User-defined labels
    category_source     TEXT DEFAULT 'auto'            -- 'auto' | 'manual' | 'rule' | 'import'
                        CHECK (category_source IN ('auto', 'manual', 'rule', 'import')),
    is_category_locked  BOOLEAN NOT NULL DEFAULT false, -- Manual overrides preserved

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
    external_ref        TEXT,                          -- External provider reference
    source_message_id   TEXT,                          -- Email/message ID for dedup
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

-- ===========================================================================
-- Indexes
-- ===========================================================================

-- Primary query patterns: date-range scans
CREATE INDEX idx_txn_posted_at ON finance.transactions (posted_at DESC);
CREATE INDEX idx_txn_transaction_date ON finance.transactions (transaction_date)
    WHERE transaction_date IS NOT NULL;

-- Category-based queries (budget status, spending by category)
CREATE INDEX idx_txn_category ON finance.transactions (category);
CREATE INDEX idx_txn_category_posted ON finance.transactions (category, posted_at DESC);

-- Merchant-based queries (baselines, recurring detection, duplicate detection)
CREATE INDEX idx_txn_merchant ON finance.transactions (merchant);
CREATE INDEX idx_txn_normalized_merchant ON finance.transactions (normalized_merchant)
    WHERE normalized_merchant IS NOT NULL;

-- Account scoping
CREATE INDEX idx_txn_account_id ON finance.transactions (account_id)
    WHERE account_id IS NOT NULL;

-- Direction filtering (debit-only aggregation for spending)
CREATE INDEX idx_txn_direction_posted ON finance.transactions (direction, posted_at DESC);

-- Dedup: source_message_id-based (email ingestion)
CREATE UNIQUE INDEX uq_txn_source_dedupe
    ON finance.transactions (source_message_id, merchant, amount, posted_at)
    WHERE source_message_id IS NOT NULL;

-- Dedup: external_id-based (bank transaction ID)
CREATE UNIQUE INDEX uq_txn_external_id_account
    ON finance.transactions (account_id, external_id)
    WHERE external_id IS NOT NULL;

-- Dedup: composite fallback (banks without stable IDs)
CREATE UNIQUE INDEX uq_txn_composite_dedupe
    ON finance.transactions (account_id, posted_at, amount, merchant)
    WHERE external_id IS NULL AND source_message_id IS NULL;

-- Amount-range queries (anomaly detection: "transactions over X")
CREATE INDEX idx_txn_amount ON finance.transactions (amount);

-- Recurring group membership
CREATE INDEX idx_txn_recurring_group ON finance.transactions (recurring_group_id)
    WHERE recurring_group_id IS NOT NULL;

-- Import batch lookup
CREATE INDEX idx_txn_import_batch ON finance.transactions (import_batch_id)
    WHERE import_batch_id IS NOT NULL;

-- Soft delete: exclude deleted rows from all normal queries
CREATE INDEX idx_txn_active ON finance.transactions (posted_at DESC)
    WHERE deleted_at IS NULL;

-- Metadata GIN (extensible queries)
CREATE INDEX idx_txn_metadata_gin ON finance.transactions USING GIN (metadata);

-- Tags GIN (array containment: tags @> ARRAY['tax-deductible'])
CREATE INDEX idx_txn_tags_gin ON finance.transactions USING GIN (tags);

-- Composite: spending summary by category in date range (covers the hot path)
CREATE INDEX idx_txn_debit_category_posted
    ON finance.transactions (category, posted_at)
    WHERE direction = 'debit' AND deleted_at IS NULL;
```

### Column Changes from Current Schema

| Column | Current | Proposed | Rationale |
|--------|---------|----------|-----------|
| `external_id` | Not present | Added | Bank-provided transaction ID for dedup |
| `transaction_date` | Not present | Added | Some banks distinguish transaction vs. posted date |
| `normalized_description` | Not present | Added | Cleaned description for matching |
| `normalized_merchant` | Not present | Added | Cleaned merchant for dedup/grouping |
| `subcategory` | Not present | Added | Hierarchical categorization |
| `tags` | Not present | Added | User-defined labels (tax-deductible, reimbursable, etc.) |
| `category_source` | Not present | Added | Track how categorization happened |
| `is_category_locked` | Not present | Added | Prevent auto-recategorization of manual overrides |
| `type` | Not present | Added | Transaction type beyond debit/credit direction |
| `is_recurring` | Not present | Added | Flag for detected recurring charges |
| `recurring_group_id` | Not present | Added | FK to recurring charge group |
| `is_duplicate` | Not present | Added | Soft flag for suspected duplicates |
| `duplicate_of` | Not present | Added | Points to the canonical transaction |
| `import_batch_id` | Not present | Added | Which import brought this in |
| `source` | Not present | Added | manual, email, csv_import, api, bulk |
| `raw_data` | Not present | Added | Original import row preserved for audit |
| `notes` | Not present | Added | User annotations |
| `deleted_at` | Not present | Added | Soft delete (never hard delete financial data) |
| `version` | Not present | Added | Optimistic locking for concurrent edits |

### 2. `finance.accounts` (Enhanced)

The existing table needs minor additions for net worth tracking and sync metadata.

```sql
CREATE TABLE IF NOT EXISTS finance.accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    institution     TEXT NOT NULL,
    type            TEXT NOT NULL
                    CHECK (type IN ('checking', 'savings', 'credit',
                                    'investment', 'loan', 'other')),
    name            TEXT,
    last_four       CHAR(4),
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    is_active       BOOLEAN NOT NULL DEFAULT true,     -- Can be deactivated without deletion
    last_synced_at  TIMESTAMPTZ,                       -- When data was last imported
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_accounts_institution_type_last_four
    ON finance.accounts (institution, type, last_four)
    WHERE last_four IS NOT NULL;
CREATE INDEX idx_accounts_institution ON finance.accounts (institution);
CREATE INDEX idx_accounts_type ON finance.accounts (type);
CREATE INDEX idx_accounts_active ON finance.accounts (is_active) WHERE is_active = true;
```

### 3. `finance.categories` (New)

Hierarchical category taxonomy for consistent categorization.

```sql
CREATE TABLE IF NOT EXISTS finance.categories (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL UNIQUE,               -- 'groceries', 'dining', 'transport'
    parent_id       UUID REFERENCES finance.categories(id) ON DELETE SET NULL,
    display_name    TEXT,                               -- 'Groceries', 'Dining Out'
    icon            TEXT,                               -- Optional emoji or icon name
    is_tax_relevant BOOLEAN NOT NULL DEFAULT false,     -- Flag for tax categorization
    tax_category    TEXT,                               -- Mapped tax bucket (e.g., 'medical', 'charitable')
    is_system       BOOLEAN NOT NULL DEFAULT false,     -- System-defined vs. user-created
    sort_order      INTEGER NOT NULL DEFAULT 0,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_categories_parent ON finance.categories (parent_id);
CREATE INDEX idx_categories_tax ON finance.categories (is_tax_relevant)
    WHERE is_tax_relevant = true;

-- Seed default categories
INSERT INTO finance.categories (name, display_name, is_system, is_tax_relevant, tax_category) VALUES
    ('groceries',        'Groceries',         true, false, NULL),
    ('dining',           'Dining Out',        true, false, NULL),
    ('transport',        'Transportation',    true, false, NULL),
    ('subscriptions',    'Subscriptions',     true, false, NULL),
    ('utilities',        'Utilities',         true, false, NULL),
    ('housing',          'Housing',           true, false, NULL),
    ('healthcare',       'Healthcare',        true, false, NULL),
    ('entertainment',    'Entertainment',     true, false, NULL),
    ('shopping',         'Shopping',          true, false, NULL),
    ('travel',           'Travel',            true, false, NULL),
    ('education',        'Education',         true, true,  'education'),
    ('medical',          'Medical',           true, true,  'medical'),
    ('charitable',       'Charitable',        true, true,  'charitable'),
    ('home_office',      'Home Office',       true, true,  'home_office'),
    ('business_expense', 'Business Expense',  true, true,  'business_expense'),
    ('insurance',        'Insurance',         true, false, NULL),
    ('personal_care',    'Personal Care',     true, false, NULL),
    ('gifts',            'Gifts',             true, false, NULL),
    ('income',           'Income',            true, false, NULL),
    ('transfer',         'Transfer',          true, false, NULL),
    ('fees',             'Fees & Charges',    true, false, NULL),
    ('uncategorized',    'Uncategorized',     true, false, NULL)
ON CONFLICT (name) DO NOTHING;
```

### 4. `finance.merchant_mappings` (New)

Learned merchant-to-category mapping table. Replaces `predicate='merchant_category_mapping'` fact storage with a proper lookup table optimized for pattern matching.

```sql
CREATE TABLE IF NOT EXISTS finance.merchant_mappings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_pattern         TEXT NOT NULL,                  -- Original merchant string pattern (ILIKE)
    normalized_merchant TEXT NOT NULL,                  -- Cleaned merchant name
    category            TEXT NOT NULL,                  -- Default category for this merchant
    confidence          FLOAT NOT NULL DEFAULT 1.0,     -- (most_frequent_count / total_count)
    learned_from_count  INTEGER NOT NULL DEFAULT 0,     -- How many transactions informed this
    source              TEXT NOT NULL DEFAULT 'learned' -- 'learned' | 'manual' | 'import'
                        CHECK (source IN ('learned', 'manual', 'import')),
    is_active           BOOLEAN NOT NULL DEFAULT true,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_merchant_mapping_pattern
    ON finance.merchant_mappings (lower(raw_pattern))
    WHERE is_active = true;
CREATE INDEX idx_merchant_mapping_normalized
    ON finance.merchant_mappings (normalized_merchant);
CREATE INDEX idx_merchant_mapping_category
    ON finance.merchant_mappings (category);
```

**Trade-off vs. SPO facts**: The design doc (Decision #2) chose SPO facts for merchant mappings because they "fit the SPO model naturally." However, merchant mapping lookups are high-frequency (called on every transaction import) and pattern-matching intensive (`ILIKE`). A dedicated table with a unique index on `lower(raw_pattern)` is dramatically faster than scanning facts with `predicate='merchant_category_mapping'` and extracting the pattern from metadata. The memory fact layer is better suited for learned knowledge the LLM reasons about; merchant mappings are a lookup table the code queries programmatically.

### 5. `finance.recurring_groups` (New)

Detected recurring charge patterns. Links multiple transactions that form a subscription-like pattern.

```sql
CREATE TABLE IF NOT EXISTS finance.recurring_groups (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant            TEXT NOT NULL,
    normalized_merchant TEXT,
    expected_amount     NUMERIC(14, 2) NOT NULL,
    amount_variance_pct FLOAT NOT NULL DEFAULT 0.0,    -- % variance observed
    currency            CHAR(3) NOT NULL DEFAULT 'USD',
    frequency           TEXT NOT NULL
                        CHECK (frequency IN ('weekly', 'monthly', 'quarterly', 'yearly')),
    occurrence_count    INTEGER NOT NULL DEFAULT 0,
    last_charge_date    DATE,
    next_expected_date  DATE,
    confidence          TEXT NOT NULL DEFAULT 'medium'
                        CHECK (confidence IN ('high', 'medium', 'low')),
    is_subscription     BOOLEAN NOT NULL DEFAULT false, -- Confirmed by user or linked to subscription
    subscription_id     UUID REFERENCES finance.subscriptions(id) ON DELETE SET NULL,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'paused', 'stopped')),
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_recurring_merchant ON finance.recurring_groups (merchant);
CREATE INDEX idx_recurring_next_expected ON finance.recurring_groups (next_expected_date)
    WHERE status = 'active';
CREATE INDEX idx_recurring_status ON finance.recurring_groups (status);
```

### 6. `finance.import_batches` (New)

Audit trail for each data import operation.

```sql
CREATE TABLE IF NOT EXISTS finance.import_batches (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source          TEXT NOT NULL,                     -- 'chase_csv', 'amex_csv', 'capital_one_csv', 'generic_csv', 'manual'
    filename        TEXT,                              -- Original filename
    account_id      UUID REFERENCES finance.accounts(id) ON DELETE SET NULL,
    row_count       INTEGER NOT NULL DEFAULT 0,        -- Total rows in source
    imported_count  INTEGER NOT NULL DEFAULT 0,        -- Successfully imported
    skipped_count   INTEGER NOT NULL DEFAULT 0,        -- Duplicates skipped
    error_count     INTEGER NOT NULL DEFAULT 0,        -- Failed rows
    date_range_start DATE,                             -- Earliest transaction in batch
    date_range_end  DATE,                              -- Latest transaction in batch
    detected_format TEXT,                              -- Auto-detected format name
    column_mapping  JSONB,                             -- Column mapping used
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'completed',
                                      'completed_with_errors', 'failed')),
    error_details   JSONB DEFAULT '[]'::jsonb,         -- [{row, reason}]
    baselines_computed BOOLEAN NOT NULL DEFAULT false,
    categories_learned INTEGER NOT NULL DEFAULT 0,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_import_batch_status ON finance.import_batches (status);
CREATE INDEX idx_import_batch_account ON finance.import_batches (account_id);
CREATE INDEX idx_import_batch_created ON finance.import_batches (created_at DESC);
```

### 7. `finance.balance_snapshots` (New)

Periodic account balance records for net worth tracking. Replaces the `predicate='account_balance'` temporal fact approach.

```sql
CREATE TABLE IF NOT EXISTS finance.balance_snapshots (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES finance.accounts(id) ON DELETE CASCADE,
    balance         NUMERIC(14, 2) NOT NULL,           -- Negative for credit/loan accounts
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    as_of_date      DATE NOT NULL,                     -- When this balance was valid
    source          TEXT NOT NULL DEFAULT 'manual'      -- 'manual' | 'import' | 'statement'
                    CHECK (source IN ('manual', 'import', 'statement')),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One snapshot per account per date
CREATE UNIQUE INDEX uq_balance_snapshot_account_date
    ON finance.balance_snapshots (account_id, as_of_date);
CREATE INDEX idx_balance_snapshot_date ON finance.balance_snapshots (as_of_date DESC);
CREATE INDEX idx_balance_snapshot_account ON finance.balance_snapshots (account_id);
```

### 8. `finance.budgets` (New)

Category budget targets with threshold configuration.

```sql
CREATE TABLE IF NOT EXISTS finance.budgets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category        TEXT NOT NULL,
    amount          NUMERIC(14, 2) NOT NULL,
    currency        CHAR(3) NOT NULL DEFAULT 'USD',
    period          TEXT NOT NULL
                    CHECK (period IN ('weekly', 'monthly', 'quarterly', 'annual')),
    warn_threshold  FLOAT NOT NULL DEFAULT 0.8,        -- Warn at 80% utilization
    alert_threshold FLOAT NOT NULL DEFAULT 1.0,        -- Alert at 100% utilization
    is_active       BOOLEAN NOT NULL DEFAULT true,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One active budget per category per period
CREATE UNIQUE INDEX uq_budget_category_period
    ON finance.budgets (category, period)
    WHERE is_active = true;
CREATE INDEX idx_budget_active ON finance.budgets (is_active) WHERE is_active = true;
```

### 9. `finance.transaction_corrections` (New)

Audit trail for edits to transaction data. Financial data should never be silently modified.

```sql
CREATE TABLE IF NOT EXISTS finance.transaction_corrections (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id  UUID NOT NULL REFERENCES finance.transactions(id) ON DELETE CASCADE,
    field_name      TEXT NOT NULL,                      -- Which column was changed
    old_value       TEXT,                               -- Previous value (as text)
    new_value       TEXT,                               -- New value (as text)
    reason          TEXT,                               -- Why the change was made
    source          TEXT NOT NULL DEFAULT 'user'        -- 'user' | 'rule' | 'auto' | 'merge'
                    CHECK (source IN ('user', 'rule', 'auto', 'merge')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_correction_txn ON finance.transaction_corrections (transaction_id);
CREATE INDEX idx_correction_created ON finance.transaction_corrections (created_at DESC);
```

### 10. `finance.spending_summaries` (New -- Materialized)

Pre-computed monthly spending aggregates to avoid re-scanning the full transaction table for dashboard and trend queries. Refreshed nightly or after bulk imports.

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

CREATE UNIQUE INDEX uq_spending_summary_key
    ON finance.spending_summaries (period, account_id, category, direction, currency);
CREATE INDEX idx_spending_summary_period
    ON finance.spending_summaries (period DESC);
CREATE INDEX idx_spending_summary_category
    ON finance.spending_summaries (category);
```

Refresh strategy:
```sql
REFRESH MATERIALIZED VIEW CONCURRENTLY finance.spending_summaries;
```

This should be triggered by:
- Completion of `import_transactions` (bulk import)
- The daily anomaly digest scheduled task (piggyback on existing schedule)
- Explicit `compute_baselines()` calls

---

## Idempotent Sync and Deduplication Strategy

### Dedup Key Hierarchy

Transactions can arrive from multiple sources. The dedup strategy uses a priority hierarchy of keys:

| Priority | Key | Use Case |
|----------|-----|----------|
| 1 | `(account_id, external_id)` | Banks that provide stable transaction IDs |
| 2 | `(source_message_id, merchant, amount, posted_at)` | Email-extracted transactions |
| 3 | `(account_id, posted_at, amount, merchant)` | CSV imports without stable IDs |

Each level is enforced by a `UNIQUE` partial index (shown in the transactions schema above). The application layer checks in priority order:

```python
async def _deduplicate(pool, txn: dict) -> UUID | None:
    """Return existing transaction ID if duplicate found, else None."""
    # Priority 1: external_id
    if txn.get("external_id") and txn.get("account_id"):
        row = await pool.fetchrow(
            "SELECT id FROM transactions WHERE account_id = $1 AND external_id = $2",
            txn["account_id"], txn["external_id"]
        )
        if row: return row["id"]

    # Priority 2: source_message_id
    if txn.get("source_message_id"):
        row = await pool.fetchrow(
            """SELECT id FROM transactions
               WHERE source_message_id = $1 AND merchant = $2
                 AND amount = $3 AND posted_at = $4""",
            txn["source_message_id"], txn["merchant"],
            txn["amount"], txn["posted_at"]
        )
        if row: return row["id"]

    # Priority 3: composite fallback
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

### Handling Bank Re-statements

When a bank changes a transaction amount after posting (e.g., tip adjustment, currency conversion):

1. Match on `(account_id, external_id)` -- the external ID remains stable.
2. Update the `amount` field.
3. Record the change in `transaction_corrections`.
4. Increment `version`.

### Handling Pending to Posted Transitions

1. Pending transactions are imported with `type='purchase'` and a `posted_at` of the pending date.
2. When the posted version arrives with the same `external_id`, the row is updated in-place:
   - `posted_at` updated to the real posted date
   - `transaction_date` retains the original pending date
   - Correction recorded in `transaction_corrections`

### Import Idempotency

Each import creates an `import_batches` row. Rows are deduplicated individually via the key hierarchy. A re-import of the same CSV file is safe: all rows will be deduped as `skipped`. The `import_batch_id` on each transaction provides a complete audit trail of which import brought each row in.

---

## CRUD Operations

### Create

**Single transaction:**
```python
async def record_transaction(pool, ...) -> dict:
    # 1. Check dedup
    # 2. Apply merchant mapping (auto-categorize if category not provided)
    # 3. INSERT with RETURNING *
    # 4. Record in transaction_corrections if updating existing
    # 5. Post-insert: check large_transaction alert threshold
    # 6. Post-insert: check budget utilization
    # 7. Mirror to SPO fact layer (fire-and-forget, for memory/recall)
```

**Bulk import:**
```python
async def import_transactions(pool, file_path, ...) -> dict:
    # 1. Create import_batch row (status='processing')
    # 2. Detect format, parse CSV
    # 3. Normalize dates, amounts, merchant names
    # 4. Process in batches of 500
    # 5. Per row: dedup check, merchant mapping, INSERT
    # 6. Update import_batch with counts
    # 7. If 50+ imported: trigger compute_baselines()
    # 8. REFRESH MATERIALIZED VIEW spending_summaries
```

### Read

**List with filters:**
```sql
SELECT * FROM finance.transactions
WHERE deleted_at IS NULL
  AND posted_at BETWEEN $1 AND $2
  AND category = $3
  AND merchant ILIKE $4
ORDER BY posted_at DESC
LIMIT $5 OFFSET $6;
```

**Spending by category this month:**
```sql
SELECT category, SUM(amount) AS total, COUNT(*) AS count
FROM finance.transactions
WHERE direction = 'debit'
  AND deleted_at IS NULL
  AND posted_at >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY category
ORDER BY total DESC;
```

**All transactions from merchant X:**
```sql
SELECT * FROM finance.transactions
WHERE deleted_at IS NULL
  AND (merchant ILIKE $1 OR normalized_merchant ILIKE $1)
ORDER BY posted_at DESC;
```

**Anomalies in last 30 days** (joins with baselines computed by `compute_baselines()`):
```sql
WITH merchant_baselines AS (
    SELECT
        merchant,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount) AS median_amount,
        STDDEV(amount) AS stddev_amount,
        COUNT(*) AS txn_count
    FROM finance.transactions
    WHERE direction = 'debit'
      AND deleted_at IS NULL
      AND posted_at >= CURRENT_DATE - INTERVAL '6 months'
    GROUP BY merchant
    HAVING COUNT(*) >= 3
)
SELECT t.*, mb.median_amount, mb.stddev_amount,
       ABS(t.amount - mb.median_amount) / NULLIF(mb.stddev_amount, 0) AS deviation_factor
FROM finance.transactions t
JOIN merchant_baselines mb ON t.merchant = mb.merchant
WHERE t.direction = 'debit'
  AND t.deleted_at IS NULL
  AND t.posted_at >= CURRENT_DATE - INTERVAL '30 days'
  AND ABS(t.amount - mb.median_amount) / NULLIF(mb.stddev_amount, 0) > 2.5
ORDER BY deviation_factor DESC;
```

### Update

**Modify category with audit trail:**
```python
async def update_transaction(pool, txn_id, updates: dict) -> dict:
    # 1. Fetch current row
    # 2. For each changed field: INSERT into transaction_corrections
    # 3. UPDATE transactions SET ... WHERE id = $1 AND version = $2
    #    (optimistic lock -- raises on version mismatch)
    # 4. Increment version
    # 5. If category changed and is_category_locked was false:
    #    set is_category_locked = true (user correction)
```

### Delete (Soft)

```sql
UPDATE finance.transactions
SET deleted_at = now(), updated_at = now(), version = version + 1
WHERE id = $1 AND deleted_at IS NULL
RETURNING *;
```

### Merge (Duplicates)

```python
async def merge_duplicates(pool, keep_id: UUID, duplicate_ids: list[UUID]) -> dict:
    # 1. Mark duplicates: is_duplicate = true, duplicate_of = keep_id
    # 2. Soft-delete duplicates (deleted_at = now())
    # 3. Record corrections for audit trail
    # 4. Return merged transaction
```

### Split

```python
async def split_transaction(pool, txn_id: UUID, parts: list[dict]) -> list[dict]:
    # parts = [{"amount": 50.00, "category": "groceries"},
    #          {"amount": 30.00, "category": "electronics"}]
    # 1. Validate sum(parts.amount) == original.amount
    # 2. Soft-delete original
    # 3. Create new transactions for each part, linked via metadata.split_from = txn_id
    # 4. Record corrections
```

### Bulk Recategorize

```python
async def bulk_recategorize(pool, merchant_pattern: str, new_category: str,
                            create_rule: bool = False) -> dict:
    # 1. UPDATE transactions SET category = $1
    #    WHERE merchant ILIKE $2 AND is_category_locked = false AND deleted_at IS NULL
    # 2. Record corrections for each updated row
    # 3. If create_rule: upsert merchant_mappings with the new category
    # 4. Return count of updated transactions
```

---

## Performance Considerations

### Indexing Strategy

The proposed indexes cover the five primary query patterns:

| Query Pattern | Index Used |
|---------------|-----------|
| Date range scan | `idx_txn_posted_at`, `idx_txn_active` |
| Category spending | `idx_txn_debit_category_posted` (composite partial) |
| Merchant lookup | `idx_txn_merchant`, `idx_txn_normalized_merchant` |
| Amount range | `idx_txn_amount` |
| Account scoping | `idx_txn_account_id` |

### Partitioning Strategy

For volumes under 200k rows, PostgreSQL handles a single table with proper indexes efficiently. If a user accumulates >200k transactions (roughly 10+ years of heavy usage), consider range partitioning by year:

```sql
-- Only implement if performance degrades measurably
CREATE TABLE finance.transactions (
    ...
) PARTITION BY RANGE (posted_at);

CREATE TABLE finance.transactions_2024 PARTITION OF finance.transactions
    FOR VALUES FROM ('2024-01-01') TO ('2025-01-01');
CREATE TABLE finance.transactions_2025 PARTITION OF finance.transactions
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
-- etc.
```

**Recommendation**: Do not partition in v1. Monitor query latency. Partition only when `EXPLAIN ANALYZE` shows sequential scans exceeding 100ms on the hot path (spending summary by category in date range).

### Materialized View Refresh

The `finance.spending_summaries` materialized view avoids full-table scans for dashboard queries. `REFRESH MATERIALIZED VIEW CONCURRENTLY` requires the unique index and does not block reads.

Estimated refresh time for 50k rows: < 1 second.

### JSONB vs. Dedicated Columns

The schema uses dedicated columns for all fields that participate in:
- `WHERE` clauses (indexed)
- `GROUP BY` / `ORDER BY` aggregation
- Type-safe arithmetic (`NUMERIC`)
- Uniqueness constraints (dedup keys)

The `metadata` JSONB column is retained for truly extensible fields that vary per source (e.g., original bank-specific fields, import-time annotations). The `raw_data` JSONB preserves the entire original import row for audit.

---

## Migration Path

### Phase 1: Schema Enhancement (Non-breaking)

1. Add new columns to `finance.transactions` via `ALTER TABLE ADD COLUMN`.
2. Create new tables (`categories`, `merchant_mappings`, `recurring_groups`, `import_batches`, `balance_snapshots`, `budgets`, `transaction_corrections`).
3. Create the materialized view.
4. All additions are backward-compatible -- no existing columns are removed or renamed.

```python
# Migration: finance_002_intelligence_tables.py
def upgrade():
    # Add new columns to transactions
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS external_id TEXT")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS transaction_date DATE")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS normalized_description TEXT")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS normalized_merchant TEXT")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS subcategory TEXT")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}'")
    op.execute("""ALTER TABLE transactions ADD COLUMN IF NOT EXISTS category_source TEXT
                  DEFAULT 'auto' CHECK (category_source IN ('auto','manual','rule','import'))""")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_category_locked BOOLEAN NOT NULL DEFAULT false")
    op.execute("""ALTER TABLE transactions ADD COLUMN IF NOT EXISTS type TEXT DEFAULT 'purchase'
                  CHECK (type IN ('purchase','refund','transfer','payment','fee','interest','atm','deposit','other'))""")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS recurring_group_id UUID")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN NOT NULL DEFAULT false")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS duplicate_of UUID")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS import_batch_id UUID")
    op.execute("""ALTER TABLE transactions ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'
                  CHECK (source IN ('manual','email','csv_import','api','bulk'))""")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS raw_data JSONB DEFAULT '{}'::jsonb")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS notes TEXT")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE transactions ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")

    # Create new tables (categories, merchant_mappings, recurring_groups, etc.)
    # ... CREATE TABLE statements from sections 3-9 above ...

    # Add new indexes
    # ... CREATE INDEX statements from section 1 above ...

    # Create materialized view
    # ... from section 10 above ...
```

### Phase 2: Backfill from SPO Facts

Migrate existing transaction facts to the dedicated table:

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
FROM public.facts f
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

### Phase 3: Dual-Write Transition

During the transition period, both stores receive writes:

1. `record_transaction` writes to `finance.transactions` (primary) and fires a background task to mirror to `public.facts` (for memory/recall compatibility).
2. Intelligence tools (`anomaly_scan`, `spending_trends`, `budget_status`, etc.) query `finance.transactions` exclusively.
3. Memory tools (`memory_recall`, `memory_search`) continue to query `public.facts` for financial context in LLM conversations.

### Phase 4: Deprecate SPO Transaction Writes

Once the intelligence features are stable and all queries use the dedicated table:

1. Stop writing transaction data to `public.facts`.
2. Keep existing facts in place (read-only, for historical memory/recall).
3. Remove the fact-layer transaction tools from the MCP surface.

**Timeline estimate**: Phase 1-2 can happen in a single migration. Phase 3 runs for 1-2 weeks to validate. Phase 4 is a cleanup task after validation.

---

## Relationship to SPO Fact Layer

The SPO fact layer remains the correct home for:

| Data Type | Storage | Rationale |
|-----------|---------|-----------|
| Transactions | **Dedicated table** | High-volume, typed-column queries, range scans, aggregation |
| Subscriptions | **Dedicated table** | Already has `finance.subscriptions`; keep it |
| Bills | **Dedicated table** | Already has `finance.bills`; keep it |
| Accounts | **Dedicated table** | Already has `finance.accounts`; keep it |
| Budget targets | **Dedicated table** | New `finance.budgets` table |
| Balance snapshots | **Dedicated table** | New `finance.balance_snapshots` table |
| Merchant mappings | **Dedicated table** | New `finance.merchant_mappings` table; high-frequency lookup |
| Alert configurations | **SPO facts** | Low-volume, rarely queried, fits property-fact pattern |
| Spending baselines | **SPO facts** | Computed statistics, referenced by LLM for context |
| Anomaly thresholds | **SPO facts** | User preferences, fits property-fact pattern |
| User spending habits | **SPO facts** | Learned knowledge for LLM reasoning |

The dividing line: if the data is queried programmatically with SQL aggregation, range scans, or pattern matching at volume, it belongs in a dedicated table. If the data is contextual knowledge the LLM references during conversation, it belongs in the SPO fact layer.

---

## Open Questions

1. **Multi-currency transactions**: The current schema stores `currency` per transaction but all aggregation assumes same-currency. Should we add a `base_amount` column with a normalized currency for cross-currency comparison? Deferred per the design doc's non-goal, but worth considering for the future.

2. **Transaction splitting granularity**: When splitting a transaction (e.g., Costco receipt into groceries + electronics), should split children be full transaction rows or a separate `transaction_splits` table? Full rows are simpler and work with all existing queries; a separate table avoids inflating transaction count.

3. **Materialized view vs. summary table**: The materialized view requires `REFRESH MATERIALIZED VIEW` which rebuilds the entire view. For very large datasets, an incrementally-updated summary table (maintained by triggers) may be more efficient. For v1 volumes (under 100k rows), the materialized view approach is simpler and sufficient.

4. **Backward compatibility of tool surface**: The intelligence spec references SPO-based tool names (`record_transaction_fact`, `list_transaction_facts`, `spending_summary_facts`). These tools should continue to work during the transition but route to the dedicated table. The MCP tool wrappers in `roster/finance/modules/tools.py` provide a natural seam for this routing change.
