"""Preserve read-side calendar event type and declared working location.

Revision ID: core_262
Revises: core_261
Create Date: 2026-10-07 00:00:00.000000

Core replay uses each target schema's calendar projection. Defaults retain
legacy/default-family behavior; future provider types are not an enum CHECK.
Downgrade intentionally retains the columns and their provider evidence while
returning the Alembic marker to the predecessor. It does not reproduce the old
physical schema or make the predecessor runtime understand typed OOO context.
No roles, grants, bootstrap or provider write contracts change.
"""

from __future__ import annotations

from alembic import op

revision = "core_262"
down_revision = "core_261"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE calendar_events
            ADD COLUMN IF NOT EXISTS event_type TEXT NOT NULL DEFAULT 'default',
            ADD COLUMN IF NOT EXISTS working_location JSONB
    """)
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'calendar_events'::regclass
                  AND conname = 'calendar_events_working_location_object'
            ) THEN
                ALTER TABLE calendar_events ADD CONSTRAINT calendar_events_working_location_object
                    CHECK (working_location IS NULL OR jsonb_typeof(working_location) = 'object');
            END IF;
        END $$
    """)


def downgrade() -> None:
    """Retain typed source evidence; only Alembic's revision marker changes."""
    pass
