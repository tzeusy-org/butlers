"""create_mailbox_table

Revision ID: mailbox_001
Revises:
Create Date: 2026-02-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mailbox_001"
down_revision = None
branch_labels = ("mailbox",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS mailbox (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sender TEXT NOT NULL,
            sender_channel TEXT NOT NULL,
            subject TEXT,
            body TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'unread',
            metadata JSONB NOT NULL DEFAULT '{}',
            read_at TIMESTAMPTZ,
            actioned_at TIMESTAMPTZ,
            archived_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mailbox_status ON mailbox (status)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mailbox_sender ON mailbox (sender)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mailbox_created_at ON mailbox (created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mailbox")
