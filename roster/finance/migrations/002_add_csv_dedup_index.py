"""Add partial unique index for CSV-imported transaction dedup

Revision ID: finance_002
Revises: finance_001
Create Date: 2026-03-28 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "finance_002"
down_revision = "finance_001"
branch_labels = ("finance",)
depends_on = None


def upgrade() -> None:
    """Add partial unique index on (posted_at, amount, merchant, account_id)

    This index ensures reliable ON CONFLICT deduplication for CSV-imported
    transactions (rows without source_message_id). The dedup key is the hash
    of canonical (posted_at, amount, merchant, account_id) but the underlying
    constraint requires an actual unique index on these columns.

    The index is partial (WHERE source_message_id IS NULL) to avoid conflicts
    with the existing email-based dedup index.
    """
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_csv_dedup
            ON transactions (posted_at, amount, merchant, account_id)
            WHERE source_message_id IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_transactions_csv_dedup")
