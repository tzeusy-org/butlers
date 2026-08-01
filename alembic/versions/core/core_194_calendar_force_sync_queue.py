"""Add a durable, serialized queue for dashboard calendar force-sync commands.

Revision ID: core_194
Revises: core_193
Create Date: 2026-08-01 00:00:00.000000

``calendar_action_log`` is schema-local and is absent for butlers that do not
load CalendarModule, so every change is guarded. The queue uses ``running`` as
an explicit lease state and permits one pending successor per calendar owner.
"""

from __future__ import annotations

from alembic import op

revision = "core_194"
down_revision = "core_193"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('calendar_action_log') IS NULL THEN
                RETURN;
            END IF;

            ALTER TABLE calendar_action_log
                DROP CONSTRAINT IF EXISTS calendar_action_log_status_check;
            ALTER TABLE calendar_action_log
                ADD CONSTRAINT calendar_action_log_status_check
                CHECK (action_status IN ('pending', 'running', 'applied', 'failed', 'noop'));

            CREATE UNIQUE INDEX IF NOT EXISTS ix_calendar_action_log_force_sync_pending
                ON calendar_action_log (action_type)
                WHERE action_type = 'calendar_force_sync' AND action_status = 'pending';
            CREATE UNIQUE INDEX IF NOT EXISTS ix_calendar_action_log_force_sync_running
                ON calendar_action_log (action_type)
                WHERE action_type = 'calendar_force_sync' AND action_status = 'running';
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('calendar_action_log') IS NULL THEN
                RETURN;
            END IF;

            UPDATE calendar_action_log
            SET action_status = 'pending', updated_at = now()
            WHERE action_status = 'running';

            DROP INDEX IF EXISTS ix_calendar_action_log_force_sync_pending;
            DROP INDEX IF EXISTS ix_calendar_action_log_force_sync_running;
            ALTER TABLE calendar_action_log
                DROP CONSTRAINT IF EXISTS calendar_action_log_status_check;
            ALTER TABLE calendar_action_log
                ADD CONSTRAINT calendar_action_log_status_check
                CHECK (action_status IN ('pending', 'applied', 'failed', 'noop'));
        END
        $$;
        """
    )
