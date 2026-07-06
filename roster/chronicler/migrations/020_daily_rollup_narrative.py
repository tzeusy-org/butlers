"""daily_rollup_narrative

Revision ID: chronicler_020
Revises: chronicler_019
Create Date: 2026-07-06 00:00:00.000000

Add nullable ``narrative`` columns to ``chronicler.daily_rollups`` and
``chronicler.daily_rollup_flags`` (bu-v9y18, telemetry-distillation bead 6,
design doc §3.5/§6.6 + openspec change ``chronicler-telemetry-distillation``
"Bounded Once-Daily LLM Labeling (Optional)").

These back the optional, bounded, once-per-local-day LLM labeling pass:
a short natural-language label per anomaly flag, and/or a one-line day
summary. Both columns are nullable and written by nothing except the new
``chronicler_narrate_daily`` job — every other writer (the deterministic
rollup materializer in ``rollups.py``, the anomaly-flag evaluator in
``flags.py``) leaves them untouched.

[decision] Deliberately separate columns rather than folding the per-flag
label into ``daily_rollup_flags.detail`` (the obvious-looking reuse the
design doc's prose suggests): ``flags.py::evaluate_and_write_daily_flags``
fully overwrites ``detail`` on every reconciliation pass, and that job runs
hourly over a 7-day trailing window (``chronicler_rollup_daily``, migration
chronicler_019/bu-u30as) — a label written into ``detail`` would be wiped
within the hour, before any surface could realistically read it. A
dedicated column that no other writer ever references is the only way this
survives across re-evaluations. Same reasoning applies symmetrically to
``daily_rollups.narrative`` vs. threading a value through
``rollups.py::upsert_daily_rollup``'s upsert.

``daily_rollups`` has no single per-day row (one row per ``(local_date,
lane)``); the day-level summary is written identically to every lane row
for that date by the narration job, not modeled as a new table.

Both columns are additive/nullable — no backfill, no NOT NULL constraint,
fully reversible.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_020"
down_revision = "chronicler_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE daily_rollups ADD COLUMN IF NOT EXISTS narrative TEXT")
    op.execute("ALTER TABLE daily_rollup_flags ADD COLUMN IF NOT EXISTS narrative TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE daily_rollup_flags DROP COLUMN IF EXISTS narrative")
    op.execute("ALTER TABLE daily_rollups DROP COLUMN IF EXISTS narrative")
