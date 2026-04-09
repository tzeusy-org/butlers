"""qa_findings: add source_session_trigger_source and structured_evidence columns.

Revision ID: core_067
Revises: core_066
Create Date: 2026-04-09 00:00:00.000000

Adds two nullable columns to ``public.qa_findings`` to support the phased
workflow evidence plumbing (bu-xp0x0.4):

  source_session_trigger_source  TEXT (nullable)
      The ``trigger_source`` value from the session record or log entry that
      produced the error.  Used by the QA self-recursion barrier in
      ``dispatch_qa_investigation`` (Gate 0) to suppress autonomous
      investigation of failures that originated from QA self-healing sessions.

  structured_evidence  JSONB (nullable)
      Optional dict of structured diagnostic evidence containing identifiers
      and diagnostics from the discovery source (e.g. ``session_id``,
      ``request_id``, ``trace_id``, ``runtime_type``, ``model``,
      ``tool_call_count``).  Investigation agents reference this evidence via
      a persisted artifact pointer rather than embedding raw payloads in the
      prompt.

Both columns default to NULL so all existing rows are unaffected.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_067"
down_revision = "core_066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.qa_findings
            ADD COLUMN IF NOT EXISTS source_session_trigger_source TEXT DEFAULT NULL
    """)
    op.execute("""
        ALTER TABLE public.qa_findings
            ADD COLUMN IF NOT EXISTS structured_evidence JSONB DEFAULT NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.qa_findings
            DROP COLUMN IF EXISTS structured_evidence
    """)
    op.execute("""
        ALTER TABLE public.qa_findings
            DROP COLUMN IF EXISTS source_session_trigger_source
    """)
