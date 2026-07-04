"""dismissed_issues_ack_recurrence: add last_seen_at to public.dismissed_issues.

Revision ID: core_152
Revises: core_151
Create Date: 2026-07-04 00:00:00.000000

Backs the JARVIS audit "Act loop" move (bu-86c4c.15): the Issues feed's
dismiss control today is dismiss-forever — an issue group stays hidden
indefinitely even if the underlying error recurs tomorrow. This turns it
into acknowledge-until-recurrence: the ack now records the issue's
``last_seen_at`` at the moment it was acknowledged, and ``GET /api/issues``
re-surfaces the group the instant its ``last_seen_at`` advances past that
snapshot (a genuinely new occurrence), instead of requiring the owner to
notice and manually restore it.

Column:
  last_seen_at  TIMESTAMPTZ NULL — the issue's ``last_seen_at`` at ack time.
                NULL for legacy rows written before this column existed (or
                for an issue type that never carried a timestamp); the
                router treats a NULL as "no recurrence signal available",
                which preserves the old dismiss-forever behavior for those
                rows rather than guessing.

Backward-compatible, additive-only: existing rows get NULL and keep working.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_152"
down_revision = "core_151"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dismissed_issues
            ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dismissed_issues
            DROP COLUMN IF EXISTS last_seen_at
        """
    )
