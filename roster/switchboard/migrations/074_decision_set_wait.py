"""Decision-set waits: a goal waits on one exact decision set version, and a moved goal ends it.

Revision ID: sw_074
Revises: sw_073

RFC 0038 D13, S2.2 and M7 (bu-4c7nvp.1.3, PR-B2). A lead that grouped parked calls into a
decision set may end its turn and wait on the owner's decision of that set. The wait is one more
``wait_envelopes`` kind, ``decision_set``, bound by foreign key to the exact set version (and its
goal) it waits on, so the wake can only ever concern that version.

A set version binds the lead epoch and plan version it was proposed under. The resolver already
refuses a stale binding; this migration makes the staleness durable, in the transaction that
moves the goal, whichever path moves it:

- a goal whose plan version changes supersedes its open set proposed under another plan version;
- a goal that reaches a later lead epoch (a recovery, an escalation or a wake) supersedes its open
  set proposed at an earlier epoch.

No table is added: ``wait_envelopes`` keeps its sw_063 grants, row-level security and guard,
whose immutability check covers the new columns. The downgrade refuses while any decision-set
wait exists.
"""

from alembic import op

revision = "sw_074"
down_revision = "sw_073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE switchboard.wait_envelopes DROP CONSTRAINT wait_envelopes_kind_check;
        ALTER TABLE switchboard.wait_envelopes ADD CONSTRAINT wait_envelopes_kind_check
            CHECK (kind IN ('conditions','owner_answer','approval','settle','decision_set'));
        ALTER TABLE switchboard.wait_envelopes
            ADD COLUMN decision_set_id UUID,
            ADD COLUMN decision_set_version BIGINT;
        ALTER TABLE switchboard.wait_envelopes
            ADD CONSTRAINT ck_envelope_decision_set_pair CHECK (
                (decision_set_id IS NULL) = (decision_set_version IS NULL)),
            ADD CONSTRAINT ck_envelope_decision_set_kind CHECK (
                (kind = 'decision_set') = (decision_set_id IS NOT NULL)),
            ADD CONSTRAINT fk_envelope_decision_set
                FOREIGN KEY (decision_set_id, decision_set_version, goal_id)
                REFERENCES switchboard.decision_set_versions(set_id, version, goal_id);

        -- A changed plan supersedes the open set proposed under another plan version.
        CREATE FUNCTION switchboard.decision_set_plan_moved()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            UPDATE switchboard.decision_set_versions
               SET state = 'superseded', updated_at = clock_timestamp()
             WHERE goal_id = NEW.id AND state = 'open'
               AND plan_version IS DISTINCT FROM NEW.plan_version;
            RETURN NULL;
        END $$;
        CREATE TRIGGER decision_set_plan_moved AFTER UPDATE OF plan_version ON switchboard.goals
            FOR EACH ROW WHEN (OLD.plan_version IS DISTINCT FROM NEW.plan_version)
            EXECUTE FUNCTION switchboard.decision_set_plan_moved();

        -- A later lead epoch supersedes the open set proposed at an earlier one.
        CREATE FUNCTION switchboard.decision_set_epoch_moved()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        BEGIN
            UPDATE switchboard.decision_set_versions
               SET state = 'superseded', updated_at = clock_timestamp()
             WHERE goal_id = NEW.goal_id AND state = 'open' AND epoch < NEW.epoch;
            RETURN NULL;
        END $$;
        CREATE TRIGGER decision_set_epoch_moved AFTER INSERT ON switchboard.goal_epochs
            FOR EACH ROW EXECUTE FUNCTION switchboard.decision_set_epoch_moved();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.wait_envelopes WHERE kind = 'decision_set') THEN
                RAISE EXCEPTION 'Cannot discard decision set waits';
            END IF;
        END $$;
        DROP TRIGGER IF EXISTS decision_set_epoch_moved ON switchboard.goal_epochs;
        DROP FUNCTION IF EXISTS switchboard.decision_set_epoch_moved();
        DROP TRIGGER IF EXISTS decision_set_plan_moved ON switchboard.goals;
        DROP FUNCTION IF EXISTS switchboard.decision_set_plan_moved();
        ALTER TABLE switchboard.wait_envelopes
            DROP CONSTRAINT fk_envelope_decision_set,
            DROP CONSTRAINT ck_envelope_decision_set_kind,
            DROP CONSTRAINT ck_envelope_decision_set_pair,
            DROP COLUMN decision_set_version,
            DROP COLUMN decision_set_id;
        ALTER TABLE switchboard.wait_envelopes DROP CONSTRAINT wait_envelopes_kind_check;
        ALTER TABLE switchboard.wait_envelopes ADD CONSTRAINT wait_envelopes_kind_check
            CHECK (kind IN ('conditions','owner_answer','approval','settle'));
    """)
