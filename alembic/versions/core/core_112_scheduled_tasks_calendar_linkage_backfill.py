"""scheduled_tasks_calendar_linkage_backfill.

Revision ID: core_112
Revises: core_111
Create Date: 2026-05-24 00:00:00.000000

Fresh installs create scheduled_tasks with calendar-linkage columns in
core_001. Existing deployments whose scheduled_tasks table predated that
baseline edit need an explicit backfill migration, otherwise the scheduler
tick can reference columns that are absent in long-lived schemas.
"""

from __future__ import annotations

from alembic import op

revision = "core_112"
down_revision = "core_111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            target_schema text := current_schema();
            table_ref regclass := to_regclass(format('%I.scheduled_tasks', target_schema));
        BEGIN
            IF table_ref IS NULL THEN
                RETURN;
            END IF;

            EXECUTE format(
                'ALTER TABLE IF EXISTS %I.scheduled_tasks '
                'ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT ''UTC'', '
                'ADD COLUMN IF NOT EXISTS start_at TIMESTAMPTZ, '
                'ADD COLUMN IF NOT EXISTS end_at TIMESTAMPTZ, '
                'ADD COLUMN IF NOT EXISTS until_at TIMESTAMPTZ, '
                'ADD COLUMN IF NOT EXISTS display_title TEXT, '
                'ADD COLUMN IF NOT EXISTS calendar_event_id TEXT',
                target_schema
            );

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = table_ref
                  AND conname = 'scheduled_tasks_window_bounds_check'
            ) THEN
                EXECUTE format(
                    'ALTER TABLE %I.scheduled_tasks '
                    'ADD CONSTRAINT scheduled_tasks_window_bounds_check '
                    'CHECK (start_at IS NULL OR end_at IS NULL OR end_at > start_at)',
                    target_schema
                );
            END IF;

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = table_ref
                  AND conname = 'scheduled_tasks_until_bounds_check'
            ) THEN
                EXECUTE format(
                    'ALTER TABLE %I.scheduled_tasks '
                    'ADD CONSTRAINT scheduled_tasks_until_bounds_check '
                    'CHECK (until_at IS NULL OR start_at IS NULL OR until_at >= start_at)',
                    target_schema
                );
            END IF;

            EXECUTE format(
                'CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_calendar_event_id '
                'ON %I.scheduled_tasks (calendar_event_id) '
                'WHERE calendar_event_id IS NOT NULL',
                target_schema
            );
        END
        $$;
        """
    )


def downgrade() -> None:
    """Intentionally preserve baseline scheduled_tasks projection columns.

    This revision repairs schemas that missed the current core_001 table
    shape. Dropping these columns on downgrade would move the database away
    from the baseline scheduler contract and can break runtime code.
    """
    pass
