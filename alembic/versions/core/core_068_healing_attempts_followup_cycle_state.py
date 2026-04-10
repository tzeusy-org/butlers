"""healing_attempts: add per-cycle follow-up budgeting and outcome persistence.

Revision ID: core_068
Revises: core_067
Create Date: 2026-04-11 00:00:00.000000

Fixes QA follow-up rate-limiting semantics: the existing ``follow_up_count``
column is a lifetime monotonic counter, but the dispatch gate treats it as a
per-patrol-cycle counter.  This migration adds explicit per-cycle state columns
so that:

  * The cycle budget resets whenever a new patrol_id is seen.
  * Failed follow-ups no longer burn the cycle slot permanently.
  * Operators can diagnose follow-up outcomes from the attempt row.

New columns
-----------
``follow_up_cycle_patrol_id``  UUID DEFAULT NULL
    The patrol_id under which the current cycle counter was last set.
    When a new patrol_id is seen at dispatch time the cycle counter resets
    to 0 before incrementing.

``follow_up_cycle_count``  INTEGER NOT NULL DEFAULT 0
    Number of follow-up dispatches within the current patrol cycle.  Resets
    to 1 (i.e. incremented from 0) whenever ``follow_up_cycle_patrol_id``
    changes.  The dispatch gate checks this value, not the lifetime
    ``follow_up_count``.

``last_follow_up_status``  TEXT DEFAULT NULL
    Outcome of the most recent follow-up dispatch.
    One of: ``'dispatched'`` (pre-execution marker, cleared on outcome),
    ``'succeeded'`` (agent ran and push succeeded), or ``'failed'``
    (agent error, push error, or unexpected exception).

``last_follow_up_session_id``  UUID DEFAULT NULL
    Session identifier returned by the spawner for the last follow-up agent.
    ``NULL`` when the spawner did not return a session_id or the follow-up
    has not yet started.

``last_follow_up_error``  TEXT DEFAULT NULL
    Short error description for the most recent failed follow-up.
    ``NULL`` on success.  Truncated to 500 characters.

``last_follow_up_at``  TIMESTAMPTZ DEFAULT NULL
    Wall-clock timestamp of the most recent follow-up dispatch start.

Strategy
--------
All columns are added with DEFAULT NULL / DEFAULT 0 so that ALTER TABLE
does not require a full table rewrite.  The existing ``follow_up_count``
column is preserved intact as a monotonic lifetime counter.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_068"
down_revision = "core_067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Per-cycle patrol tracking
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS follow_up_cycle_patrol_id UUID DEFAULT NULL
    """)

    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS follow_up_cycle_count INTEGER NOT NULL DEFAULT 0
    """)

    # Last follow-up outcome columns
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS last_follow_up_status TEXT DEFAULT NULL
    """)

    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS last_follow_up_session_id UUID DEFAULT NULL
    """)

    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS last_follow_up_error TEXT DEFAULT NULL
    """)

    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS last_follow_up_at TIMESTAMPTZ DEFAULT NULL
    """)

    # Index for follow-up cycle lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_healing_attempts_followup_cycle
        ON public.healing_attempts (follow_up_cycle_patrol_id)
        WHERE follow_up_cycle_patrol_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS public.idx_healing_attempts_followup_cycle
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS last_follow_up_at
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS last_follow_up_error
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS last_follow_up_session_id
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS last_follow_up_status
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS follow_up_cycle_count
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS follow_up_cycle_patrol_id
    """)
