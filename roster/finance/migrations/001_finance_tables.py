"""finance_tables

Revision ID: finance_001
Revises:
Create Date: 2026-02-23 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_001"
down_revision = None
branch_labels = ("finance",)
depends_on = None


def upgrade() -> None:
    # --- finance.accounts ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            institution TEXT NOT NULL,
            type        TEXT NOT NULL
                            CHECK (type IN ('checking', 'savings', 'credit', 'investment')),
            name        TEXT,
            last_four   CHAR(4),
            currency    CHAR(3) NOT NULL DEFAULT 'USD',
            metadata    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_accounts_institution
            ON accounts (institution)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_accounts_type
            ON accounts (type)
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_institution_type_last_four
            ON accounts (institution, type, last_four)
            WHERE last_four IS NOT NULL
    """)

    # --- finance.transactions ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            account_id        UUID REFERENCES accounts(id) ON DELETE SET NULL,
            source_message_id TEXT,
            posted_at         TIMESTAMPTZ NOT NULL,
            merchant          TEXT NOT NULL,
            description       TEXT,
            amount            NUMERIC(14, 2) NOT NULL,
            currency          CHAR(3) NOT NULL,
            direction         TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
            category          TEXT NOT NULL,
            payment_method    TEXT,
            receipt_url       TEXT,
            external_ref      TEXT,
            metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_posted_at
            ON transactions (posted_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_merchant
            ON transactions (merchant)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_category
            ON transactions (category)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_account_id
            ON transactions (account_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_source_message_id
            ON transactions (source_message_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_metadata_gin
            ON transactions USING GIN (metadata)
    """)
    # Dedupe partial index: prevents duplicate ingestion from the same source message
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_dedupe
            ON transactions (source_message_id, merchant, amount, posted_at)
            WHERE source_message_id IS NOT NULL
    """)

    # --- finance.subscriptions ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            service           TEXT NOT NULL,
            amount            NUMERIC(14, 2) NOT NULL,
            currency          CHAR(3) NOT NULL,
            frequency         TEXT NOT NULL
                                  CHECK (frequency IN ('weekly', 'monthly', 'quarterly', 'yearly', 'custom')),
            next_renewal      DATE NOT NULL,
            status            TEXT NOT NULL
                                  CHECK (status IN ('active', 'cancelled', 'paused')),
            auto_renew        BOOLEAN NOT NULL DEFAULT true,
            payment_method    TEXT,
            account_id        UUID REFERENCES accounts(id) ON DELETE SET NULL,
            source_message_id TEXT,
            metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_next_renewal
            ON subscriptions (next_renewal)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_status
            ON subscriptions (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_subscriptions_service
            ON subscriptions (service)
    """)

    # --- finance.bills ---
    op.execute("""
        CREATE TABLE IF NOT EXISTS bills (
            id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            payee                  TEXT NOT NULL,
            amount                 NUMERIC(14, 2) NOT NULL,
            currency               CHAR(3) NOT NULL,
            due_date               DATE NOT NULL,
            frequency              TEXT NOT NULL
                                       CHECK (frequency IN (
                                           'one_time', 'weekly', 'monthly',
                                           'quarterly', 'yearly', 'custom'
                                       )),
            status                 TEXT NOT NULL
                                       CHECK (status IN ('pending', 'paid', 'overdue')),
            payment_method         TEXT,
            account_id             UUID REFERENCES accounts(id) ON DELETE SET NULL,
            source_message_id      TEXT,
            statement_period_start DATE,
            statement_period_end   DATE,
            paid_at                TIMESTAMPTZ,
            metadata               JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_bills_due_date
            ON bills (due_date)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_bills_status
            ON bills (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_bills_payee
            ON bills (payee)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_bills_account_id
            ON bills (account_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS bills")
    op.execute("DROP TABLE IF EXISTS subscriptions")
    op.execute("DROP TABLE IF EXISTS transactions")
    op.execute("DROP TABLE IF EXISTS accounts")
