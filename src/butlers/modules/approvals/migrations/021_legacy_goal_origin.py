"""Mark pre-cutover pending actions with the ``legacy`` goal origin.

Revision ID: approvals_021
Revises: approvals_020
Create Date: 2026-11-05 00:00:00.000000

bu-m1u7zy.7: approvals_020 admitted ``goal_origin = 'legacy'`` for actions
created before the RFC 0038 cutover. This additive backfill sets it on every
existing row without goal lineage, so a reader can tell a pre-cutover action
(``legacy``) from a later non-goal domain job (NULL) and from a broker-parked
goal effect. It runs once at migration time; rows written afterwards are
untouched. No column or constraint changes.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_021"
down_revision = "approvals_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                UPDATE pending_actions SET goal_origin = 'legacy'
                 WHERE goal_id IS NULL AND goal_origin IS NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                UPDATE pending_actions SET goal_origin = NULL
                 WHERE goal_id IS NULL AND goal_origin = 'legacy';
            END IF;
        END $$;
        """
    )
