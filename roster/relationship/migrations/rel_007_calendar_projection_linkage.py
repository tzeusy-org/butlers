"""rel_007_calendar_projection_linkage

Revision ID: rel_007
Revises: rel_006
Create Date: 2026-02-22 00:00:00.000000

Standardizes reminder projection fields used by calendar workspace materialization.
"""

from __future__ import annotations

from alembic import op

revision = "rel_007"
down_revision = "rel_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS timezone TEXT DEFAULT 'UTC'")
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS until_at TIMESTAMPTZ")
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ")
    op.execute("ALTER TABLE reminders ADD COLUMN IF NOT EXISTS calendar_event_id UUID")

    op.execute(
        """
        UPDATE reminders
        SET timezone = COALESCE(timezone, 'UTC'),
            next_trigger_at = COALESCE(next_trigger_at, due_at),
            updated_at = COALESCE(updated_at, now())
        """
    )

    op.execute("ALTER TABLE reminders ALTER COLUMN timezone SET DEFAULT 'UTC'")
    op.execute("ALTER TABLE reminders ALTER COLUMN timezone SET NOT NULL")
    op.execute("ALTER TABLE reminders ALTER COLUMN updated_at SET DEFAULT now()")
    op.execute("ALTER TABLE reminders ALTER COLUMN updated_at SET NOT NULL")

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_reminders_calendar_event_id
        ON reminders (calendar_event_id)
        WHERE calendar_event_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reminders_calendar_event_id")
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS calendar_event_id")
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS updated_at")
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS until_at")
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS timezone")
