"""reset_steam_spotify_watermarks

Revision ID: chronicler_011
Revises: chronicler_010
Create Date: 2026-05-03 00:00:01.000000

Reset the per-source ``projection_checkpoints`` watermarks for the
``steam.play_history`` and ``spotify.session_summary`` adapters so the
next chronicler run re-projects all evidence rows under the new
title/anchor logic.

Background
----------
Two unrelated chronicler adapter improvements rely on a re-projection
to take effect for episodes already in the database:

- ``SteamPlayAdapter`` now anchors a ``play_episode``'s end-of-day to
  the connector's ``recorded_at`` instead of always parking the bar at
  midnight UTC. Existing rows in ``chronicler.episodes`` were written
  before the anchor logic shipped and still start at ``00:00``.

- ``SpotifySessionAdapter`` now falls back to ``track_names`` when an
  upstream playback session has no ``context_name``/``context_uri``,
  producing titles like ``Listened to Beneath the Blazing Sky`` instead
  of ``Spotify session (spotify:tzeusii)``.

Both adapters write idempotent ``source_ref`` keys, so a watermark reset
followed by the next scheduled adapter tick is safe — every existing
episode is upserted in place with the corrected ``start_at`` / ``title``.

Idempotency
-----------
Setting an already-NULL watermark to ``NULL`` is a no-op. Re-running the
migration touches no additional rows.

Downgrade
---------
No-op; the original watermark values are not preserved. The next adapter
run after upgrade will rebuild a fresh watermark.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_011"
down_revision = "chronicler_010"
branch_labels = None
depends_on = None


_SOURCES_TO_RESET = (
    "steam.play_history",
    "spotify.session_summary",
)


def upgrade() -> None:
    in_list = ", ".join(f"'{name}'" for name in _SOURCES_TO_RESET)
    op.execute(f"""
        UPDATE projection_checkpoints
        SET watermark    = NULL,
            watermark_id = NULL
        WHERE source_name IN ({in_list})
    """)


def downgrade() -> None:
    pass
