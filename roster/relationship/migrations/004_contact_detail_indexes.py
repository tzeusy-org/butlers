"""contact_detail_indexes

Add missing indexes on columns used by the contact detail and list endpoints.
These tables were queried by contact_id without dedicated indexes, causing
sequential scans on every contact page load.

Revision ID: rel_004
Revises: rel_003
Create Date: 2026-03-31 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_004"
down_revision = "rel_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contact_labels_contact_id
            ON contact_labels (contact_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_important_dates_contact_id
            ON important_dates (contact_id)
    """)
    # Compound index for the email/phone correlated subqueries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_contact_info_contact_type
            ON public.contact_info (contact_id, type)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_contact_labels_contact_id")
    op.execute("DROP INDEX IF EXISTS idx_important_dates_contact_id")
    op.execute("DROP INDEX IF EXISTS public.idx_contact_info_contact_type")
