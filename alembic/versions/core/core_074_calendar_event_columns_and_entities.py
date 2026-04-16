"""calendar: add source_butler, source_session_id, body columns and calendar_event_entities table.

Revision ID: core_074
Revises: core_073
Create Date: 2026-04-16 00:00:00.000000

Phase 1 of the reminders-as-calendar-native-events change.

Schema changes:
  - calendar_events: add source_butler TEXT NOT NULL (default 'unknown' during migration)
  - calendar_events: add source_session_id TEXT (nullable)
  - calendar_events: add body TEXT (nullable, longer description to complement title)
  - calendar_events: backfill title from metadata->>'title' or metadata->>'display_title'
  - calendar_events: drop DEFAULT on source_butler after backfill
  - calendar_event_entities: new junction table (event_id, entity_id)
  - idx_calendar_event_entities_entity: index on entity_id for reverse lookups
  - ix_calendar_events_source_butler: index on source_butler for butler-scoped queries
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_074"
down_revision = "core_073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Phase 1a: Add new columns to calendar_events
    # -------------------------------------------------------------------------
    # source_butler: WITH DEFAULT 'unknown' so backfill can complete safely
    # before we make it strict.
    op.execute("""
        ALTER TABLE calendar_events
            ADD COLUMN IF NOT EXISTS source_butler TEXT NOT NULL DEFAULT 'unknown',
            ADD COLUMN IF NOT EXISTS source_session_id TEXT,
            ADD COLUMN IF NOT EXISTS body TEXT
    """)

    # -------------------------------------------------------------------------
    # Phase 1b: Backfill title from metadata for any rows with empty/bare title
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE calendar_events
        SET title = COALESCE(
            NULLIF(metadata->>'title', ''),
            NULLIF(metadata->>'display_title', ''),
            '(untitled)'
        )
        WHERE btrim(title) = ''
    """)

    # -------------------------------------------------------------------------
    # Phase 1c: Backfill source_butler from metadata for butler-generated events
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE calendar_events
        SET source_butler = COALESCE(
            NULLIF(btrim(metadata->>'butler_name'), ''),
            'unknown'
        )
        WHERE source_butler = 'unknown'
          AND metadata ? 'butler_name'
    """)

    # -------------------------------------------------------------------------
    # Phase 1d: Drop the DEFAULT from source_butler so new rows must supply it
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE calendar_events
            ALTER COLUMN source_butler DROP DEFAULT
    """)

    # -------------------------------------------------------------------------
    # Phase 1e: Index on calendar_events(source_butler)
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_calendar_events_source_butler
        ON calendar_events (source_butler)
    """)

    # -------------------------------------------------------------------------
    # Phase 2: calendar_event_entities junction table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS calendar_event_entities (
            event_id  UUID NOT NULL REFERENCES calendar_events(id) ON DELETE CASCADE,
            entity_id UUID NOT NULL REFERENCES public.entities(id) ON DELETE CASCADE,
            PRIMARY KEY (event_id, entity_id)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_calendar_event_entities_entity
        ON calendar_event_entities (entity_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_calendar_event_entities_entity")
    op.execute("DROP TABLE IF EXISTS calendar_event_entities")

    op.execute("DROP INDEX IF EXISTS ix_calendar_events_source_butler")

    op.execute("""
        ALTER TABLE calendar_events
            DROP COLUMN IF EXISTS body,
            DROP COLUMN IF EXISTS source_session_id,
            DROP COLUMN IF EXISTS source_butler
    """)
