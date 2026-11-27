"""Fence owner Gmail sends without retaining recipients or message content.

Revision ID: msg_005
Revises: msg_004
"""

from alembic import op

revision = "msg_005"
down_revision = "msg_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS gmail_owner_sends (
            effect_id UUID PRIMARY KEY,
            account TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            message_id TEXT NOT NULL UNIQUE,
            call_digest TEXT NOT NULL CHECK (call_digest ~ '^[0-9a-f]{64}$'),
            gmail_message_id TEXT,
            state TEXT NOT NULL CHECK (state IN ('dispatching', 'sent', 'failed', 'unknown')),
            draft_cleanup TEXT CHECK (draft_cleanup IN ('draft_removed', 'draft_kept_changed', 'draft_kept_unavailable')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CHECK ((state = 'sent') = (gmail_message_id IS NOT NULL))
        )
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('gmail_owner_sends') IS NOT NULL THEN
                LOCK TABLE gmail_owner_sends IN ACCESS EXCLUSIVE MODE;
                IF EXISTS (SELECT 1 FROM gmail_owner_sends) THEN
                    RAISE EXCEPTION 'Cannot downgrade msg_005 while Gmail send receipts exist';
                END IF;
                DROP TABLE gmail_owner_sends;
            END IF;
        END $$
    """)
