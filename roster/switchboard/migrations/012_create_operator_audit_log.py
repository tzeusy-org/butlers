"""Create operator_audit_log table for manual interventions.

Revision ID: sw_012
Revises: sw_011
Create Date: 2026-02-15 00:00:00.000000

Migration notes:
- Upgrade creates operator_audit_log for tracking manual reroutes, cancels,
  replays, force-completes.
- All operator actions are attributable (who, when, why, result).
- Downgrade drops the table.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_012"
down_revision = "sw_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE operator_audit_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            action_type TEXT NOT NULL,
            target_request_id UUID NOT NULL,
            target_table TEXT NOT NULL,
            operator_identity TEXT NOT NULL,
            reason TEXT NOT NULL,
            action_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            outcome TEXT NOT NULL,
            outcome_details JSONB NOT NULL DEFAULT '{}'::jsonb,
            performed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT valid_action_type CHECK (
                action_type IN (
                    'manual_reroute',
                    'cancel_request',
                    'abort_request',
                    'controlled_replay',
                    'controlled_retry',
                    'force_complete'
                )
            ),
            CONSTRAINT valid_outcome CHECK (
                outcome IN ('success', 'failed', 'rejected', 'partial')
            )
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_performed_at
        ON operator_audit_log (performed_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_action_type_performed_at
        ON operator_audit_log (action_type, performed_at DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_target_request_id
        ON operator_audit_log (target_request_id)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_operator_audit_log_operator_identity_performed_at
        ON operator_audit_log (operator_identity, performed_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS operator_audit_log")
