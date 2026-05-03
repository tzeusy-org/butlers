"""reset_spotify_watermark_for_in_progress_projection

Revision ID: chronicler_012
Revises: chronicler_011
Create Date: 2026-05-03 00:00:02.000000

Reset the ``spotify.session_summary`` projection_checkpoints watermark so
the next chronicler run re-projects all evidence rows under the new
``recorded_at``-based watermark semantics.

Background
----------
``SpotifySessionAdapter`` previously fetched and watermarked on
``started_at``, which is set once at session start and never changes. This
meant in-progress listening sessions (which the connector now upserts on
every active poll, with ``ended_at`` continually advancing) were never
re-projected after their first appearance, leaving the Music lane stuck on
each episode's first observed boundary.

The adapter now fetches and watermarks on ``recorded_at`` so each
connector upsert flows through to ``chronicler.episodes`` via the
idempotent ``upsert_episode``. The connector's
``persist_session_summary`` was switched from ``ON CONFLICT DO NOTHING``
to ``ON CONFLICT DO UPDATE`` and is called from
``_handle_active_playback`` on every active poll, so the Music lane shows
a live-extending bar within ~60s of starting playback instead of waiting
for the 5-minute idle-drain to close the session — and survives
container restart.

Idempotency
-----------
Setting an already-NULL watermark to ``NULL`` is a no-op. Re-running this
migration touches no additional rows. The next ``SpotifySessionAdapter``
run after upgrade will rebuild a fresh ``recorded_at``-based watermark.

Downgrade
---------
No-op; the original watermark is not preserved.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_012"
down_revision = "chronicler_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE projection_checkpoints
        SET watermark    = NULL,
            watermark_id = NULL
        WHERE source_name = 'spotify.session_summary'
        """
    )


def downgrade() -> None:
    pass
