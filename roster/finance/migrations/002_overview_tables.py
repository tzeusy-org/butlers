"""overview_tables — balance_snapshots, categories, and recurring_groups.

Revision ID: finance_002
Revises: finance_001
Create Date: 2026-03-26 00:00:00.000000

Adds:
  - finance.balance_snapshots : Point-in-time account balance records used by
                                net_worth_snapshot and net_worth_history.
  - finance.categories        : Category taxonomy with tax metadata used by
                                flag_tax_deductible.
  - finance.recurring_groups  : Detected recurring charge patterns used by
                                detect_recurring and subscription_audit.

These tables are required by the financial overview tools added in overview.py.
The overview module also has inline CREATE TABLE IF NOT EXISTS guards so it
degrades gracefully during staged roll-out; this migration makes those tables
permanent schema citizens with proper indexes and seed data.
"""

from __future__ import annotations

from alembic import op

revision = "finance_002"
down_revision = "finance_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- finance.balance_snapshots ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS balance_snapshots (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            balance     NUMERIC(14, 2) NOT NULL,
            currency    CHAR(3) NOT NULL DEFAULT 'USD',
            as_of_date  DATE NOT NULL,
            source      TEXT NOT NULL DEFAULT 'manual',
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_balance_snapshot_account_date UNIQUE (account_id, as_of_date)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_balance_snapshots_as_of_date
            ON balance_snapshots (as_of_date DESC)
    """)

    # --- finance.categories ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name            TEXT NOT NULL UNIQUE,
            display_name    TEXT,
            parent_category TEXT,
            is_tax_relevant BOOLEAN NOT NULL DEFAULT false,
            tax_category    TEXT,
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_categories_is_tax_relevant
            ON categories (is_tax_relevant)
            WHERE is_tax_relevant = true
    """)

    # Seed built-in tax-relevant categories (mirrors _DEFAULT_TAX_CATEGORIES in overview.py)
    op.execute("""
        INSERT INTO categories (name, display_name, is_tax_relevant, tax_category) VALUES
            ('medical',     'Medical',      true, 'medical_expense'),
            ('charitable',  'Charitable',   true, 'charitable_donation'),
            ('charity',     'Charity',      true, 'charitable_donation'),
            ('donation',    'Donation',     true, 'charitable_donation'),
            ('education',   'Education',    true, 'education_expense'),
            ('home_office', 'Home Office',  true, 'home_office'),
            ('business',    'Business',     true, 'business_expense')
        ON CONFLICT (name) DO NOTHING
    """)

    # --- finance.recurring_groups ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS recurring_groups (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant             TEXT NOT NULL UNIQUE,
            estimated_frequency  TEXT NOT NULL
                                     CHECK (estimated_frequency IN (
                                         'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                                     )),
            avg_amount           NUMERIC(14, 2) NOT NULL,
            currency             CHAR(3) NOT NULL DEFAULT 'USD',
            confidence           FLOAT NOT NULL DEFAULT 0.0,
            already_tracked      BOOLEAN NOT NULL DEFAULT false,
            occurrences          INTEGER NOT NULL DEFAULT 0,
            first_seen_at        TIMESTAMPTZ,
            last_seen_at         TIMESTAMPTZ,
            metadata             JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_recurring_groups_merchant
            ON recurring_groups (merchant)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_recurring_groups_already_tracked
            ON recurring_groups (already_tracked)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_recurring_groups_last_seen_at
            ON recurring_groups (last_seen_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS recurring_groups")
    op.execute("DROP TABLE IF EXISTS categories")
    op.execute("DROP TABLE IF EXISTS balance_snapshots")
