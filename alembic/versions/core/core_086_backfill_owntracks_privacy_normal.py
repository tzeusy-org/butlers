"""backfill_owntracks_privacy_normal: reclassify owntracks rows to normal.

Revision ID: core_086
Revises: core_085
Create Date: 2026-05-01 00:00:00.000000

Privacy contract update for owner-facing dashboard:
- ``owntracks.points`` location point events and ``movement_episode`` rows
  were previously created with ``privacy='sensitive'`` (see core_085 note).
  The Chronicles dashboard is the owner's own view of their own location
  history — masking the envelope and excluding markers from the map made
  the Travel lane and Map widget effectively useless to the only viewer.
- This backfill reclassifies existing rows to ``privacy='normal'`` so the
  owner can see their own travel data in full. The matching adapter change
  in ``OwnTracksPointAdapter`` lands the same default for new ingestion.
- Future per-recipient masking (e.g. shared dashboards or screenshots)
  should be reintroduced via an explicit user-toggle, per the
  ``Map Render Privacy Contract`` requirement.

Idempotent: re-running when rows already have ``privacy='normal'`` is a no-op.
"""

from __future__ import annotations

from alembic import op

revision = "core_086"
down_revision = "core_085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Reclassify existing OwnTracks rows from sensitive to normal."""
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'episodes'
            ) THEN
                UPDATE chronicler.episodes
                SET privacy = 'normal'
                WHERE source_name = 'owntracks.points'
                  AND privacy = 'sensitive';
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'point_events'
            ) THEN
                UPDATE chronicler.point_events
                SET privacy = 'normal'
                WHERE source_name = 'owntracks.points'
                  AND privacy = 'sensitive';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    """Reverse: reclassify OwnTracks rows back to sensitive."""
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'episodes'
            ) THEN
                UPDATE chronicler.episodes
                SET privacy = 'sensitive'
                WHERE source_name = 'owntracks.points'
                  AND privacy = 'normal';
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'point_events'
            ) THEN
                UPDATE chronicler.point_events
                SET privacy = 'sensitive'
                WHERE source_name = 'owntracks.points'
                  AND privacy = 'normal';
            END IF;
        END
        $$;
    """)
