"""
Add composite index on (source_thread_identity, received_at DESC) for conversation history queries
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_013"
down_revision = "sw_012"
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_inbox_thread_identity_received_at
        ON message_inbox (source_thread_identity, received_at DESC)
        WHERE source_thread_identity IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_inbox_thread_identity_received_at")
