"""healing_attempts_followup_backoff: add last_follow_up_at tracking column.

Revision ID: core_072
Revises: core_071
Create Date: 2026-04-11 00:00:00.000000

Adds ``last_follow_up_at`` to ``public.healing_attempts`` so QA PR review
follow-up dispatch can use exponential backoff without overloading the
review-poll timestamp.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_072"
down_revision = "core_071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.healing_attempts
            ADD COLUMN IF NOT EXISTS last_follow_up_at TIMESTAMPTZ DEFAULT NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.healing_attempts
            DROP COLUMN IF EXISTS last_follow_up_at
    """)
