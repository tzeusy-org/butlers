"""qa_findings: add dispatch_queued column for concurrency-cap retry queue.

Revision ID: core_069
Revises: core_068
Create Date: 2026-04-11 00:00:00.000000

When the QA dispatcher hits the concurrency cap mid-patrol, subsequent novel
findings are skipped without being dispatched.  Previously those findings had
no dedup_reason and no retry path — a one-shot finding skipped this way would
be silently lost.

This migration adds:

  dispatch_queued  BOOLEAN NOT NULL DEFAULT FALSE
      Set to TRUE by the dispatcher for findings that were skipped due to
      concurrency pressure in a patrol where at least one prior finding
      consumed the concurrency cap.  The next patrol cycle picks up rows
      where ``dispatch_queued = TRUE``, reconstitutes them as QaFinding
      objects, and injects them into the triage batch so they receive a
      fresh dispatch opportunity.

      The flag is cleared (set back to FALSE) once the finding is loaded
      for re-triage, preventing duplicate re-injection across patrols.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_069"
down_revision = "core_068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.qa_findings
            ADD COLUMN IF NOT EXISTS dispatch_queued BOOLEAN NOT NULL DEFAULT FALSE
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_qa_findings_dispatch_queued
        ON public.qa_findings (dispatch_queued)
        WHERE dispatch_queued = TRUE
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS public.ix_qa_findings_dispatch_queued
    """)
    op.execute("""
        ALTER TABLE public.qa_findings
            DROP COLUMN IF EXISTS dispatch_queued
    """)
