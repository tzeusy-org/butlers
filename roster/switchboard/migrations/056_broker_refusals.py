"""Durable capability-broker refusal records (RFC 0038 D7, bu-m1u7zy.12).

Revision ID: sw_056
Revises: sw_055

A refused ``tools/call`` rolls back the call's own transaction, so the broker
writes its refusal here in a transaction of its own: the closed reason, the
requested capability name and, when the bearer token resolved, the goal and
invocation. An unauthenticated refusal records no lineage it could not prove.
Content-blind: no token, argument or downstream error is stored.

Like the sw_047 ledger, runtime access is ordinary-owner, non-FORCE RLS: the
Switchboard runtime role may insert and read. Table grants alone cannot keep it
from updating or deleting, because core_001 (run again for every butler schema)
and init-db.sql replays re-grant own-schema DML on ALL TABLES after this
migration. So a statement-level trigger refuses every UPDATE, DELETE and
TRUNCATE, for the runtime role and the owner alike, with SQLSTATE 42501
(insufficient_privilege), even when RLS leaves no row to touch. ``capability``
holds a published ``<domain>__<tool>`` identity or the ``<unpublished>``
marker, never session-chosen text (D17). The downgrade refuses while a record
exists.
"""

from alembic import op

revision = "sw_056"
down_revision = "sw_055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.broker_refusals (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            goal_id UUID REFERENCES switchboard.goals(id),
            invocation_id UUID,
            -- A published catalog identity or the fixed marker, never free text.
            capability TEXT NOT NULL CHECK (
                capability = '<unpublished>'
                OR capability ~ '^[a-z][a-z0-9_]{0,62}__[a-z][a-z0-9_]{0,126}$'),
            reason TEXT NOT NULL CHECK (reason IN (
                'authentication_refused','goal_not_running','lead_epoch_revoked',
                'capability_not_granted','catalog_changed','resource_outside_grant',
                'policy_refused','budget_exhausted','effect_already_recorded',
                'broker_unavailable')),
            effect_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT ck_refusal_lineage CHECK ((goal_id IS NULL) = (invocation_id IS NULL)),
            CONSTRAINT fk_refusal_invocation FOREIGN KEY (goal_id,invocation_id)
                REFERENCES switchboard.invocations(goal_id,id)
        );
        CREATE INDEX IF NOT EXISTS ix_broker_refusals_goal
            ON switchboard.broker_refusals (goal_id, created_at) WHERE goal_id IS NOT NULL;
        CREATE OR REPLACE FUNCTION switchboard.guard_broker_refusal()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            RAISE EXCEPTION 'Broker refusal records are immutable'
                USING ERRCODE = 'insufficient_privilege';
        END $$;
        DROP TRIGGER IF EXISTS immutable_broker_refusal ON switchboard.broker_refusals;
        -- Statement level, so it fires even when RLS leaves no row to touch, and
        -- on TRUNCATE (including TRUNCATE ... CASCADE from the goal ledger).
        CREATE TRIGGER immutable_broker_refusal BEFORE UPDATE OR DELETE OR TRUNCATE
            ON switchboard.broker_refusals FOR EACH STATEMENT
            EXECUTE FUNCTION switchboard.guard_broker_refusal();
        ALTER TABLE switchboard.broker_refusals ENABLE ROW LEVEL SECURITY;
        -- Fresh-state hygiene only: core_001 (re-run for every butler schema) and
        -- init-db.sql replays re-grant own-schema DML on ALL TABLES, so the
        -- trigger above, not this REVOKE, is what keeps the records immutable.
        REVOKE ALL ON switchboard.broker_refusals FROM PUBLIC, butler_switchboard_rw;
        GRANT SELECT, INSERT ON switchboard.broker_refusals TO butler_switchboard_rw;
        DROP POLICY IF EXISTS broker_refusal_read ON switchboard.broker_refusals;
        CREATE POLICY broker_refusal_read ON switchboard.broker_refusals
            FOR SELECT TO butler_switchboard_rw USING (current_user='butler_switchboard_rw');
        DROP POLICY IF EXISTS broker_refusal_insert ON switchboard.broker_refusals;
        CREATE POLICY broker_refusal_insert ON switchboard.broker_refusals
            FOR INSERT TO butler_switchboard_rw WITH CHECK (current_user='butler_switchboard_rw');
    """)


def downgrade() -> None:
    op.execute("""
        LOCK TABLE switchboard.broker_refusals IN ACCESS EXCLUSIVE MODE;
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.broker_refusals) THEN
                RAISE EXCEPTION 'Cannot discard durable broker refusal records';
            END IF;
        END $$;
        DROP TABLE switchboard.broker_refusals;
        DROP FUNCTION switchboard.guard_broker_refusal();
    """)
