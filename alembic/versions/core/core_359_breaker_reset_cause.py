"""breaker_resets: record the cause each manual reset claims to address (bu-ejow5p.8).

Revision ID: core_359
Revises: core_358

REQ-qa-remediation-liveness-003.  A manual QA breaker reset buys five more attempts. When the
failure streak was caused by a missing capability (for example no publisher), a reset that does not
name what changed only buys five more identical failures. Two nullable columns record the cause
code the reset addresses and the operator's reference to what changed (a PR, a bead, a deploy).
Historic rows keep NULL: they were reset before a cause was required, and are never rewritten.

A refused reset is an owner-visible decision, so it is recorded too: ``outcome`` is ``reset``
(default, so every historic row and every legacy writer is a real reset) or ``refused``, and
``refusal_reason`` carries the machine reason. A refusal row must never clear the breaker, so the
shared QA admission filter (``QA_BREAKER_WHERE_SQL``) counts only ``outcome = 'reset'`` rows.

All columns are additive. Table-level grants from core_164 already cover new columns.
"""

from __future__ import annotations

from alembic import op

revision = "core_359"
down_revision = "core_358"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS cause_code TEXT")
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS cause_ref TEXT")
    op.execute(
        "ALTER TABLE public.breaker_resets "
        "ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT 'reset'"
    )
    op.execute("ALTER TABLE public.breaker_resets ADD COLUMN IF NOT EXISTS refusal_reason TEXT")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conrelid = 'public.breaker_resets'::regclass
                  AND conname = 'breaker_resets_outcome_check'
            ) THEN
                ALTER TABLE public.breaker_resets
                    ADD CONSTRAINT breaker_resets_outcome_check
                    CHECK (outcome IN ('reset', 'refused'));
            END IF;
        END $$
        """
    )


def downgrade() -> None:
    # Refusal rows are not resets; drop them so the downgraded filter-less readers stay correct.
    # The core chain runs once per butler schema over this one shared table, so an earlier
    # schema's downgrade has already dropped the column: guard the delete on its existence.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'breaker_resets'
                  AND column_name = 'outcome'
            ) THEN
                DELETE FROM public.breaker_resets WHERE outcome = 'refused';
            END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE public.breaker_resets DROP CONSTRAINT IF EXISTS breaker_resets_outcome_check"
    )
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS refusal_reason")
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS outcome")
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS cause_ref")
    op.execute("ALTER TABLE public.breaker_resets DROP COLUMN IF EXISTS cause_code")
