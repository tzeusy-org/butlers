"""rel_009_calendar_event_id_to_text

Revision ID: rel_009
Revises: rel_008
Create Date: 2026-02-26 00:00:00.000000

Change reminders.calendar_event_id from UUID to TEXT so it can store
Google Calendar event IDs (26-char base32 strings) which are not valid UUIDs.
"""

from __future__ import annotations

from alembic import op

revision = "rel_009"
down_revision = "rel_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reminders_calendar_event_id")
    op.execute(
        "ALTER TABLE reminders ALTER COLUMN calendar_event_id TYPE TEXT USING calendar_event_id::text"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_reminders_calendar_event_id
        ON reminders (calendar_event_id)
        WHERE calendar_event_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_reminders_calendar_event_id")
    op.execute(
        "ALTER TABLE reminders ALTER COLUMN calendar_event_id TYPE UUID USING calendar_event_id::uuid"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_reminders_calendar_event_id
        ON reminders (calendar_event_id)
        WHERE calendar_event_id IS NOT NULL
        """
    )
