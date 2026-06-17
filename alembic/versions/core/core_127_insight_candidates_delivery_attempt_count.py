"""Add delivery_attempt_count column to public.insight_candidates.

Revision ID: core_127
Revises: core_126
Create Date: 2026-06-17 00:00:00.000000

Motivation
----------
The broker (roster/switchboard/tools/insight/broker.py) tracks consecutive
delivery failures per candidate via ``delivery_attempt_count`` and promotes a
candidate to ``status='filtered'`` after three consecutive failures.  That
column was present in the test DDL helper (``create_insight_tables()``) but
was never added to the production Alembic migration chain (core_010 omitted
it).  This migration adds the missing column so the production table matches
the broker's expectations and so the dashboard can surface honest
delivery-failure counts.

Column semantics
----------------
- 0 (default)  — never attempted delivery, or last attempt succeeded
- 1 or 2       — pending retry (status='pending')
- >= 3         — permanently failed; broker sets status='filtered'

On upgrade existing rows receive DEFAULT 0 (accurate: pre-column rows have
no meaningful counter).  The delivery-failure dashboard stat therefore starts
at zero and grows from the first post-migration delivery failure.

Reversibility
-------------
The downgrade path drops the column cleanly; the broker degrades gracefully
on a missing column (it would raise at runtime, which is caught by tests).
"""

from __future__ import annotations

from alembic import op

revision = "core_127"
down_revision = "core_126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.insight_candidates
        ADD COLUMN IF NOT EXISTS delivery_attempt_count INTEGER NOT NULL DEFAULT 0
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.insight_candidates
        DROP COLUMN IF EXISTS delivery_attempt_count
        """
    )
