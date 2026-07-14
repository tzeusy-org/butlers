"""daily_rollup_butler_ops_lane

Revision ID: chronicler_021
Revises: chronicler_020
Create Date: 2026-07-14 00:00:00.000000

bu-whhll.14 (epic bu-whhll). Split the ``work`` Activity lane into two honest
lanes: ``work`` (the OWNER's occupation) and a new ``butler_ops`` lane (the
butlers' own LLM sessions — conversations/tasks). The owner's workday and the
butlers' cron chatter must not share a slice; mixing them is exactly the
fabricated-signal problem the chronicles surface exists to avoid.

``aggregations.LANES`` is materialized as a DB CHECK on ``daily_rollups.lane``
(chronicler_019) and the rollup job writes one row per lane, so adding the
``butler_ops`` lane requires extending that CHECK before the materializer can
persist it. This is a drop+recreate of the inline column CHECK with the 9-lane
set — the same convention chronicler_018 used for its ``dow_mask`` bound. It is
additive (no lane removed) and needs no backfill: every existing ``lane='work'``
row stays valid; occupation-vs-butler rows only diverge going forward.

The inline column CHECK created in chronicler_019 gets Postgres's default name
``daily_rollups_lane_check``; drop it by that name (IF EXISTS, defensively) and
re-add an explicitly-named constraint with the same name and the widened set.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_021"
down_revision = "chronicler_020"
branch_labels = None
depends_on = None

# Mirrors aggregations.LANES exactly (9 lanes as of bu-whhll.14). Duplicated
# here, not imported, per the migration-chain convention (chronicler_019).
_LANES = (
    "sleep",
    "exercise",
    "work",
    "butler_ops",
    "play",
    "social",
    "travel",
    "eat",
    "rest",
)
_PREV_LANES = ("sleep", "exercise", "work", "play", "social", "travel", "eat", "rest")


def _lanes_sql(lanes: tuple[str, ...]) -> str:
    return ", ".join(f"'{lane}'" for lane in lanes)


def upgrade() -> None:
    op.execute("ALTER TABLE daily_rollups DROP CONSTRAINT IF EXISTS daily_rollups_lane_check")
    op.execute(
        f"ALTER TABLE daily_rollups ADD CONSTRAINT daily_rollups_lane_check "
        f"CHECK (lane IN ({_lanes_sql(_LANES)}))"
    )


def downgrade() -> None:
    # Reversible only when no butler_ops rows exist (the widened CHECK is a
    # superset); a butler_ops row present would make the narrower CHECK fail,
    # which is the correct signal that the split has been used.
    op.execute("ALTER TABLE daily_rollups DROP CONSTRAINT IF EXISTS daily_rollups_lane_check")
    op.execute(
        f"ALTER TABLE daily_rollups ADD CONSTRAINT daily_rollups_lane_check "
        f"CHECK (lane IN ({_lanes_sql(_PREV_LANES)}))"
    )
