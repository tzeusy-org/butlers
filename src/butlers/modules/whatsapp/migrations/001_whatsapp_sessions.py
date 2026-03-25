"""whatsapp_sessions

Revision ID: whatsapp_001
Revises:
Create Date: 2026-03-25 00:00:00.000000

Creates the ``whatsapp_sessions`` table used by the WhatsApp module to persist
device pairing state across restarts.  One row per paired phone number; the
session_data JSONB column holds whatsmeow's serialized session blob.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "whatsapp_001"
down_revision = None
branch_labels = ("whatsapp",)
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS whatsapp_sessions (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            phone_number  TEXT        NOT NULL UNIQUE,
            device_id     TEXT,
            session_data  JSONB,
            paired_at     TIMESTAMPTZ,
            last_seen_at  TIMESTAMPTZ,
            active        BOOLEAN     NOT NULL DEFAULT true
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_whatsapp_sessions_active
            ON whatsapp_sessions (active)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_whatsapp_sessions_active")
    op.execute("DROP TABLE IF EXISTS whatsapp_sessions")
