"""Admit ``action_cancelled`` as an approval delivery reason code.

Revision ID: approvals_022
Revises: approvals_021
Create Date: 2026-11-05 00:00:00.000000

bu-m1u7zy.10: a goal Stop cancels a parked effect's action (approvals_020's
``cancelled`` status). Its owner prompt was fenced with the stored reason
``action_rejected``, which also means an owner rejection. This revision relaxes
the two approvals_015 reason CHECKs (presentation ``last_reason_code`` and
attempt ``reason_code``) to admit ``action_cancelled`` as well. Additive: every
existing value stays valid and no row changes. The downgrade refuses while a
row carries the new code.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_022"
down_revision = "approvals_021"
branch_labels = None
depends_on = None

_REASON_CODES = (
    "'quiet_hours', 'owner_recipient_unavailable', 'callback_secret_unavailable', "
    "'transport_unavailable', 'provider_preflight_failed', 'provider_outcome_unknown', "
    "'action_approved', 'action_rejected', 'action_expired', 'action_abandoned', "
    "'defer_rescheduled', 'cohort_empty'"
)


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('approval_delivery_presentations') IS NOT NULL THEN
                ALTER TABLE approval_delivery_presentations
                    DROP CONSTRAINT IF EXISTS
                        approval_delivery_presentations_last_reason_code_check;
                ALTER TABLE approval_delivery_presentations
                    ADD CONSTRAINT approval_delivery_presentations_last_reason_code_check
                    CHECK (last_reason_code IN ({_REASON_CODES}, 'action_cancelled'));
            END IF;
            IF to_regclass('approval_delivery_attempts') IS NOT NULL THEN
                ALTER TABLE approval_delivery_attempts
                    DROP CONSTRAINT IF EXISTS approval_delivery_attempts_reason_code_check;
                ALTER TABLE approval_delivery_attempts
                    ADD CONSTRAINT approval_delivery_attempts_reason_code_check
                    CHECK (reason_code IN ({_REASON_CODES}, 'action_cancelled'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Refuse to rewrite stored cancellation fences."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('approval_delivery_presentations') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM approval_delivery_presentations
                     WHERE last_reason_code = 'action_cancelled'
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade approvals_022 while cancelled fences exist';
                END IF;
                ALTER TABLE approval_delivery_presentations
                    DROP CONSTRAINT IF EXISTS
                        approval_delivery_presentations_last_reason_code_check;
                ALTER TABLE approval_delivery_presentations
                    ADD CONSTRAINT approval_delivery_presentations_last_reason_code_check
                    CHECK (last_reason_code IN ({_REASON_CODES}));
            END IF;
            IF to_regclass('approval_delivery_attempts') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM approval_delivery_attempts
                     WHERE reason_code = 'action_cancelled'
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade approvals_022 while cancelled attempts exist';
                END IF;
                ALTER TABLE approval_delivery_attempts
                    DROP CONSTRAINT IF EXISTS approval_delivery_attempts_reason_code_check;
                ALTER TABLE approval_delivery_attempts
                    ADD CONSTRAINT approval_delivery_attempts_reason_code_check
                    CHECK (reason_code IN ({_REASON_CODES}));
            END IF;
        END $$;
        """
    )
