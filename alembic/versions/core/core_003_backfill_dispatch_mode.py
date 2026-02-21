"""backfill dispatch_mode for schema-scoped deployments

Revision ID: core_003
Revises: core_002
Create Date: 2026-02-22 00:00:00.000000

core_002 may have been a no-op on multi-schema deployments because the
information_schema lookups were not scoped to current_schema() and could
match columns in a sibling schema. This revision repeats the same ALTER
TABLE logic with the corrected current_schema() filter.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_003"
down_revision = "core_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'dispatch_mode'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks
                    ADD COLUMN dispatch_mode TEXT NOT NULL DEFAULT 'prompt';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'job_name'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN job_name TEXT;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'job_args'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN job_args JSONB;
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'scheduled_tasks_dispatch_mode_check'
                  AND table_name = 'scheduled_tasks'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks
                    ADD CONSTRAINT scheduled_tasks_dispatch_mode_check
                    CHECK (dispatch_mode IN ('prompt', 'job'));
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'scheduled_tasks_dispatch_payload_check'
                  AND table_name = 'scheduled_tasks'
                  AND table_schema = current_schema()
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
    # core_002 downgrade already handles column/constraint removal.
    pass
