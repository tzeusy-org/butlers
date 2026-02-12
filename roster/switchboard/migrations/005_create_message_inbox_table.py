"""
Create message_inbox table
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_005"
down_revision = "sw_004"
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE message_inbox (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_channel TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            raw_content TEXT NOT NULL,
            raw_metadata JSONB DEFAULT '{}',
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            classification JSONB,
            classified_at TIMESTAMPTZ,
            classification_duration_ms INTEGER,
            routing_results JSONB,
            response_summary TEXT,
            completed_at TIMESTAMPTZ,
            trace_id TEXT,
            session_id UUID
        )
    """)
    op.execute(
        """
        CREATE INDEX ix_message_inbox_source_channel_received_at
        ON message_inbox (source_channel, received_at DESC);
        """
    )
    op.execute(
        """
        CREATE INDEX ix_message_inbox_sender_id_received_at
        ON message_inbox (sender_id, received_at DESC);
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS message_inbox")
