"""qa_patrols: add 'suppressed' to status CHECK constraint.

Revision ID: core_064
Revises: core_063
Create Date: 2026-04-09 00:00:00.000000

The QA module writes ``status = 'suppressed'`` when novel findings exist but
all are filtered out by cooldown or severity threshold (novel_count > 0 but
nothing dispatched and no dispatch error).  The original CHECK constraint
(core_051) did not include this value, causing CheckViolationError at runtime.

This migration drops the old constraint and creates a new one that includes
all six status values the code can write:
  running, clean, findings_dispatched, error, skipped_overlap, suppressed
"""

from __future__ import annotations

from alembic import op

revision = "core_064"
down_revision = "core_063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.qa_patrols
        DROP CONSTRAINT IF EXISTS ck_qa_patrols_status
    """)
    op.execute("""
        ALTER TABLE public.qa_patrols
        ADD CONSTRAINT ck_qa_patrols_status CHECK (
            status IN (
                'running',
                'clean',
                'findings_dispatched',
                'error',
                'skipped_overlap',
                'suppressed'
            )
        )
    """)


def downgrade() -> None:
    # Convert any 'suppressed' rows to 'clean' so the old constraint can apply.
    op.execute("""
        UPDATE public.qa_patrols
        SET status = 'clean'
        WHERE status = 'suppressed'
    """)
    op.execute("""
        ALTER TABLE public.qa_patrols
        DROP CONSTRAINT IF EXISTS ck_qa_patrols_status
    """)
    op.execute("""
        ALTER TABLE public.qa_patrols
        ADD CONSTRAINT ck_qa_patrols_status CHECK (
            status IN (
                'running',
                'clean',
                'findings_dispatched',
                'error',
                'skipped_overlap'
            )
        )
    """)
