"""Drop the retired ``routing_log.contact_id`` compatibility column.

Revision ID: sw_056
Revises: sw_054

Identity is entity-keyed; ``contact_id`` was never populated and nothing reads
it. The partial index goes first, then the column. Downgrade re-adds the column
nullable plus the index (the column held only NULLs, so no data is restored).
"""

from __future__ import annotations

from alembic import op

revision = "sw_056"
down_revision = "sw_054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_routing_log_contact_id")
    op.execute("ALTER TABLE routing_log DROP COLUMN IF EXISTS contact_id")


def downgrade() -> None:
    op.execute("ALTER TABLE routing_log ADD COLUMN IF NOT EXISTS contact_id UUID")
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_routing_log_contact_id
        ON routing_log (contact_id)
        WHERE contact_id IS NOT NULL
        """
    )
