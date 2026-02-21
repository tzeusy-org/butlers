"""add dispatch_mode columns to existing scheduled_tasks

Revision ID: core_002
Revises: core_001
Create Date: 2026-02-22 00:00:00.000000

Existing databases created before core_001 may have scheduled_tasks without
the dispatch_mode, job_name, and job_args columns. core_001 uses
CREATE TABLE IF NOT EXISTS which skips creation when the table already exists.
This migration adds the missing columns and constraints idempotently.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_002"
down_revision = "core_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add missing columns idempotently.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks' AND column_name = 'dispatch_mode'
            ) THEN
                ALTER TABLE scheduled_tasks
                    ADD COLUMN dispatch_mode TEXT NOT NULL DEFAULT 'prompt';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks' AND column_name = 'job_name'
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN job_name TEXT;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks' AND column_name = 'job_args'
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN job_args JSONB;
            END IF;
        END
        $$;
        """
    )

    # Add check constraints idempotently.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'scheduled_tasks_dispatch_mode_check'
                  AND table_name = 'scheduled_tasks'
            ) THEN
                ALTER TABLE scheduled_tasks
                    ADD CONSTRAINT scheduled_tasks_dispatch_mode_check
                    CHECK (dispatch_mode IN ('prompt', 'job'));
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'scheduled_tasks_dispatch_payload_check'
                  AND table_name = 'scheduled_tasks'
            ) THEN
                ALTER TABLE scheduled_tasks
                    ADD CONSTRAINT scheduled_tasks_dispatch_payload_check
                    CHECK (
                        (dispatch_mode = 'prompt' AND prompt IS NOT NULL AND job_name IS NULL)
                        OR (dispatch_mode = 'job' AND job_name IS NOT NULL)
                    );
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE scheduled_tasks
            DROP CONSTRAINT IF EXISTS scheduled_tasks_dispatch_payload_check;
        ALTER TABLE scheduled_tasks
            DROP CONSTRAINT IF EXISTS scheduled_tasks_dispatch_mode_check;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS job_args;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS job_name;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS dispatch_mode;
        """
    )
