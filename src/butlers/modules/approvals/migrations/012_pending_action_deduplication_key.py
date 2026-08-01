"""Add durable semantic deduplication keys for active pending actions.

Revision ID: approvals_012
Revises: approvals_011
Create Date: 2026-08-01 00:00:00.000000

Some producers can derive a stable semantic identity for an approval request.
Persisting that identity lets PostgreSQL, rather than a best-effort
check-then-insert read, enforce one active or owner-decided action under
concurrent job runs. The column is nullable so historic rows remain untouched
and producers opt in only when their identity contract is explicit.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_012"
down_revision = "approvals_011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable key and protect active/decided owner actions."""
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                ALTER TABLE pending_actions
                    ADD COLUMN IF NOT EXISTS deduplication_key TEXT;

                CREATE UNIQUE INDEX IF NOT EXISTS
                    ux_pending_actions_active_deduplication_key
                ON pending_actions (deduplication_key)
                WHERE deduplication_key IS NOT NULL
                  AND status IN ('pending', 'approved', 'rejected');
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
                        'Cannot downgrade approvals_012 while deduplication keys exist';
                END IF;
                DROP INDEX IF EXISTS ux_pending_actions_active_deduplication_key;
                ALTER TABLE pending_actions DROP COLUMN IF EXISTS deduplication_key;
            END IF;
        END $$;
        """
    )
