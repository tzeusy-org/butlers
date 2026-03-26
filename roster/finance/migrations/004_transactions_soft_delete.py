"""transactions_soft_delete — add deleted_at column to transactions table.

Revision ID: finance_004
Revises: finance_003
Create Date: 2026-03-26 00:00:00.000000

Adds:
  - finance.transactions.deleted_at : TIMESTAMPTZ nullable column.  NULL means
    the transaction is active; non-NULL means it has been soft-deleted by
    delete_transaction(), merge_duplicates(), or split_transaction().

  - A partial index on (posted_at DESC) WHERE deleted_at IS NULL speeds up
    the common case of querying only active transactions.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_004"
down_revision = "finance_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_transactions_active_posted_at
            ON transactions (posted_at DESC)
            WHERE deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_transactions_active_posted_at")
    op.execute("ALTER TABLE transactions DROP COLUMN IF EXISTS deleted_at")
