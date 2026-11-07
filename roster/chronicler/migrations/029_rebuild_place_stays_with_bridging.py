"""rebuild_place_stays_with_bridging

Revision ID: chronicler_029
Revises: chronicler_028
Create Date: 2026-11-03 00:00:00.000000

Place clustering now bridges silent OwnTracks reporting gaps and projects
``place_episode`` rows as ``layer=evidence`` (bu-r3fmja,
REQ-butler-chronicler-014). Stored stays were cut at every gap over 60 minutes
and counted as Rest wherever they were, so they are rebuilt:

- every live ``owntracks.place_cluster`` ``place_episode`` is tombstoned;
- the adapter's watermark and carryover are cleared, so the next run
  re-clusters all retained evidence with bridging. A stay re-derived with the
  same start reuses its ``source_ref``, and the upsert clears its tombstone.

``away_episode`` rows are untouched: they re-upsert on the same ``source_ref``.
Downgrade is a no-op: the tombstones are recoverable history, and re-running
the projection is the repair either way.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_029"
down_revision = "chronicler_028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE episodes
        SET tombstone_at = now(),
            tombstone_reason = 'bu-r3fmja: rebuilt with sparse-report bridging'
        WHERE source_name = 'owntracks.place_cluster'
          AND episode_type = 'place_episode'
          AND tombstone_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE projection_checkpoints
        SET watermark = NULL,
            watermark_id = NULL,
            carryover = NULL,
            updated_at = now()
        WHERE source_name = 'owntracks.place_cluster'
        """
    )


def downgrade() -> None:
    pass
