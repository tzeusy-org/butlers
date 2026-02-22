"""extend scheduled_tasks with calendar linkage fields

Revision ID: core_006
Revises: core_005
Create Date: 2026-02-22 00:00:00.000000

Adds scheduler fields required for calendar projection linkage:
- timezone, start_at, end_at, until_at
- display_title
- calendar_event_id

Uses current_schema()-scoped metadata checks for one-db schema deployments.
"""

from __future__ import annotations

from alembic import op

revision = "core_006"
down_revision = "core_005"
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
                  AND column_name = 'timezone'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN timezone TEXT DEFAULT 'UTC';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'start_at'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN start_at TIMESTAMPTZ;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'end_at'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN end_at TIMESTAMPTZ;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'until_at'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN until_at TIMESTAMPTZ;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'display_title'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN display_title TEXT;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'scheduled_tasks'
                  AND column_name = 'calendar_event_id'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks ADD COLUMN calendar_event_id UUID;
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        UPDATE scheduled_tasks
        SET timezone = 'UTC'
        WHERE timezone IS NULL;

        ALTER TABLE scheduled_tasks
            ALTER COLUMN timezone SET DEFAULT 'UTC';
        ALTER TABLE scheduled_tasks
            ALTER COLUMN timezone SET NOT NULL;
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'scheduled_tasks_window_bounds_check'
                  AND table_name = 'scheduled_tasks'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks
                    ADD CONSTRAINT scheduled_tasks_window_bounds_check
                    CHECK (start_at IS NULL OR end_at IS NULL OR end_at > start_at);
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'scheduled_tasks_until_bounds_check'
                  AND table_name = 'scheduled_tasks'
                  AND table_schema = current_schema()
            ) THEN
                ALTER TABLE scheduled_tasks
                    ADD CONSTRAINT scheduled_tasks_until_bounds_check
                    CHECK (until_at IS NULL OR start_at IS NULL OR until_at >= start_at);
            END IF;
        END
        $$;
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_calendar_event_id
        ON scheduled_tasks (calendar_event_id)
        WHERE calendar_event_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP INDEX IF EXISTS ix_scheduled_tasks_calendar_event_id;

        ALTER TABLE scheduled_tasks
            DROP CONSTRAINT IF EXISTS scheduled_tasks_until_bounds_check;
        ALTER TABLE scheduled_tasks
            DROP CONSTRAINT IF EXISTS scheduled_tasks_window_bounds_check;

        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS calendar_event_id;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS display_title;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS until_at;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS end_at;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS start_at;
        ALTER TABLE scheduled_tasks
            DROP COLUMN IF EXISTS timezone;
        """
    )
