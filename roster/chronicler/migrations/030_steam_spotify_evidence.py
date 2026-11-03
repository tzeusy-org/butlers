"""Route Steam and Spotify source history through situational fusion (bu-r3fmja.7).

Revision ID: chronicler_030
Revises: chronicler_029

Preserve row identities, provenance, corrections and tombstones; change only the
base layer. Daily Steam totals remain fallback evidence. Spotify stays useful as
a quiet-run breaker but never counts as Play. Downgrade preserves evidence:
restoring automatic counted rows would double-count existing derived blocks.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_030"
down_revision = "chronicler_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE episodes SET layer = 'evidence', updated_at = now()
        WHERE (source_name = 'steam.play_history' AND episode_type = 'play_episode')
           OR (source_name = 'spotify.session_summary' AND episode_type = 'listening_episode')
    """)

    # One initial replay rebuilds historical fallback Play from evidence, not
    # just the adapter's normal three-day trailing window.
    op.execute("""
        UPDATE projection_checkpoints SET watermark = NULL, watermark_id = NULL
        WHERE source_name = 'chronicler.situation_inferred'
    """)


def downgrade() -> None:
    pass
