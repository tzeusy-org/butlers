"""Durable central Stop and PARK identity, without owning-domain cancellation.

Revision ID: sw_048
Revises: sw_050 (delivered admission-attempt bindings)
"""

from alembic import op

revision = "sw_048"
down_revision = "sw_050"
branch_labels = None
depends_on = None

_TABLES = ("goal_park_associations", "goal_stop_requests", "goal_approval_cancel_work")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS switchboard.goal_park_associations (
            effect_id UUID PRIMARY KEY,
            goal_id UUID NOT NULL,
            owning_domain TEXT NOT NULL CHECK (owning_domain ~ '^[a-zA-Z0-9_.-]+$' AND position('__' in owning_domain)=0),
            action_id UUID NOT NULL CHECK (action_id=effect_id),
            allow_standing_rule BOOLEAN NOT NULL,
            approval_ref TEXT CHECK (approval_ref IS NULL OR length(approval_ref) BETWEEN 1 AND 512),
            dispatch_intent_id UUID UNIQUE,
            dispatch_committed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(goal_id,effect_id),
            FOREIGN KEY(goal_id,effect_id) REFERENCES switchboard.effects(goal_id,id),
            CHECK ((dispatch_intent_id IS NULL) = (dispatch_committed_at IS NULL))
        );
        CREATE TABLE IF NOT EXISTS switchboard.goal_stop_requests (
            request_id UUID PRIMARY KEY,
            goal_id UUID NOT NULL REFERENCES switchboard.goals(id),
            provenance_digest TEXT NOT NULL CHECK (provenance_digest ~ '^[0-9a-f]{64}$'),
            revoked_tokens BIGINT NOT NULL CHECK (revoked_tokens>=0),
            cancellation_work BIGINT NOT NULL CHECK (cancellation_work>=0),
            committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(goal_id,request_id)
        );
        CREATE TABLE IF NOT EXISTS switchboard.goal_approval_cancel_work (
            effect_id UUID PRIMARY KEY,
            goal_id UUID NOT NULL,
            request_id UUID NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','claimed','acknowledged')),
            generation BIGINT NOT NULL DEFAULT 0 CHECK (generation>=0),
            claim_id UUID,
            lease_expires_at TIMESTAMPTZ,
            receipt_digest TEXT CHECK (receipt_digest ~ '^[0-9a-f]{64}$'),
            outcome TEXT CHECK (outcome IN ('cancelled','fenced_absent')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            FOREIGN KEY(goal_id,effect_id) REFERENCES switchboard.goal_park_associations(goal_id,effect_id),
            FOREIGN KEY(goal_id,request_id) REFERENCES switchboard.goal_stop_requests(goal_id,request_id)
                DEFERRABLE INITIALLY DEFERRED,
            CHECK ((state='pending' AND generation=0 AND claim_id IS NULL AND lease_expires_at IS NULL)
                OR (state IN ('claimed','acknowledged') AND generation>0 AND claim_id IS NOT NULL
                    AND lease_expires_at IS NOT NULL AND isfinite(lease_expires_at))),
            CHECK ((state='acknowledged') = (receipt_digest IS NOT NULL AND outcome IS NOT NULL))
        );
        CREATE INDEX IF NOT EXISTS ix_goal_cancel_claim
            ON switchboard.goal_approval_cancel_work(goal_id,state,lease_expires_at);
    """)
    # Match sw047: ordinary migration-owner dump bypass, isolated runtime RLS.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles r
                WHERE (r.rolname LIKE 'butler_%_rw' OR r.rolname LIKE 'connector_%')
                  AND (r.rolsuper OR r.rolbypassrls OR pg_has_role(r.rolname,current_user,'MEMBER')))
            THEN RAISE EXCEPTION 'Stop requires isolated ordinary runtime roles'; END IF;
        END $$
    """)
    for table in _TABLES:
        relation = f"switchboard.{table}"
        op.execute(f"ALTER TABLE {relation} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {relation} FROM PUBLIC")
        op.execute(f"GRANT SELECT,INSERT,UPDATE ON {relation} TO butler_switchboard_rw")
        for command in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            name = "goal_stop_" + command.lower()
            predicate = "current_user='butler_switchboard_rw'"
            if command == "DELETE" or (table == "goal_stop_requests" and command == "UPDATE"):
                predicate += " AND false"
            policy = f"CREATE POLICY {name} ON {relation} FOR {command} TO butler_switchboard_rw "
            op.execute(f"DROP POLICY IF EXISTS {name} ON {relation}")
            if command != "INSERT":
                policy += f"USING ({predicate}) "
            if command in {"INSERT", "UPDATE"}:
                policy += f"WITH CHECK ({predicate})"
            op.execute(policy)
    op.execute("""
        CREATE OR REPLACE FUNCTION switchboard.goal_stop_guard()
        RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$
        DECLARE target_status TEXT; effect switchboard.effects%ROWTYPE;
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Stop evidence is immutable'; END IF;
            IF TG_TABLE_NAME='goals' THEN
                IF OLD.status='stopped' AND NEW.status<>'stopped' THEN
                    RAISE EXCEPTION 'Stopped goal cannot revive';
                END IF;
            ELSIF TG_TABLE_NAME='invocations' THEN
                SELECT status INTO target_status FROM switchboard.goals WHERE id=NEW.goal_id FOR UPDATE;
                IF target_status='stopped' AND
                    (NEW.token_hash IS NOT NULL OR NEW.status IN ('launching','running')) THEN
                    RAISE EXCEPTION 'Stopped goal cannot issue active invocation authority';
                END IF;
            ELSIF TG_TABLE_NAME='effects' THEN
                IF NEW.status='dispatched' AND OLD.status<>'dispatched' THEN
                    SELECT status INTO target_status FROM switchboard.goals WHERE id=NEW.goal_id FOR UPDATE;
                    IF target_status NOT IN ('running','settling') THEN
                        RAISE EXCEPTION 'Stopped goal cannot begin dispatch';
                    END IF;
                END IF;
                -- Future approved replay must commit an immutable witness BEFORE forward.
                -- A direct-dispatch effect has no PARK association and is unchanged.
                IF NEW.status IN ('dispatched','committed') AND EXISTS (
                    SELECT 1 FROM switchboard.goal_park_associations
                    WHERE effect_id=NEW.id AND dispatch_intent_id IS NULL) THEN
                    RAISE EXCEPTION 'PARK dispatch requires committed intent identity';
                END IF;
            ELSIF TG_TABLE_NAME='goal_stop_requests' THEN
                IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Stop receipt is immutable'; END IF;
            ELSIF TG_TABLE_NAME='goal_park_associations' THEN
                SELECT status INTO target_status FROM switchboard.goals WHERE id=NEW.goal_id FOR UPDATE;
                SELECT * INTO STRICT effect FROM switchboard.effects
                    WHERE id=NEW.effect_id AND goal_id=NEW.goal_id;
                IF effect.capability !~ '^[a-zA-Z0-9_.-]+$'
                    OR cardinality(string_to_array(effect.capability,'__'))<>2
                    OR split_part(effect.capability,'__',2)=''
                    OR split_part(effect.capability,'__',1)<>NEW.owning_domain THEN
                    RAISE EXCEPTION 'PARK domain must match immutable effect';
                END IF;
                IF TG_OP='INSERT' AND (target_status='stopped' OR effect.status<>'requested'
                    OR NEW.dispatch_intent_id IS NOT NULL OR NEW.approval_ref IS NOT NULL) THEN
                    RAISE EXCEPTION 'PARK identity must precede owning forward';
                END IF;
                IF TG_OP='UPDATE' THEN
                    IF (to_jsonb(NEW)-ARRAY['approval_ref','dispatch_intent_id','dispatch_committed_at'])
                       IS DISTINCT FROM
                       (to_jsonb(OLD)-ARRAY['approval_ref','dispatch_intent_id','dispatch_committed_at'])
                       OR (OLD.approval_ref IS NOT NULL AND NEW.approval_ref IS DISTINCT FROM OLD.approval_ref)
                       OR (OLD.dispatch_intent_id IS NOT NULL AND
                           (NEW.dispatch_intent_id,NEW.dispatch_committed_at) IS DISTINCT FROM
                           (OLD.dispatch_intent_id,OLD.dispatch_committed_at)) THEN
                        RAISE EXCEPTION 'PARK identity and attachments are immutable';
                    END IF;
                    IF OLD.dispatch_intent_id IS NULL AND NEW.dispatch_intent_id IS NOT NULL THEN
                        IF target_status NOT IN ('running','settling')
                           OR effect.status NOT IN ('parked','approved')
                           OR EXISTS (SELECT 1 FROM switchboard.goal_approval_cancel_work
                                      WHERE effect_id=NEW.effect_id) THEN
                            RAISE EXCEPTION 'Stop fenced approved dispatch';
                        END IF;
                        NEW.dispatch_committed_at=clock_timestamp();
                    END IF;
                END IF;
            ELSIF TG_TABLE_NAME='goal_approval_cancel_work' THEN
                SELECT status INTO target_status FROM switchboard.goals WHERE id=NEW.goal_id FOR UPDATE;
                IF target_status<>'stopped' OR EXISTS (
                    SELECT 1 FROM switchboard.goal_park_associations
                    WHERE effect_id=NEW.effect_id AND dispatch_intent_id IS NOT NULL) THEN
                    RAISE EXCEPTION 'Cancellation requires stopped goal without dispatch intent';
                END IF;
                IF TG_OP='UPDATE' AND ( (NEW.effect_id,NEW.goal_id,NEW.request_id,NEW.created_at) IS DISTINCT FROM
                   (OLD.effect_id,OLD.goal_id,OLD.request_id,OLD.created_at)
                   OR OLD.state='acknowledged'
                   OR NOT (
                       (NEW.state='claimed' AND NEW.generation=OLD.generation+1
                        AND NEW.claim_id IS DISTINCT FROM OLD.claim_id
                        AND (OLD.state='pending' OR OLD.lease_expires_at<=clock_timestamp())
                        AND NEW.lease_expires_at>clock_timestamp()
                        AND NEW.lease_expires_at<=clock_timestamp()+interval '60 seconds')
                       OR (OLD.state='claimed' AND NEW.state='acknowledged'
                           AND (NEW.generation,NEW.claim_id,NEW.lease_expires_at) IS NOT DISTINCT FROM
                               (OLD.generation,OLD.claim_id,OLD.lease_expires_at)
                           AND OLD.lease_expires_at>clock_timestamp()))) THEN
                    RAISE EXCEPTION 'Invalid cancellation claim transition';
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    events = {"goals": "UPDATE", "invocations": "INSERT OR UPDATE", "effects": "UPDATE"}
    events |= {table: "INSERT OR UPDATE OR DELETE" for table in _TABLES}
    for table, event in events.items():
        op.execute(f"DROP TRIGGER IF EXISTS goal_stop_guard ON switchboard.{table}")
        op.execute(
            f"CREATE TRIGGER goal_stop_guard BEFORE {event} ON switchboard.{table} "
            "FOR EACH ROW EXECUTE FUNCTION switchboard.goal_stop_guard()"
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"LOCK TABLE switchboard.{table} IN ACCESS EXCLUSIVE MODE")
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM switchboard.goal_park_associations)
               OR EXISTS (SELECT 1 FROM switchboard.goal_stop_requests)
               OR EXISTS (SELECT 1 FROM switchboard.goal_approval_cancel_work) THEN
                RAISE EXCEPTION 'Cannot discard Stop evidence';
            END IF;
        END $$
    """)
    for table in ("goals", "invocations", "effects"):
        op.execute(f"DROP TRIGGER IF EXISTS goal_stop_guard ON switchboard.{table}")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE IF EXISTS switchboard.{table}")
    op.execute("DROP FUNCTION IF EXISTS switchboard.goal_stop_guard()")
