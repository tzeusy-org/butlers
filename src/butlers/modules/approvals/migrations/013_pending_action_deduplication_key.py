"""Converge durable semantic keys after the abandonment migration.

Revision ID: approvals_013
Revises: approvals_012
Create Date: 2026-08-01 00:00:00.000000

Some producers can derive a stable semantic identity for an approval request.
Persisting that identity lets PostgreSQL, rather than a best-effort
check-then-insert read, enforce one active or owner-decided action under
concurrent job runs. The column is nullable so historic rows remain untouched
and producers opt in only when their identity contract is explicit.

This migration also converges a pre-merge development database that recorded
the former, divergent ``approvals_012`` semantic-key migration.  That database
already has the key column but not the abandonment vocabulary which now owns
``approvals_012``.  Reasserting those additive constraints here makes either
history reach the same schema without rewriting approval rows.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_013"
down_revision = "approvals_012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge the schema and protect active or owner-decided actions."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                ALTER TABLE pending_actions
                    ADD COLUMN IF NOT EXISTS deduplication_key TEXT;

                -- A development branch briefly used approvals_012 for the
                -- old three-state index.  Check first rather than choosing a
                -- winner among historical owner decisions during convergence.
                IF EXISTS (
                    SELECT 1
                      FROM pending_actions
                     WHERE deduplication_key IS NOT NULL
                       AND status IN ('pending', 'approved', 'rejected', 'abandoned')
                     GROUP BY deduplication_key
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'approvals_013 found duplicate owner-decision deduplication keys';
                END IF;

                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_status_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_status_check
                    CHECK (status IN (
                        'pending', 'approved', 'rejected', 'expired', 'executed', 'abandoned'
                    ));

                DROP INDEX IF EXISTS ux_pending_actions_active_deduplication_key;
                CREATE UNIQUE INDEX
                    ux_pending_actions_active_deduplication_key
                ON pending_actions (deduplication_key)
                WHERE deduplication_key IS NOT NULL
                  AND status IN ('pending', 'approved', 'rejected', 'abandoned');
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
    """Refuse to discard live semantic identities during a binary rollback."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pending_actions WHERE deduplication_key IS NOT NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade approvals_013 while deduplication keys exist';
                END IF;
                DROP INDEX IF EXISTS ux_pending_actions_active_deduplication_key;
                ALTER TABLE pending_actions DROP COLUMN IF EXISTS deduplication_key;
            END IF;
        END $$;
        """
    )
