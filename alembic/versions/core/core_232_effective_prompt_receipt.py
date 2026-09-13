"""Persist the exact effective system prompt and its provenance receipt.

Revision ID: core_232
Revises: core_231
Create Date: 2026-09-13 00:00:00.000000

The columns are nullable as a group so historical sessions retain their
original meaning. New Spawner-created sessions populate all three atomically.
Rollback refuses to discard any captured receipt evidence.
"""

from __future__ import annotations

from alembic import op

revision = "core_232"
down_revision = "core_231"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_sessions_effective_prompt_receipt_complete"


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE sessions
            ADD COLUMN IF NOT EXISTS effective_system_prompt TEXT,
            ADD COLUMN IF NOT EXISTS prompt_digest TEXT,
            ADD COLUMN IF NOT EXISTS prompt_provenance JSONB
        """
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'sessions'::regclass
                  AND conname = '{_CONSTRAINT}'
            ) THEN
                ALTER TABLE sessions
                ADD CONSTRAINT {_CONSTRAINT}
                CHECK (
                    (
                        effective_system_prompt IS NULL
                        AND prompt_digest IS NULL
                        AND prompt_provenance IS NULL
                    )
                    OR
                    (
                        effective_system_prompt IS NOT NULL
                        AND prompt_digest ~ '^[0-9a-f]{{64}}$'
                        AND prompt_provenance IS NOT NULL
                        AND jsonb_typeof(prompt_provenance) = 'array'
                    )
                );
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM sessions
                WHERE effective_system_prompt IS NOT NULL
                   OR prompt_digest IS NOT NULL
                   OR prompt_provenance IS NOT NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade core_232 while effective prompt receipts exist';
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE sessions DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute(
        """
        ALTER TABLE sessions
            DROP COLUMN IF EXISTS prompt_provenance,
            DROP COLUMN IF EXISTS prompt_digest,
            DROP COLUMN IF EXISTS effective_system_prompt
        """
    )
