"""Add direction column to message_inbox for bidirectional conversation history.

Revision ID: sw_016
Revises: sw_015
Create Date: 2026-02-18 00:00:00.000000

Migration notes:
- Adds TEXT column 'direction' to message_inbox with default 'inbound'
- 'inbound' = user message received by switchboard (existing rows)
- 'outbound' = butler response delivered via notify()
- Enables history loaders to surface full back-and-forth conversation
- ALTER TABLE on parent partition propagates to all child partitions
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_016"
down_revision = "sw_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE message_inbox
        ADD COLUMN direction TEXT NOT NULL DEFAULT 'inbound'
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN message_inbox.direction IS
        'Message direction: inbound (user → butler) or outbound (butler → user)'
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_message_inbox_thread_direction_received_at
        ON message_inbox (
            (request_context ->> 'source_thread_identity'),
            direction,
            received_at DESC
        )
        WHERE request_context ->> 'source_thread_identity' IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_message_inbox_thread_direction_received_at")
    op.execute(
        """
        ALTER TABLE message_inbox
        DROP COLUMN IF EXISTS direction
        """
    )
