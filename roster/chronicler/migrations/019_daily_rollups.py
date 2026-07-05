"""daily_rollups

Revision ID: chronicler_019
Revises: chronicler_018
Create Date: 2026-07-06 00:00:00.000000

Add ``chronicler.daily_rollups`` + ``chronicler.daily_rollup_flags``
(bu-u30as, telemetry-distillation bead 3, design doc §3.3/§6.3 +
openspec change ``chronicler-telemetry-distillation``).

This is the first genuinely new piece of infrastructure the distillation
design introduces: a persisted per-lane daily summary, materialized by the
``chronicler_rollup_daily`` job (``rollups.py::materialize_daily_rollups``)
from already-projected ``activity``-layer episodes, reusing
``aggregations.lane_for_activity``/``union_seconds`` exactly as the live
``GET /aggregate/by-category`` endpoint does (see
``tests/integration/test_daily_rollups_integration.py`` for the bit-for-bit
regression proving the two surfaces cannot diverge).

Schema
------
- ``daily_rollups`` — one row per ``(local_date, lane)``. ``lane`` is
  constrained to the fixed ``aggregations.LANES`` taxonomy (sleep, exercise,
  work, play, social, travel, eat, rest) so a typo can never silently create
  an unqueryable bucket. ``seconds``/``episode_count`` mirror the live
  endpoint's per-category bucket fields. ``distinct_place_count`` is left
  NULL in this bead (no scenario in the ``chronicler-telemetry-distillation``
  spec requires it yet, and no writer populates it here — a future bead may
  compute it from ``place_episode``/``presence_episode`` rows without a
  further migration).
- ``daily_rollup_flags`` — one row per ``(local_date, flag_type)``. This bead
  only creates the table; the anomaly-flag rules that populate it
  (``feeder_dark``, ``sleep_missing``, ``routine_break``,
  ``lane_share_outlier``) are bead 4's scope (design doc §3.4). ``severity``
  is constrained to the two values the design names (``info``, ``warning``);
  widening the CHECK later is a small, reversible follow-up if bead 4 needs a
  third.

Idempotent materialization
---------------------------
Both tables are upserted on their natural key (``UNIQUE (local_date, lane)`` /
``UNIQUE (local_date, flag_type)``) so a re-run after a late-arriving
correction/override simply recomputes in place — no duplicate rows, per the
design's idempotency requirement (spec.md "Idempotent re-materialization").
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_019"
down_revision = "chronicler_018"
branch_labels = None
depends_on = None

# Mirrors aggregations.LANES exactly. Duplicated here (not imported) because
# Alembic migrations must remain runnable independent of application code
# evolving later — same convention as every other CHECK-constrained
# enumeration in this migration chain (e.g. chronicler_018's ``origin``).
_LANES = ("sleep", "exercise", "work", "play", "social", "travel", "eat", "rest")


def upgrade() -> None:
    lanes_list = ", ".join(f"'{lane}'" for lane in _LANES)
    op.execute(f"""
        CREATE TABLE IF NOT EXISTS daily_rollups (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            local_date DATE NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Asia/Singapore',
            lane TEXT NOT NULL CHECK (lane IN ({lanes_list})),
            seconds INTEGER NOT NULL DEFAULT 0 CHECK (seconds >= 0),
            episode_count INTEGER NOT NULL DEFAULT 0 CHECK (episode_count >= 0),
            distinct_place_count INTEGER,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (local_date, lane)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_rollups_local_date_idx
        ON daily_rollups (local_date DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_rollup_flags (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            local_date DATE NOT NULL,
            flag_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning')),
            detail JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (local_date, flag_type)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS daily_rollup_flags_local_date_idx
        ON daily_rollup_flags (local_date DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS daily_rollup_flags_local_date_idx")
    op.execute("DROP TABLE IF EXISTS daily_rollup_flags")
    op.execute("DROP INDEX IF EXISTS daily_rollups_local_date_idx")
    op.execute("DROP TABLE IF EXISTS daily_rollups")
