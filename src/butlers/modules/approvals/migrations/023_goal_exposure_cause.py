"""Record why a parked goal effect was exposed and whether a rule could apply (RFC 0038 D19).

Revision ID: approvals_023
Revises: approvals_022
Create Date: 2026-11-13 00:00:00.000000

bu-jv1moa.26: the owner surface shows "exposed to what". The cause is the
invocation's ``exposure_cause`` on the Switchboard goal ledger, which a butler
schema cannot read. The capability broker reads it under the goal lock and
stamps it on the park record, so every reader sees the same server-held fact.

``goal_rule_possible`` is the broker's park-time verdict (``Decision.allow_standing_rule`` and
the tool is not rule-prepared), kept so the owner can be told whether a standing rule could ever
match the call. It is display-only and never feeds authorization: admission still judges each
rule on its own. NULL on a pre-stamp or legacy row.

``goal_exposure_cause`` is the closed S1 enum (``butlers.core.goal_labels``).
NULL means unknown: a pre-stamp or legacy row, or an unexposed lineage.
Additive: existing rows keep NULL and every existing path is unchanged.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_023"
down_revision = "approvals_022"
branch_labels = None
depends_on = None

_CAUSES = "'ingress', 'read', 'child', 'inbox', 'recovery', 'legacy_unknown'"


def upgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('pending_actions') IS NOT NULL THEN
                ALTER TABLE pending_actions
                    ADD COLUMN IF NOT EXISTS goal_exposure_cause TEXT,
                    ADD COLUMN IF NOT EXISTS goal_rule_possible BOOLEAN;
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_goal_exposure_cause_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_goal_exposure_cause_check CHECK (
                        goal_exposure_cause IS NULL
                        OR (goal_id IS NOT NULL AND goal_exposed IS TRUE
                            AND goal_exposure_cause IN ({_CAUSES}))
                    );
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_goal_rule_possible_check;
                ALTER TABLE pending_actions
                    ADD CONSTRAINT pending_actions_goal_rule_possible_check CHECK (
                        goal_rule_possible IS NULL OR goal_id IS NOT NULL
                    );
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
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_goal_exposure_cause_check;
                ALTER TABLE pending_actions
                    DROP CONSTRAINT IF EXISTS pending_actions_goal_rule_possible_check;
                ALTER TABLE pending_actions DROP COLUMN IF EXISTS goal_exposure_cause,
                    DROP COLUMN IF EXISTS goal_rule_possible;
            END IF;
        END $$;
        """
    )
