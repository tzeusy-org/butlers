"""Allow historical WhatsApp session rows while keeping one active row.

Revision ID: whatsapp_002
Revises: whatsapp_001
Create Date: 2026-07-16 00:00:00.000000

``Store.SaveNew`` deactivates an existing session before inserting the newly
paired device.  The original migration's global phone-number constraint still
rejected that insert, even though the old row was inactive.  Replace it with
the active-row partial uniqueness used by the bridge's fallback schema.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "whatsapp_002"
down_revision = "whatsapp_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE whatsapp_sessions
            DROP CONSTRAINT IF EXISTS whatsapp_sessions_phone_number_key
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_sessions_active_phone
            ON whatsapp_sessions (phone_number)
            WHERE active = true
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_whatsapp_sessions_active_phone")
    # Restoring global uniqueness requires collapsing rotation history. Keep
    # the active row when present, otherwise the most recently paired row.
    op.execute("""
        DELETE FROM whatsapp_sessions
        WHERE id IN (
            SELECT id
            FROM (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY phone_number
                        ORDER BY active DESC, paired_at DESC NULLS LAST, id
                    ) AS row_rank
                FROM whatsapp_sessions
            ) ranked
            WHERE row_rank > 1
        )
    """)
    op.execute("""
        ALTER TABLE whatsapp_sessions
            ADD CONSTRAINT whatsapp_sessions_phone_number_key UNIQUE (phone_number)
    """)
