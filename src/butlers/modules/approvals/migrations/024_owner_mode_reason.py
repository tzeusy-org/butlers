"""Admit ``owner_mode`` as an approval delivery retry reason (bu-dhloi1.22.1).

Revision ID: approvals_024
Revises: approvals_023
Create Date: 2026-11-23 00:00:00.000000

An owner mode that holds proactive items defers a card's push and the worker retries it. That
wait is not a quiet period, so it gets its own closed reason, ``owner_mode``, instead of reusing
``quiet_hours``. This revision relaxes the two approvals_015/022 reason CHECKs (presentation
``last_reason_code`` and attempt ``reason_code``) to admit it. Additive: every existing value
stays valid and no row changes. The downgrade refuses while a row carries the new code.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_024"
down_revision = "approvals_023"
branch_labels = None
depends_on = None

_BEFORE = (
    "'quiet_hours', 'owner_recipient_unavailable', 'callback_secret_unavailable', "
    "'transport_unavailable', 'provider_preflight_failed', 'provider_outcome_unknown', "
    "'action_approved', 'action_rejected', 'action_expired', 'action_abandoned', "
    "'defer_rescheduled', 'cohort_empty', 'action_cancelled'"
)
_PRESENTATIONS = "approval_delivery_presentations"
_ATTEMPTS = "approval_delivery_attempts"


def _constraints(codes: str) -> str:
    return f"""
        IF to_regclass('{_PRESENTATIONS}') IS NOT NULL THEN
            ALTER TABLE {_PRESENTATIONS}
                DROP CONSTRAINT IF EXISTS {_PRESENTATIONS}_last_reason_code_check;
            ALTER TABLE {_PRESENTATIONS}
                ADD CONSTRAINT {_PRESENTATIONS}_last_reason_code_check
                CHECK (last_reason_code IN ({codes}));
        END IF;
        IF to_regclass('{_ATTEMPTS}') IS NOT NULL THEN
            ALTER TABLE {_ATTEMPTS} DROP CONSTRAINT IF EXISTS {_ATTEMPTS}_reason_code_check;
            ALTER TABLE {_ATTEMPTS}
                ADD CONSTRAINT {_ATTEMPTS}_reason_code_check
                CHECK (reason_code IN ({codes}));
        END IF;
    """


def upgrade() -> None:
    op.execute(f"DO $$ BEGIN {_constraints(_BEFORE + ", 'owner_mode'")} END $$;")


def downgrade() -> None:
    """Refuse to rewrite stored hold reasons."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{_PRESENTATIONS}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM {_PRESENTATIONS} WHERE last_reason_code = 'owner_mode')
            THEN
                RAISE EXCEPTION 'Cannot downgrade approvals_024 while owner-mode holds exist';
            END IF;
            IF to_regclass('{_ATTEMPTS}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM {_ATTEMPTS} WHERE reason_code = 'owner_mode')
            THEN
                RAISE EXCEPTION 'Cannot downgrade approvals_024 while owner-mode holds exist';
            END IF;
            {_constraints(_BEFORE)}
        END $$;
        """
    )
