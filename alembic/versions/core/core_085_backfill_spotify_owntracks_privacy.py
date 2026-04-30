"""backfill_spotify_owntracks_privacy: reclassify spotify episodes to normal.

Revision ID: core_085
Revises: core_084
Create Date: 2026-04-30 00:00:00.000000

Privacy contract fix (bu-6c5i6):
- Spotify session-summary episodes were created with privacy='sensitive'
  by an earlier version of SpotifySessionAdapter.  Track names and session
  duration are not sensitive data; the blanket sensitive class was causing
  the Music lane to render as opaque placeholders on the dashboard.
  Backfill: SET privacy='normal' WHERE source_name='spotify.session_summary'.

- OwnTracks point events and movement episodes remain privacy='sensitive'
  (GPS coordinates ARE personally identifying).  No change needed.

The backfill targets only canonical episode rows with the old value.
The UPDATE is idempotent: re-running it when rows already have
privacy='normal' is a no-op.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_085"
down_revision = "core_084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Reclassify existing Spotify session-summary episodes from sensitive to normal.

    Guards with an existence check so the migration is safe when the chronicler
    schema is absent (e.g. test databases that run only the core chain without
    the chronicler butler chain).  Follows the same pattern as core_080.
    """
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'episodes'
            ) THEN
                UPDATE chronicler.episodes
                SET privacy = 'normal'
                WHERE source_name = 'spotify.session_summary'
                  AND privacy = 'sensitive';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    """Reverse: reclassify Spotify session-summary episodes back to sensitive.

    This restores the pre-bu-6c5i6 state for rollback purposes.
    """
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'chronicler' AND table_name = 'episodes'
            ) THEN
                UPDATE chronicler.episodes
                SET privacy = 'sensitive'
                WHERE source_name = 'spotify.session_summary'
                  AND privacy = 'normal';
            END IF;
        END
        $$;
    """)
