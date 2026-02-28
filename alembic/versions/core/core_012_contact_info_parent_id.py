"""core_012_contact_info_parent_id

Revision ID: core_012
Revises: core_011
Create Date: 2026-02-28 00:00:00.000000

Add parent_id self-FK to shared.contact_info so credential entries
(email_password, telegram_api_id, telegram_api_hash) can be grouped
under their parent identifier entry.

Backfills unambiguous parent links where exactly one candidate parent
exists per contact.
"""

from __future__ import annotations

from alembic import op

revision = "core_012"
down_revision = "core_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL THEN
                -- Add parent_id column
                ALTER TABLE shared.contact_info
                ADD COLUMN IF NOT EXISTS parent_id UUID
                    REFERENCES shared.contact_info(id) ON DELETE CASCADE;

                -- Partial index for efficient parent lookups
                IF NOT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE indexname = 'ix_shared_contact_info_parent_id'
                ) THEN
                    CREATE INDEX ix_shared_contact_info_parent_id
                    ON shared.contact_info (parent_id)
                    WHERE parent_id IS NOT NULL;
                END IF;

                -- Backfill: email_password -> email (when exactly 1 email per contact)
                UPDATE shared.contact_info child
                SET parent_id = (
                    SELECT p.id FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id
                      AND p.type = 'email'
                )
                WHERE child.type = 'email_password'
                  AND child.parent_id IS NULL
                  AND (
                    SELECT count(*) FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id AND p.type = 'email'
                  ) = 1;

                -- Backfill: telegram -> telegram_chat_id
                UPDATE shared.contact_info child
                SET parent_id = (
                    SELECT p.id FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id
                      AND p.type = 'telegram_chat_id'
                )
                WHERE child.type = 'telegram'
                  AND child.parent_id IS NULL
                  AND (
                    SELECT count(*) FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id AND p.type = 'telegram_chat_id'
                  ) = 1;

                -- Backfill: telegram_api_id -> telegram_chat_id
                UPDATE shared.contact_info child
                SET parent_id = (
                    SELECT p.id FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id
                      AND p.type = 'telegram_chat_id'
                )
                WHERE child.type = 'telegram_api_id'
                  AND child.parent_id IS NULL
                  AND (
                    SELECT count(*) FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id AND p.type = 'telegram_chat_id'
                  ) = 1;

                -- Backfill: telegram_api_hash -> telegram_chat_id
                UPDATE shared.contact_info child
                SET parent_id = (
                    SELECT p.id FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id
                      AND p.type = 'telegram_chat_id'
                )
                WHERE child.type = 'telegram_api_hash'
                  AND child.parent_id IS NULL
                  AND (
                    SELECT count(*) FROM shared.contact_info p
                    WHERE p.contact_id = child.contact_id AND p.type = 'telegram_chat_id'
                  ) = 1;
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
            IF to_regclass('shared.contact_info') IS NOT NULL THEN
                ALTER TABLE shared.contact_info DROP COLUMN IF EXISTS parent_id;
            END IF;
        END
        $$;
        """
    )
