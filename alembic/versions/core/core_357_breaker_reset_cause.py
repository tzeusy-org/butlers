"""breaker_resets: record the cause each manual reset claims to address (bu-ejow5p.8).

Revision ID: core_357
Revises: core_356

REQ-qa-remediation-liveness-003.  A manual QA breaker reset buys five more attempts. When the
failure streak was caused by a missing capability (for example no publisher), a reset that does not
name what changed only buys five more identical failures. Two nullable columns record the cause
code the reset addresses and the operator's reference to what changed (a PR, a bead, a deploy).
Historic rows keep NULL: they were reset before a cause was required, and are never rewritten.

Both columns are additive and nullable, so existing readers and the ``max(reset_at)`` admission
filter are unchanged. Table-level grants from core_164 already cover new columns.
"""

from __future__ import annotations

from alembic import op

revision = "core_357"
down_revision = "core_356"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS cause_code TEXT")
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS cause_ref TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS cause_ref")
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS cause_code")
