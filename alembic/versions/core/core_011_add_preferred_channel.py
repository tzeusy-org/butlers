"""core_011_add_preferred_channel

Revision ID: core_011
Revises: core_010
Create Date: 2026-02-28 00:00:00.000000

Add preferred_channel column to shared.contacts so agents can query a
contact's preferred notification channel for use with notify().
"""

from __future__ import annotations

from alembic import op

revision = "core_011"
down_revision = "core_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL THEN
                ALTER TABLE shared.contacts
                ADD COLUMN IF NOT EXISTS preferred_channel VARCHAR
                CONSTRAINT contacts_preferred_channel_check
                CHECK (preferred_channel IN ('telegram', 'email'));
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL THEN
                ALTER TABLE shared.contacts DROP COLUMN IF EXISTS preferred_channel;
            END IF;
        END
        $$;
        """
    )
