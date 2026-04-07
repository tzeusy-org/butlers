"""healing_attempts_pr_review_tracking: add PR review conversation fields.

Revision ID: core_058
Revises: core_057
Create Date: 2026-04-06 00:00:00.000000

Adds PR review conversation tracking columns to ``public.healing_attempts``.
When a QA investigation PR is open and receives review comments or
"changes requested" feedback, the QA staffer now records the review state and
dispatches follow-up agents to address reviewer feedback.

New columns
-----------
``review_state`` TEXT DEFAULT NULL
    Last observed GitHub review state for the PR.
    One of: ``"approved"``, ``"changes_requested"``, ``"commented"``,
    ``"dismissed"``, ``"pending"``, or NULL (no review yet / not a PR row).

``last_review_check_at`` TIMESTAMPTZ DEFAULT NULL
    Timestamp of the most recent successful review-state check.
    Updated on every successful gh pr view call that inspects reviews.

``review_feedback_summary`` TEXT DEFAULT NULL
    Free-text summary of outstanding reviewer feedback, truncated to 2000
    characters.  Set by the PR review tracker when it detects unresolved
    threads or change requests.  Cleared when the PR is approved / merged.

``follow_up_count`` INTEGER NOT NULL DEFAULT 0
    Number of follow-up agent dispatches made for this attempt.
    Used by the rate-limiter: at most 1 follow-up per patrol cycle per PR.

Strategy
--------
All columns are added with DEFAULT NULL / DEFAULT 0 to avoid a full table
rewrite; brief locks are still expected for ALTER TABLE and index creation.
The new FK constraint on ``review_state`` is enforced by the application
layer (not a check constraint) to remain flexible as the set of valid states
may evolve.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_058"
down_revision = "core_057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # review_state: last observed GitHub review decision
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS review_state TEXT DEFAULT NULL
    """)

    # last_review_check_at: timestamp of most recent review check
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS last_review_check_at TIMESTAMPTZ DEFAULT NULL
    """)

    # review_feedback_summary: truncated reviewer feedback text
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS review_feedback_summary TEXT DEFAULT NULL
    """)

    # follow_up_count: number of follow-up dispatches (rate-limiting)
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS follow_up_count INTEGER NOT NULL DEFAULT 0
    """)

    # Index for quickly finding pr_open rows that need review checks
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_healing_attempts_pr_review
        ON public.healing_attempts (status, last_review_check_at)
        WHERE status = 'pr_open' AND qa_patrol_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS public.idx_healing_attempts_pr_review
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS follow_up_count
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS review_feedback_summary
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS last_review_check_at
    """)
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS review_state
    """)
