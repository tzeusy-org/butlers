"""Drop superseded transaction dedup indexes.

Revision ID: finance_011
Revises: finance_010
Create Date: 2026-06-21 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_011"
down_revision = "finance_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Remove broad legacy dedup indexes superseded by finance_006 tiers."""
    op.execute("DROP INDEX IF EXISTS uq_transactions_composite_dedup")
    op.execute("DROP INDEX IF EXISTS uq_transactions_csv_dedup")


def downgrade() -> None:
    """Recreate legacy indexes for downgrade compatibility."""
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_composite_dedup
            ON transactions (account_id, posted_at, amount, merchant)
            NULLS NOT DISTINCT
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_csv_dedup
            ON transactions (posted_at, amount, merchant, account_id)
            WHERE source_message_id IS NULL
    """)
