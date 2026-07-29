"""Add the durable dashboard-only abandoned approval outcome.

Revision ID: approvals_012
Revises: approvals_011
Create Date: 2026-07-29 00:00:00.000000

An abandoned action records that the owner previously approved an action but
explicitly chose not to continue recovering its unexecuted command.  It is a
terminal pending-action status and has its own immutable audit event; it must
never be conflated with rejection or execution failure.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_012"
down_revision = "approvals_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Extend approval status and event vocabularies without touching rows."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_status_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_status_check
                    CHECK (status IN (
                        'pending', 'approved', 'rejected', 'expired', 'executed', 'abandoned'
                    ));
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_type_check;
                ALTER TABLE approval_events
                    ADD CONSTRAINT approval_events_type_check
                    CHECK (event_type IN (
                        'action_queued',
                        'action_auto_approved',
                        'action_approved',
                        'action_rejected',
                        'action_expired',
                        'action_abandoned',
                        'action_execution_succeeded',
                        'action_execution_failed',
                        'rule_created',
                        'rule_revoked',
                        'promotion_suggested',
                        'promotion_confirmed',
                        'promotion_dismissed',
                        'promotion_superseded',
                        'demotion_suggested',
                        'demotion_confirmed',
                        'demotion_dismissed'
                    ));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Refuse a lossy downgrade while abandoned actions or events remain."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pending_actions WHERE status = 'abandoned') THEN
                RAISE EXCEPTION 'cannot downgrade approvals_012 while abandoned actions exist';
            END IF;
            IF to_regclass('approval_events') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM approval_events WHERE event_type = 'action_abandoned'
               ) THEN
                RAISE EXCEPTION 'cannot downgrade approvals_012 while abandonment events exist';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE pending_actions DROP CONSTRAINT IF EXISTS pending_actions_status_check;
        ALTER TABLE pending_actions ADD CONSTRAINT pending_actions_status_check
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired', 'executed'));
        """
    )
    op.execute(
        """
        ALTER TABLE approval_events DROP CONSTRAINT IF EXISTS approval_events_type_check;
        ALTER TABLE approval_events ADD CONSTRAINT approval_events_type_check
        CHECK (event_type IN (
            'action_queued', 'action_auto_approved', 'action_approved',
            'action_rejected', 'action_expired', 'action_execution_succeeded',
            'action_execution_failed', 'rule_created', 'rule_revoked',
            'promotion_suggested', 'promotion_confirmed', 'promotion_dismissed',
            'promotion_superseded', 'demotion_suggested', 'demotion_confirmed',
            'demotion_dismissed'
        ));
        """
    )
