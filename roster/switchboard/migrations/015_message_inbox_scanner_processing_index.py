"""Add an index for DurableBuffer expired processing-row recovery.

Revision ID: sw_015
Revises: sw_014
Create Date: 2026-06-12 00:00:00.000000

The DurableBuffer cold-path scanner periodically reclaims ``message_inbox`` rows
left in ``processing`` after their lock expires.  The canonical table already
has a lifecycle/received_at index for accepted rows, but expired processing
recovery filters by ``updated_at``.  This partial index keeps that sweep
bounded on busy partitioned inbox tables.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_015"
down_revision = "sw_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_inbox_processing_updated_at
        ON message_inbox (updated_at ASC, received_at ASC)
        WHERE lifecycle_state = 'processing'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_inbox_processing_updated_at")
