"""Durable owning approval lineage, Stop tombstones and immutable receipts.

Revision ID: approvals_019
Revises: approvals_018 (delivered S1)

This installs storage, never a listener/profile or a domain assertion verifier.
No generic executed action is proof of a committed domain effect. Downgrade
refuses any retained goal evidence. Historical receipts are insertion-bound and
immutable; only the current version must match the final current state.
"""

from alembic import op

revision = "approvals_019"
down_revision = "approvals_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pending_actions
          ADD COLUMN IF NOT EXISTS goal_id uuid,
          ADD COLUMN IF NOT EXISTS invocation_id uuid,
          ADD COLUMN IF NOT EXISTS goal_depth smallint,
          ADD COLUMN IF NOT EXISTS goal_exposed boolean,
          ADD COLUMN IF NOT EXISTS goal_origin text NOT NULL DEFAULT 'legacy',
          ADD COLUMN IF NOT EXISTS goal_effect_id uuid,
          ADD COLUMN IF NOT EXISTS goal_capability text,
          ADD COLUMN IF NOT EXISTS goal_args_digest text,
          ADD COLUMN IF NOT EXISTS goal_approved_args_digest text;
        ALTER TABLE pending_actions ADD CONSTRAINT pending_goal_lineage CHECK ((
          (goal_origin='legacy' AND goal_id IS NULL AND invocation_id IS NULL
           AND goal_depth IS NULL AND goal_exposed IS NULL AND goal_effect_id IS NULL
           AND goal_capability IS NULL AND goal_args_digest IS NULL
           AND goal_approved_args_digest IS NULL)
          OR (goal_origin IN ('owner','obligation','third_party','staffer')
           AND goal_id IS NOT NULL AND invocation_id IS NOT NULL
           AND goal_depth IS NOT NULL AND goal_depth IN (0,1) AND goal_exposed IS NOT NULL
           AND goal_effect_id IS NOT NULL AND id=goal_effect_id
           AND goal_capability IS NOT NULL
           AND goal_capability ~ '^[a-zA-Z0-9_.-]+__[a-zA-Z0-9_.-]+$'
           AND split_part(goal_capability,'__',2)=tool_name
           AND goal_args_digest IS NOT NULL AND goal_args_digest ~ '^[0-9a-f]{64}$'
           AND (goal_approved_args_digest IS NULL
                OR goal_approved_args_digest ~ '^[0-9a-f]{64}$'))
        ) IS TRUE);
        ALTER TABLE pending_actions DROP CONSTRAINT pending_actions_status_check;
        ALTER TABLE pending_actions ADD CONSTRAINT pending_actions_status_check
          CHECK (status IN ('pending','approved','rejected','expired','executed',
                            'abandoned','cancelled'));
        CREATE UNIQUE INDEX IF NOT EXISTS pending_goal_effect_identity
          ON pending_actions(goal_id,goal_effect_id) WHERE goal_id IS NOT NULL;
        ALTER TABLE approval_events DROP CONSTRAINT approval_events_type_check;
        ALTER TABLE approval_events ADD CONSTRAINT approval_events_type_check CHECK (
          event_type IN ('action_queued','action_auto_approved','action_approved',
          'action_rejected','action_expired','action_execution_succeeded',
          'action_execution_failed','action_abandoned','action_cancelled',
          'rule_created','rule_revoked','promotion_suggested','promotion_confirmed',
          'promotion_dismissed','promotion_superseded','demotion_suggested',
          'demotion_confirmed','demotion_dismissed','approval_delivery_terminal',
          'approval_delivery_deferred'));
        CREATE TABLE IF NOT EXISTS approval_goal_effects (
          effect_id uuid PRIMARY KEY, action_id uuid NOT NULL UNIQUE CHECK(action_id=effect_id),
          goal_id uuid NOT NULL, owning_domain text NOT NULL,
          capability text NOT NULL, args_digest text NOT NULL CHECK(args_digest ~ '^[0-9a-f]{64}$'),
          pending_action_id uuid UNIQUE REFERENCES pending_actions(id)
            ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
          stop_fenced boolean NOT NULL DEFAULT false,
          dispatch_intent_id uuid UNIQUE,
          approved_args_digest text CHECK(approved_args_digest ~ '^[0-9a-f]{64}$'),
          phase text NOT NULL CHECK(phase IN ('absent','pending','approved',
            'dispatch_intent','cancelled','negative_terminal')),
          state_version bigint NOT NULL DEFAULT 1 CHECK(state_version>0),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          CHECK(pending_action_id IS NULL OR pending_action_id=action_id),
          CHECK(owning_domain ~ '^[a-zA-Z0-9_.-]+$'
            AND position('__' IN owning_domain)=0
            AND capability ~ '^[a-zA-Z0-9_.-]+__[a-zA-Z0-9_.-]+$'
            AND split_part(capability,'__',1)=owning_domain)
        );
        CREATE TABLE IF NOT EXISTS approval_goal_receipts (
          receipt_id uuid PRIMARY KEY,
          effect_id uuid NOT NULL REFERENCES approval_goal_effects(effect_id) ON DELETE RESTRICT,
          state_version bigint NOT NULL CHECK(state_version>0),
          goal_id uuid NOT NULL, owning_domain text NOT NULL, action_id uuid NOT NULL,
          capability text NOT NULL, args_digest text NOT NULL,
          outcome text NOT NULL CHECK(outcome IN ('parked','pending','approved',
                                                'cancelled','fenced_absent')),
          pending_action_id uuid REFERENCES pending_actions(id) ON DELETE RESTRICT,
          digest text NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(effect_id,state_version,outcome),
          CHECK((outcome='fenced_absent' AND pending_action_id IS NULL)
            OR (outcome<>'fenced_absent' AND pending_action_id IS NOT NULL
                AND pending_action_id=action_id))
        );
    """)
    op.execute("""
      CREATE FUNCTION guard_approval_goal_effect() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
      BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN
          RAISE EXCEPTION 'goal evidence immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.owning_domain<>TG_TABLE_SCHEMA THEN
          RAISE EXCEPTION 'goal domain mismatch' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' THEN
          IF NEW.state_version<>1 THEN
            RAISE EXCEPTION 'goal initial version' USING ERRCODE='23514';
          END IF;
        ELSE
          IF ROW(NEW.effect_id,NEW.action_id,NEW.goal_id,NEW.owning_domain,
                 NEW.capability,NEW.args_digest,NEW.created_at)
            IS DISTINCT FROM ROW(OLD.effect_id,OLD.action_id,OLD.goal_id,OLD.owning_domain,
                                 OLD.capability,OLD.args_digest,OLD.created_at)
            OR (OLD.pending_action_id IS NOT NULL
                AND NEW.pending_action_id IS DISTINCT FROM OLD.pending_action_id)
            OR (OLD.approved_args_digest IS NOT NULL
                AND NEW.approved_args_digest IS DISTINCT FROM OLD.approved_args_digest)
            OR (OLD.dispatch_intent_id IS NOT NULL
                AND NEW.dispatch_intent_id IS DISTINCT FROM OLD.dispatch_intent_id)
            OR (OLD.stop_fenced AND NOT NEW.stop_fenced)
            OR NEW.state_version<>OLD.state_version+1 THEN
            RAISE EXCEPTION 'goal identity or version immutable' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER approval_goal_effect_guard AFTER INSERT OR UPDATE OR DELETE
        ON approval_goal_effects FOR EACH ROW EXECUTE FUNCTION guard_approval_goal_effect();
      CREATE TRIGGER approval_goal_effect_no_truncate BEFORE TRUNCATE
        ON approval_goal_effects FOR EACH STATEMENT EXECUTE FUNCTION guard_approval_goal_effect();

      CREATE FUNCTION guard_approval_goal_receipt() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
      DECLARE g record;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'goal receipt immutable' USING ERRCODE='23514';
        END IF;
        EXECUTE format('SELECT * FROM %I.approval_goal_effects WHERE effect_id=$1 FOR UPDATE',
                       TG_TABLE_SCHEMA) INTO g USING NEW.effect_id;
        IF g.effect_id IS NULL OR ROW(NEW.goal_id,NEW.owning_domain,NEW.action_id,NEW.capability,
                            NEW.args_digest,NEW.state_version,NEW.pending_action_id)
          IS DISTINCT FROM ROW(g.goal_id,g.owning_domain,g.action_id,g.capability,
                               g.args_digest,g.state_version,g.pending_action_id)
          OR NOT ((g.phase='pending' AND NOT g.stop_fenced
                   AND g.dispatch_intent_id IS NULL AND NEW.outcome IN ('parked','pending'))
            OR (g.phase='approved' AND NOT g.stop_fenced
                AND g.dispatch_intent_id IS NULL AND NEW.outcome='approved')
            OR (g.phase='cancelled' AND g.stop_fenced
                AND g.dispatch_intent_id IS NULL AND NEW.outcome='cancelled')
            OR (g.phase='absent' AND g.stop_fenced AND g.pending_action_id IS NULL
                AND g.dispatch_intent_id IS NULL AND NEW.outcome='fenced_absent')) THEN
          RAISE EXCEPTION 'goal receipt transition mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER approval_goal_receipt_guard AFTER INSERT OR UPDATE OR DELETE
        ON approval_goal_receipts FOR EACH ROW EXECUTE FUNCTION guard_approval_goal_receipt();
      CREATE TRIGGER approval_goal_receipt_no_truncate BEFORE TRUNCATE
        ON approval_goal_receipts FOR EACH STATEMENT EXECUTE FUNCTION guard_approval_goal_receipt();

      CREATE FUNCTION guard_pending_goal_action() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
      DECLARE g record; a record;
      BEGIN
        IF TG_OP='TRUNCATE' THEN
          EXECUTE format('SELECT 1 FROM %I.approval_goal_effects LIMIT 1',TG_TABLE_SCHEMA) INTO g;
          IF g IS NOT NULL THEN
            RAISE EXCEPTION 'retained goal evidence' USING ERRCODE='23514';
          END IF;
          RETURN NULL;
        END IF;
        IF TG_OP='DELETE' THEN a:=OLD; ELSE a:=NEW; END IF;
        IF TG_OP='INSERT' OR a.goal_origin<>'legacy' THEN
          EXECUTE format('SELECT singleton FROM %I.approval_delivery_rollout '
                         'WHERE singleton IS TRUE FOR SHARE',TG_TABLE_SCHEMA);
          PERFORM pg_advisory_xact_lock(hashtext('approval-delivery:' || TG_TABLE_SCHEMA));
          EXECUTE format('SELECT * FROM %I.approval_goal_effects WHERE effect_id=$1 FOR UPDATE',
                         TG_TABLE_SCHEMA) INTO g USING a.id;
          IF g.effect_id IS NOT NULL THEN
            IF a.goal_origin='legacy' OR TG_OP='DELETE'
              OR ROW(a.goal_id,a.goal_effect_id,a.goal_capability,a.goal_args_digest,a.id)
                 IS DISTINCT FROM ROW(g.goal_id,g.effect_id,g.capability,g.args_digest,g.action_id)
              OR (g.stop_fenced AND TG_OP='INSERT') THEN
              RAISE EXCEPTION 'goal admission fenced' USING ERRCODE='23514';
            END IF;
          ELSIF a.goal_origin<>'legacy' THEN
            RAISE EXCEPTION 'goal gate required' USING ERRCODE='23514';
          END IF;
        END IF;
        IF TG_OP='UPDATE' THEN
          IF ROW(OLD.id,OLD.goal_id,OLD.invocation_id,OLD.goal_depth,OLD.goal_origin,
                 OLD.goal_effect_id,OLD.goal_capability,OLD.goal_args_digest)
              IS DISTINCT FROM ROW(NEW.id,NEW.goal_id,NEW.invocation_id,NEW.goal_depth,
                 NEW.goal_origin,
                 NEW.goal_effect_id,NEW.goal_capability,NEW.goal_args_digest)
            OR (OLD.goal_origin='legacy' AND NEW.goal_origin<>'legacy')
            OR (OLD.goal_exposed IS TRUE AND NEW.goal_exposed IS NOT TRUE)
            OR (OLD.goal_approved_args_digest IS NOT NULL AND
                 ROW(NEW.goal_approved_args_digest,NEW.tool_args,NEW.tool_name)
                 IS DISTINCT FROM ROW(OLD.goal_approved_args_digest,OLD.tool_args,OLD.tool_name))
          THEN
            RAISE EXCEPTION 'goal action identity or dispatch fenced' USING ERRCODE='23514';
          END IF;
          -- Legacy UPDATE did not SELECT INTO g. Branch procedurally before
          -- preparing any RECORD-field expression, not merely with SQL AND.
          IF OLD.goal_origin<>'legacy' THEN
            IF (OLD.status IN ('cancelled','rejected','expired','abandoned','executed')
                  AND NEW.status<>OLD.status)
              OR (NEW.status='approved' AND NEW.goal_approved_args_digest IS NULL)
              OR NEW.status='executed'
              OR (OLD.status='approved' AND NEW.status NOT IN ('approved','abandoned','cancelled'))
              OR (NEW.status='cancelled'
                  AND (g.dispatch_intent_id IS NOT NULL OR NOT g.stop_fenced)) THEN
              RAISE EXCEPTION 'goal action identity or dispatch fenced' USING ERRCODE='23514';
            END IF;
          END IF;
        END IF;
        IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
      END $$;
      CREATE TRIGGER pending_goal_action_guard BEFORE INSERT OR UPDATE OR DELETE
        ON pending_actions FOR EACH ROW EXECUTE FUNCTION guard_pending_goal_action();
      CREATE TRIGGER pending_goal_action_final_guard AFTER INSERT OR UPDATE OR DELETE
        ON pending_actions FOR EACH ROW EXECUTE FUNCTION guard_pending_goal_action();
      CREATE TRIGGER pending_goal_action_no_truncate BEFORE TRUNCATE
        ON pending_actions FOR EACH STATEMENT EXECUTE FUNCTION guard_pending_goal_action();
    """)
    # A deferred final-state check sees action+gate+receipt writes together. It
    # never revalidates historical receipt versions against later phases.
    op.execute("""
      CREATE FUNCTION validate_approval_goal_final() RETURNS trigger
      LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$
      DECLARE g record; a record; eid uuid; required text[]; n integer;
      BEGIN
        IF TG_TABLE_NAME='pending_actions' THEN
          IF NEW.goal_origin='legacy' THEN RETURN NULL; END IF;
          eid:=NEW.goal_effect_id;
        ELSE eid:=NEW.effect_id; END IF;
        EXECUTE format('SELECT * FROM %I.approval_goal_effects WHERE effect_id=$1',
                       TG_TABLE_SCHEMA) INTO g USING eid;
        IF g.effect_id IS NULL THEN
          RAISE EXCEPTION 'missing final goal gate' USING ERRCODE='23514';
        END IF;
        IF g.pending_action_id IS NULL THEN
          IF g.phase<>'absent' OR NOT g.stop_fenced OR g.dispatch_intent_id IS NOT NULL
            OR g.approved_args_digest IS NOT NULL THEN
            RAISE EXCEPTION 'invalid absent goal fence' USING ERRCODE='23514';
          END IF;
          required:=ARRAY['fenced_absent'];
        ELSE
          EXECUTE format('SELECT * FROM %I.pending_actions WHERE id=$1',TG_TABLE_SCHEMA)
            INTO a USING g.pending_action_id;
          IF a.id IS NULL OR ROW(a.goal_id,a.goal_effect_id,a.goal_capability,a.goal_args_digest,
                                a.goal_approved_args_digest)
            IS DISTINCT FROM ROW(g.goal_id,g.effect_id,g.capability,g.args_digest,
                                 g.approved_args_digest) THEN
            RAISE EXCEPTION 'final goal action mismatch' USING ERRCODE='23514';
          END IF;
          IF g.phase='pending' AND a.status='pending' AND NOT g.stop_fenced
            AND g.dispatch_intent_id IS NULL AND g.approved_args_digest IS NULL THEN
            required:=ARRAY['parked','pending'];
          ELSIF g.phase='approved' AND a.status='approved' AND NOT g.stop_fenced
            AND g.dispatch_intent_id IS NULL AND g.approved_args_digest IS NOT NULL THEN
            required:=ARRAY['approved'];
          ELSIF g.phase='negative_terminal' AND a.status IN ('rejected','expired','abandoned')
            AND NOT g.stop_fenced AND g.dispatch_intent_id IS NULL THEN
            required:=ARRAY[]::text[];
          ELSIF g.phase='cancelled' AND a.status IN ('cancelled','rejected','expired','abandoned')
            AND g.stop_fenced AND g.dispatch_intent_id IS NULL THEN
            required:=ARRAY['cancelled'];
          ELSIF g.phase='dispatch_intent' AND a.status='approved'
            AND g.dispatch_intent_id IS NOT NULL AND g.approved_args_digest IS NOT NULL THEN
            required:=ARRAY[]::text[];
          ELSE RAISE EXCEPTION 'invalid final goal state' USING ERRCODE='23514';
          END IF;
        END IF;
        EXECUTE format('SELECT count(*) FROM %I.approval_goal_receipts '
                       'WHERE effect_id=$1 AND state_version=$2 AND outcome=ANY($3)',
                       TG_TABLE_SCHEMA) INTO n USING g.effect_id,g.state_version,required;
        IF n<>cardinality(required) THEN
          RAISE EXCEPTION 'missing final goal receipt' USING ERRCODE='23514';
        END IF;
        RETURN NULL;
      END $$;
      CREATE CONSTRAINT TRIGGER approval_goal_final AFTER INSERT OR UPDATE
        ON approval_goal_effects DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_approval_goal_final();
      CREATE CONSTRAINT TRIGGER pending_goal_final AFTER INSERT OR UPDATE
        ON pending_actions DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_approval_goal_final();
    """)
    op.execute("""
      DO $$ DECLARE s text:=current_schema(); r text:='butler_'||current_schema()||'_rw';
                    t text;
      BEGIN
        FOREACH t IN ARRAY ARRAY['approval_goal_effects','approval_goal_receipts'] LOOP
          EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',s,t);
          EXECUTE format('REVOKE ALL ON %I.%I FROM PUBLIC',s,t);
          IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
            EXECUTE format('GRANT SELECT,INSERT ON %I.%I TO %I',s,t,r);
            IF t='approval_goal_effects' THEN
              EXECUTE format('GRANT UPDATE ON %I.%I TO %I',s,t,r);
            END IF;
            EXECUTE format('CREATE POLICY owning_goal_select ON %I.%I FOR SELECT TO %I '
                           'USING(current_user=%L)',s,t,r,r);
            EXECUTE format('CREATE POLICY owning_goal_insert ON %I.%I FOR INSERT TO %I '
                           'WITH CHECK(current_user=%L)',s,t,r,r);
            IF t='approval_goal_effects' THEN
              EXECUTE format('CREATE POLICY owning_goal_update ON %I.%I FOR UPDATE TO %I '
                             'USING(current_user=%L) WITH CHECK(current_user=%L)',s,t,r,r,r);
            END IF;
          END IF;
        END LOOP;
        IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
          EXECUTE format('REVOKE TRIGGER ON %I.pending_actions FROM %I',s,r);
          FOREACH t IN ARRAY ARRAY['guard_approval_goal_effect','guard_approval_goal_receipt',
                                   'guard_pending_goal_action','validate_approval_goal_final'] LOOP
            EXECUTE format('REVOKE ALL ON FUNCTION %I.%I() FROM PUBLIC',s,t);
            EXECUTE format('REVOKE ALL ON FUNCTION %I.%I() FROM %I',s,t,r);
          END LOOP;
        END IF;
      END $$;
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        LOCK TABLE approval_goal_effects,approval_goal_receipts,pending_actions
          IN ACCESS EXCLUSIVE MODE;
        IF EXISTS(SELECT 1 FROM approval_goal_effects)
          OR EXISTS(SELECT 1 FROM approval_goal_receipts)
          OR EXISTS(SELECT 1 FROM pending_actions WHERE goal_origin<>'legacy')
          OR EXISTS(SELECT 1 FROM approval_events WHERE event_type='action_cancelled') THEN
          RAISE EXCEPTION 'cannot downgrade retained goal approval evidence' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP TRIGGER pending_goal_final ON pending_actions;
      DROP TRIGGER pending_goal_action_guard ON pending_actions;
      DROP TRIGGER pending_goal_action_final_guard ON pending_actions;
      DROP TRIGGER pending_goal_action_no_truncate ON pending_actions;
      DROP TABLE approval_goal_receipts;
      DROP TABLE approval_goal_effects;
      DROP FUNCTION validate_approval_goal_final();
      DROP FUNCTION guard_pending_goal_action();
      DROP FUNCTION guard_approval_goal_receipt();
      DROP FUNCTION guard_approval_goal_effect();
      ALTER TABLE pending_actions DROP CONSTRAINT pending_goal_lineage;
      DROP INDEX pending_goal_effect_identity;
      ALTER TABLE pending_actions DROP COLUMN goal_id,DROP COLUMN invocation_id,
        DROP COLUMN goal_depth,DROP COLUMN goal_exposed,DROP COLUMN goal_origin,
        DROP COLUMN goal_effect_id,DROP COLUMN goal_capability,DROP COLUMN goal_args_digest,
        DROP COLUMN goal_approved_args_digest;
      ALTER TABLE approval_events DROP CONSTRAINT approval_events_type_check;
      ALTER TABLE approval_events ADD CONSTRAINT approval_events_type_check CHECK(
        event_type IN ('action_queued','action_auto_approved','action_approved',
          'action_rejected','action_expired','action_abandoned','action_execution_succeeded',
          'action_execution_failed','rule_created','rule_revoked','promotion_suggested',
          'promotion_confirmed','promotion_dismissed','promotion_superseded',
          'demotion_suggested','demotion_confirmed','demotion_dismissed',
          'approval_delivery_terminal'));
      ALTER TABLE pending_actions DROP CONSTRAINT pending_actions_status_check;
      ALTER TABLE pending_actions ADD CONSTRAINT pending_actions_status_check CHECK (
        status IN ('pending','approved','rejected','expired','executed','abandoned'));
    """)
