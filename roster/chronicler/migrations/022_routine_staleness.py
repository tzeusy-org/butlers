"""routine_staleness

Revision ID: chronicler_022
Revises: chronicler_021
Create Date: 2026-07-17 00:00:00.000000

Give evidence-mined ``chronicler.routines`` a bounded lifecycle.  The weekly
miner refreshes ``last_confirmed_at`` and resets ``missed_mine_cycles`` only
when it re-detects a pattern.  A completed miner run with primary activity
evidence increments the count for mined rows it did not re-detect; application
policy disables one after its third consecutive miss.  Declared rows retain
the default/null values and are never changed by that reconciliation.

The columns are additive and safe for already-migrated databases: historical
mined rows start with zero missed cycles rather than being guessed stale.
"""

from __future__ import annotations

from alembic import op

revision = "chronicler_022"
down_revision = "chronicler_021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE routines ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMPTZ")
    op.execute(
        """
        ALTER TABLE routines
        ADD COLUMN IF NOT EXISTS missed_mine_cycles INTEGER NOT NULL DEFAULT 0
            CHECK (missed_mine_cycles >= 0)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE routines DROP COLUMN IF EXISTS missed_mine_cycles")
    op.execute("ALTER TABLE routines DROP COLUMN IF EXISTS last_confirmed_at")
