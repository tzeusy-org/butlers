"""finance_intelligence_tables

Revision ID: finance_002
Revises: finance_001
Create Date: 2026-03-28 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_002"
down_revision = "finance_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Task 1.2 — Add new columns to finance.transactions
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE transactions
            ADD COLUMN IF NOT EXISTS external_id TEXT,
            ADD COLUMN IF NOT EXISTS transaction_date DATE,
            ADD COLUMN IF NOT EXISTS normalized_description TEXT,
            ADD COLUMN IF NOT EXISTS normalized_merchant TEXT,
            ADD COLUMN IF NOT EXISTS subcategory TEXT,
            ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}'::text[],
            ADD COLUMN IF NOT EXISTS category_source TEXT NOT NULL DEFAULT 'auto',
            ADD COLUMN IF NOT EXISTS is_category_locked BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS type TEXT NOT NULL DEFAULT 'purchase',
            ADD COLUMN IF NOT EXISTS is_recurring BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS recurring_group_id UUID,
            ADD COLUMN IF NOT EXISTS is_duplicate BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN IF NOT EXISTS duplicate_of UUID,
            ADD COLUMN IF NOT EXISTS import_batch_id UUID,
            ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual',
            ADD COLUMN IF NOT EXISTS raw_data JSONB NOT NULL DEFAULT '{}'::jsonb,
            ADD COLUMN IF NOT EXISTS notes TEXT,
            ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1
    """)

    # -------------------------------------------------------------------------
    # Task 1.3 — Add new columns to finance.accounts
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE accounts
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true,
            ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    """)

    # Expand accounts.type CHECK constraint to include 'loan' and 'other'
    op.execute("""
        ALTER TABLE accounts
            DROP CONSTRAINT IF EXISTS accounts_type_check
    """)
    op.execute("""
        ALTER TABLE accounts
            ADD CONSTRAINT accounts_type_check
                CHECK (type IN ('checking', 'savings', 'credit', 'investment', 'loan', 'other'))
    """)

    # -------------------------------------------------------------------------
    # Task 1.4 — Create finance.categories table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name            TEXT NOT NULL UNIQUE,
            display_name    TEXT NOT NULL,
            parent_id       UUID REFERENCES categories(id) ON DELETE SET NULL,
            is_tax_relevant BOOLEAN NOT NULL DEFAULT false,
            tax_category    TEXT,
            is_system       BOOLEAN NOT NULL DEFAULT false,
            icon            TEXT,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # Seed default categories (idempotent — ON CONFLICT DO NOTHING)
    op.execute("""
        INSERT INTO categories (name, display_name, is_system, is_tax_relevant, tax_category, sort_order)
        VALUES
            ('groceries',       'Groceries',              true, false, NULL,           10),
            ('dining',          'Dining & Restaurants',   true, false, NULL,           20),
            ('transport',       'Transport',              true, false, NULL,           30),
            ('subscriptions',   'Subscriptions',          true, false, NULL,           40),
            ('utilities',       'Utilities',              true, false, NULL,           50),
            ('housing',         'Housing',                true, false, NULL,           60),
            ('healthcare',      'Healthcare',             true, true,  'medical',      70),
            ('entertainment',   'Entertainment',          true, false, NULL,           80),
            ('shopping',        'Shopping',               true, false, NULL,           90),
            ('travel',          'Travel',                 true, false, NULL,          100),
            ('education',       'Education',              true, true,  'education',   110),
            ('medical',         'Medical Expenses',       true, true,  'medical',     120),
            ('charitable',      'Charitable Donations',   true, true,  'charitable',  130),
            ('fees',            'Fees & Charges',         true, false, NULL,          140),
            ('income',          'Income',                 true, false, NULL,          150),
            ('transfer',        'Transfer',               true, false, NULL,          160),
            ('uncategorized',   'Uncategorized',          true, false, NULL,          999)
        ON CONFLICT DO NOTHING
    """)

    # -------------------------------------------------------------------------
    # Task 1.5 — Create finance.merchant_mappings table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS merchant_mappings (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            raw_pattern          TEXT NOT NULL,
            normalized_merchant  TEXT NOT NULL,
            category             TEXT NOT NULL,
            subcategory          TEXT,
            confidence           NUMERIC(4, 3) NOT NULL DEFAULT 1.0,
            learned_from_count   INTEGER NOT NULL DEFAULT 1,
            source               TEXT NOT NULL DEFAULT 'manual',
            is_active            BOOLEAN NOT NULL DEFAULT true,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_merchant_mapping_pattern
            ON merchant_mappings (lower(raw_pattern))
            WHERE is_active = true
    """)

    # -------------------------------------------------------------------------
    # Task 1.6 — Create finance.recurring_groups table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS recurring_groups (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant          TEXT NOT NULL,
            normalized_name   TEXT,
            expected_amount   NUMERIC(14, 2),
            amount_variance   NUMERIC(14, 2),
            frequency         TEXT,
            status            TEXT NOT NULL DEFAULT 'active',
            subscription_id   UUID REFERENCES subscriptions(id) ON DELETE SET NULL,
            is_subscription   BOOLEAN NOT NULL DEFAULT false,
            next_expected_date DATE,
            last_seen_at      TIMESTAMPTZ,
            confidence        NUMERIC(4, 3) NOT NULL DEFAULT 0.5,
            transaction_count INTEGER NOT NULL DEFAULT 0,
            metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -------------------------------------------------------------------------
    # Task 1.7 — Create finance.import_batches table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS import_batches (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source              TEXT NOT NULL,
            filename            TEXT,
            account_id          UUID REFERENCES accounts(id) ON DELETE SET NULL,
            status              TEXT NOT NULL DEFAULT 'pending',
            row_count           INTEGER NOT NULL DEFAULT 0,
            imported_count      INTEGER NOT NULL DEFAULT 0,
            skipped_count       INTEGER NOT NULL DEFAULT 0,
            error_count         INTEGER NOT NULL DEFAULT 0,
            completed_at        TIMESTAMPTZ,
            error_details       JSONB NOT NULL DEFAULT '{}'::jsonb,
            baselines_computed  BOOLEAN NOT NULL DEFAULT false,
            categories_learned  INTEGER NOT NULL DEFAULT 0,
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    # -------------------------------------------------------------------------
    # Task 1.8 — Create finance.balance_snapshots table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            balance     NUMERIC(14, 2) NOT NULL,
            currency    CHAR(3) NOT NULL DEFAULT 'USD',
            as_of_date  DATE NOT NULL,
            source      TEXT NOT NULL DEFAULT 'manual',
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_balance_snapshot_account_date
            ON balance_snapshots (account_id, as_of_date)
    """)

    # -------------------------------------------------------------------------
    # Task 1.9 — Create finance.budgets table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            category        TEXT NOT NULL,
            period          TEXT NOT NULL,
            amount          NUMERIC(14, 2) NOT NULL,
            currency        CHAR(3) NOT NULL DEFAULT 'USD',
            warn_threshold  NUMERIC(4, 3) NOT NULL DEFAULT 0.8,
            alert_threshold NUMERIC(4, 3) NOT NULL DEFAULT 1.0,
            is_active       BOOLEAN NOT NULL DEFAULT true,
            notes           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_category_period
            ON budgets (category, period)
            WHERE is_active = true
    """)

    # -------------------------------------------------------------------------
    # Task 1.10 — Create finance.transaction_corrections table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS transaction_corrections (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            transaction_id UUID NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
            field_name     TEXT NOT NULL,
            old_value      TEXT,
            new_value      TEXT,
            reason         TEXT,
            source         TEXT NOT NULL DEFAULT 'manual',
            created_by     TEXT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_correction_txn
            ON transaction_corrections (transaction_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_correction_created
            ON transaction_corrections (created_at DESC)
    """)

    # -------------------------------------------------------------------------
    # Task 1.11 — Create new indexes on finance.transactions
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_posted_at
            ON transactions (posted_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_transaction_date
            ON transactions (transaction_date DESC)
            WHERE transaction_date IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_category_posted
            ON transactions (category, posted_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_normalized_merchant
            ON transactions (normalized_merchant)
            WHERE normalized_merchant IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_direction_posted
            ON transactions (direction, posted_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_amount
            ON transactions (amount)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_active
            ON transactions (posted_at DESC)
            WHERE deleted_at IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_recurring_group
            ON transactions (recurring_group_id)
            WHERE recurring_group_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_import_batch
            ON transactions (import_batch_id)
            WHERE import_batch_id IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_tags_gin
            ON transactions USING GIN (tags)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_txn_debit_category_posted
            ON transactions (category, posted_at DESC)
            WHERE direction = 'debit' AND deleted_at IS NULL
    """)

    # -------------------------------------------------------------------------
    # Task 1.12 — Tiered deduplication UNIQUE partial indexes
    # -------------------------------------------------------------------------
    # Drop old dedup index from finance_001 that is superseded by the new tiered indexes
    op.execute("""
        DROP INDEX IF EXISTS uq_transactions_dedupe
    """)

    # Priority 1: Bank-provided external ID (highest confidence)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_txn_external_id_account
            ON transactions (account_id, external_id)
            WHERE external_id IS NOT NULL
    """)

    # Priority 2: Source message dedup (email ingestion dedup — replaces old uq_transactions_dedupe)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_txn_source_dedupe
            ON transactions (source_message_id, merchant, amount, posted_at)
            WHERE source_message_id IS NOT NULL
    """)

    # Priority 3: Composite fallback (only for rows without higher-priority keys)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_txn_composite_dedupe
            ON transactions (account_id, posted_at, amount, merchant)
            WHERE external_id IS NULL AND source_message_id IS NULL
    """)

    # -------------------------------------------------------------------------
    # Task 1.13 — Create finance.spending_summaries materialized view
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS spending_summaries AS
        SELECT
            date_trunc('month', posted_at)  AS period,
            account_id,
            category,
            direction,
            currency,
            COUNT(id)                       AS transaction_count,
            SUM(amount)                     AS total_amount,
            AVG(amount)                     AS avg_amount,
            MIN(amount)                     AS min_amount,
            MAX(amount)                     AS max_amount
        FROM transactions
        WHERE deleted_at IS NULL
        GROUP BY
            date_trunc('month', posted_at),
            account_id,
            category,
            direction,
            currency
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_spending_summary_key
            ON spending_summaries (period, account_id, category, direction, currency)
    """)

    # -------------------------------------------------------------------------
    # Task 1.14 — Add FK constraints on finance.transactions
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE transactions
            ADD CONSTRAINT fk_txn_recurring_group
                FOREIGN KEY (recurring_group_id)
                REFERENCES recurring_groups(id)
                ON DELETE SET NULL
    """)
    op.execute("""
        ALTER TABLE transactions
            ADD CONSTRAINT fk_txn_duplicate_of
                FOREIGN KEY (duplicate_of)
                REFERENCES transactions(id)
                ON DELETE SET NULL
    """)
    op.execute("""
        ALTER TABLE transactions
            ADD CONSTRAINT fk_txn_import_batch
                FOREIGN KEY (import_batch_id)
                REFERENCES import_batches(id)
                ON DELETE SET NULL
    """)


def downgrade() -> None:
    # Drop FK constraints on transactions first
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS fk_txn_import_batch")
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS fk_txn_duplicate_of")
    op.execute("ALTER TABLE transactions DROP CONSTRAINT IF EXISTS fk_txn_recurring_group")

    # Drop materialized view (no downstream FK dependencies)
    op.execute("DROP MATERIALIZED VIEW IF EXISTS spending_summaries")

    # Drop tiered dedup indexes (replace with original uq_transactions_dedupe)
    op.execute("DROP INDEX IF EXISTS uq_txn_composite_dedupe")
    op.execute("DROP INDEX IF EXISTS uq_txn_source_dedupe")
    op.execute("DROP INDEX IF EXISTS uq_txn_external_id_account")

    # Restore original dedup index from finance_001
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
            ON transactions (source_message_id, merchant, amount, posted_at)
            WHERE source_message_id IS NOT NULL
    """)

    # Drop new transaction indexes
    op.execute("DROP INDEX IF EXISTS idx_txn_debit_category_posted")
    op.execute("DROP INDEX IF EXISTS idx_txn_tags_gin")
    op.execute("DROP INDEX IF EXISTS idx_txn_import_batch")
    op.execute("DROP INDEX IF EXISTS idx_txn_recurring_group")
    op.execute("DROP INDEX IF EXISTS idx_txn_active")
    op.execute("DROP INDEX IF EXISTS idx_txn_amount")
    op.execute("DROP INDEX IF EXISTS idx_txn_direction_posted")
    op.execute("DROP INDEX IF EXISTS idx_txn_normalized_merchant")
    op.execute("DROP INDEX IF EXISTS idx_txn_category_posted")
    op.execute("DROP INDEX IF EXISTS idx_txn_transaction_date")
    op.execute("DROP INDEX IF EXISTS idx_txn_posted_at")

    # Drop tables with FKs to other tables first (dependency order)
    op.execute("DROP TABLE IF EXISTS transaction_corrections")
    op.execute("DROP TABLE IF EXISTS budgets")
    op.execute("DROP TABLE IF EXISTS balance_snapshots")
    op.execute("DROP TABLE IF EXISTS import_batches")
    op.execute("DROP TABLE IF EXISTS recurring_groups")
    op.execute("DROP TABLE IF EXISTS merchant_mappings")
    op.execute("DROP TABLE IF EXISTS categories")

    # Drop new columns from transactions
    op.execute("""
        ALTER TABLE transactions
            DROP COLUMN IF EXISTS version,
            DROP COLUMN IF EXISTS notes,
            DROP COLUMN IF EXISTS raw_data,
            DROP COLUMN IF EXISTS source,
            DROP COLUMN IF EXISTS import_batch_id,
            DROP COLUMN IF EXISTS duplicate_of,
            DROP COLUMN IF EXISTS is_duplicate,
            DROP COLUMN IF EXISTS recurring_group_id,
            DROP COLUMN IF EXISTS is_recurring,
            DROP COLUMN IF EXISTS type,
            DROP COLUMN IF EXISTS is_category_locked,
            DROP COLUMN IF EXISTS category_source,
            DROP COLUMN IF EXISTS tags,
            DROP COLUMN IF EXISTS subcategory,
            DROP COLUMN IF EXISTS normalized_merchant,
            DROP COLUMN IF EXISTS normalized_description,
            DROP COLUMN IF EXISTS transaction_date,
            DROP COLUMN IF EXISTS external_id
    """)

    # Drop new columns from accounts
    op.execute("""
        ALTER TABLE accounts
            DROP COLUMN IF EXISTS updated_at,
            DROP COLUMN IF EXISTS last_synced_at,
            DROP COLUMN IF EXISTS is_active
    """)

    # Restore original accounts.type CHECK constraint
    op.execute("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_type_check")
    op.execute("""
        ALTER TABLE accounts
            ADD CONSTRAINT accounts_type_check
                CHECK (type IN ('checking', 'savings', 'credit', 'investment'))
    """)
