"""Persist the content-blind purpose lane on session admission.

Revision ID: core_233
Revises: core_232
Create Date: 2026-09-13 00:00:00.000000

Legacy rows remain NULL. New session admissions write one closed value.
Downgrade refuses to discard recorded lane evidence.
"""

from __future__ import annotations

from alembic import op

revision = "core_233"
down_revision = "core_232"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_sessions_purpose_lane"


def upgrade() -> None:
    op.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS purpose_lane TEXT")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                 WHERE conrelid = 'sessions'::regclass
                   AND conname = '{_CONSTRAINT}'
            ) THEN
                ALTER TABLE sessions ADD CONSTRAINT {_CONSTRAINT}
                CHECK (purpose_lane IS NULL OR purpose_lane IN ('standard', 'private_content'));
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
            IF EXISTS (SELECT 1 FROM sessions WHERE purpose_lane IS NOT NULL) THEN
                RAISE EXCEPTION 'cannot downgrade core_233 while session purpose evidence exists';
            END IF;
        END
        $$
        """
    )
    op.execute(f"ALTER TABLE sessions DROP CONSTRAINT IF EXISTS {_CONSTRAINT}")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS purpose_lane")
