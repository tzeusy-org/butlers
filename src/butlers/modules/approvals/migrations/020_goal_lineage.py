"""Add RFC 0038 goal lineage and the ``cancelled`` status to pending actions.

Revision ID: approvals_020
Revises: approvals_019
Create Date: 2026-11-04 00:00:00.000000

bu-cadcr1: the capability broker parks a goal's effect through the owning
domain's approvals module with its exact lineage (goal, invocation, depth,
exposure, goal origin). The action id is the broker's effect id. A Stop
cancels parked effects with the new status ``cancelled`` (and the
``action_cancelled`` event).

``goal_origin`` is separate from the producer ``origin`` column. It is NULL
for a non-goal domain job, a goal origin for broker-parked actions, and
``legacy`` for actions created before the cutover (the switch release sets
it; this revision only admits the value).

Additive: existing rows keep NULL lineage and every existing path is
unchanged.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_020"
down_revision = "approvals_019"
branch_labels = None
depends_on = None

_EVENT_TYPES = (
    "'action_queued', 'action_auto_approved', 'action_approved', "
    "'action_rejected', 'action_expired', 'action_abandoned', "
    "'action_execution_succeeded', 'action_execution_failed', "
    "'rule_created', 'rule_revoked', 'promotion_suggested', "
    "'promotion_confirmed', 'promotion_dismissed', 'promotion_superseded', "
    "'demotion_suggested', 'demotion_confirmed', 'demotion_dismissed', "
    "'approval_delivery_terminal'"
)


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                ALTER TABLE pending_actions
                    ADD COLUMN IF NOT EXISTS goal_id UUID,
                    ADD COLUMN IF NOT EXISTS goal_invocation_id UUID,
                    ADD COLUMN IF NOT EXISTS goal_depth SMALLINT,
                    ADD COLUMN IF NOT EXISTS goal_exposed BOOLEAN,
                    ADD COLUMN IF NOT EXISTS goal_origin TEXT;
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_goal_lineage_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_goal_lineage_check CHECK (
                        (goal_id IS NULL AND goal_invocation_id IS NULL
                         AND goal_depth IS NULL AND goal_exposed IS NULL
                         AND (goal_origin IS NULL OR goal_origin = 'legacy'))
                        OR (goal_id IS NOT NULL AND goal_invocation_id IS NOT NULL
                            AND goal_depth IN (0, 1) AND goal_exposed IS NOT NULL
                            AND goal_origin IN ('owner', 'obligation', 'third_party', 'staffer'))
                    );
                CREATE INDEX IF NOT EXISTS ix_pending_actions_goal
                    ON pending_actions (goal_id) WHERE goal_id IS NOT NULL;

                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_status_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_status_check
                    CHECK (status IN (
                        'pending', 'approved', 'rejected', 'expired', 'executed', 'abandoned',
                        'cancelled'
                    ));
            END IF;
            IF to_regclass('approval_events') IS NOT NULL THEN
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_type_check;
                ALTER TABLE approval_events ADD CONSTRAINT approval_events_type_check
                    CHECK (event_type IN ({_EVENT_TYPES}, 'action_cancelled'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    """Refuse to discard goal lineage or cancellation history."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM pending_actions
                     WHERE goal_origin IS NOT NULL OR status = 'cancelled'
                ) THEN
                    RAISE EXCEPTION
                        'Cannot downgrade approvals_020 while goal lineage or cancellations exist';
                END IF;
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_status_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_status_check
                    CHECK (status IN (
                        'pending', 'approved', 'rejected', 'expired', 'executed', 'abandoned'
                    ));
                DROP INDEX IF EXISTS ix_pending_actions_goal;
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_goal_lineage_check,
                    DROP COLUMN IF EXISTS goal_origin,
                    DROP COLUMN IF EXISTS goal_exposed,
                    DROP COLUMN IF EXISTS goal_depth,
                    DROP COLUMN IF EXISTS goal_invocation_id,
                    DROP COLUMN IF EXISTS goal_id;
            END IF;
            IF to_regclass('approval_events') IS NOT NULL THEN
                IF EXISTS (
                    SELECT 1 FROM approval_events WHERE event_type = 'action_cancelled'
                ) THEN
                    RAISE EXCEPTION 'Cannot downgrade approvals_020 while cancellations exist';
                END IF;
                ALTER TABLE approval_events
                    DROP CONSTRAINT IF EXISTS approval_events_type_check;
                ALTER TABLE approval_events ADD CONSTRAINT approval_events_type_check
                    CHECK (event_type IN ({_EVENT_TYPES}));
            END IF;
        END $$;
        """
    )
