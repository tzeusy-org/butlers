"""Fenced dashboard fast-answer runtime, intent lane, and deterministic reply.

Revision ID: core_267
Revises: core_265

The existing dashboard-turn row is the first lock for every transition, so Stop
and reply persistence have one winner. Runtime table access stays behind the
Switchboard-only control surface, including after broad bootstrap grants.
"""

from alembic import op

revision = "core_267"
down_revision = "core_265"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.dashboard_conversation_turns
          ADD COLUMN IF NOT EXISTS intent_lane text
          CHECK (intent_lane IN ('answer', 'non_answer'));
        ALTER TABLE public.dashboard_conversation_turn_sessions
          DROP CONSTRAINT IF EXISTS dashboard_conversation_turn_sessions_phase_check;
        ALTER TABLE public.dashboard_conversation_turn_sessions
          ADD CONSTRAINT dashboard_conversation_turn_sessions_phase_check
          CHECK (phase IN ('classification', 'route', 'fast_answer'));
        CREATE TABLE IF NOT EXISTS public.dashboard_fast_answer_runtimes (
          message_id uuid PRIMARY KEY REFERENCES public.dashboard_conversation_turns(message_id)
            ON DELETE CASCADE,
          session_id uuid NOT NULL UNIQUE,
          owner_instance_id uuid NOT NULL,
          lease_generation bigint NOT NULL DEFAULT 1 CHECK (lease_generation > 0),
          heartbeat_at timestamptz NOT NULL,
          lease_expires_at timestamptz NOT NULL,
          reconcile_anchor_expires_at timestamptz,
          reconcile_deadline_at timestamptz,
          phase text NOT NULL DEFAULT 'catalog'
            CHECK (phase IN ('catalog', 'classification', 'reads', 'phrasing', 'reply')),
          state text NOT NULL DEFAULT 'active'
            CHECK (state IN ('active', 'fallback', 'pending_reconciliation',
              'pending_cancellation', 'completed', 'failed', 'cancelled', 'ambiguous')),
          reply_claimed_at timestamptz,
          reply_digest text,
          source_envelopes jsonb,
          reply_message_id uuid REFERENCES public.dashboard_messages(id),
          failure_reason text CHECK (failure_reason IS NULL OR failure_reason IN
            ('fast_answer_runtime_failed', 'fast_answer_runtime_outcome_unknown')),
          attribution jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(attribution)='array'),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CHECK ((reconcile_anchor_expires_at IS NULL) = (reconcile_deadline_at IS NULL)),
          CHECK (reconcile_deadline_at IS NULL OR
            reconcile_deadline_at = reconcile_anchor_expires_at + interval '15 minutes')
        );
        ALTER TABLE public.dashboard_fast_answer_runtimes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dashboard_fast_answer_runtimes FORCE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS fast_answer_definer_only ON public.dashboard_fast_answer_runtimes;
        CREATE POLICY fast_answer_definer_only ON public.dashboard_fast_answer_runtimes
          USING (current_user = pg_get_userbyid((SELECT relowner FROM pg_class
            WHERE oid = 'public.dashboard_fast_answer_runtimes'::regclass)))
          WITH CHECK (current_user = pg_get_userbyid((SELECT relowner FROM pg_class
            WHERE oid = 'public.dashboard_fast_answer_runtimes'::regclass)));
        REVOKE ALL ON public.dashboard_fast_answer_runtimes FROM PUBLIC;
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.dashboard_fast_answer_now()
        RETURNS timestamptz LANGUAGE sql VOLATILE SET search_path = pg_catalog
        AS $$ SELECT clock_timestamp() $$;

        CREATE OR REPLACE FUNCTION public.dashboard_turn_intent_lane(p_message uuid, p_lane text)
        RETURNS text LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE old_lane text;
        BEGIN
          IF p_lane IS NOT NULL THEN
            PERFORM public.dashboard_turn_require_role('butler_switchboard_rw');
            IF p_lane NOT IN ('answer', 'non_answer') THEN
              RAISE EXCEPTION 'invalid dashboard intent lane' USING ERRCODE='22023';
            END IF;
            SELECT intent_lane INTO old_lane FROM public.dashboard_conversation_turns
              WHERE message_id=p_message FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'missing dashboard turn'; END IF;
            IF old_lane IS NOT NULL AND old_lane <> p_lane THEN
              RAISE EXCEPTION 'dashboard intent lane conflict' USING ERRCODE='23505';
            END IF;
            UPDATE public.dashboard_conversation_turns SET intent_lane=p_lane
              WHERE message_id=p_message AND intent_lane IS NULL;
          END IF;
          RETURN (SELECT intent_lane FROM public.dashboard_conversation_turns
            WHERE message_id=p_message);
        END $$;

        CREATE OR REPLACE FUNCTION public.dashboard_fast_answer_register(
          p_message uuid, p_request uuid, p_session uuid, p_owner uuid)
        RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE t public.dashboard_conversation_turns%ROWTYPE;
          r public.dashboard_fast_answer_runtimes%ROWTYPE;
          stamp timestamptz := public.dashboard_fast_answer_now();
        BEGIN
          PERFORM public.dashboard_turn_require_role('butler_switchboard_rw');
          IF p_session IS NULL OR p_owner IS NULL OR p_request IS NULL THEN
            RAISE EXCEPTION 'missing fast runtime identity';
          END IF;
          SELECT * INTO t FROM public.dashboard_conversation_turns
            WHERE message_id=p_message FOR UPDATE;
          IF NOT FOUND OR t.request_id IS DISTINCT FROM p_request THEN
            RETURN jsonb_build_object('outcome','missing');
          END IF;
          IF t.terminal_at IS NOT NULL OR t.cancel_requested_at IS NOT NULL THEN
            RETURN jsonb_build_object('outcome','stopped');
          END IF;
          SELECT * INTO r FROM public.dashboard_fast_answer_runtimes WHERE message_id=p_message;
          IF FOUND THEN
            RETURN to_jsonb(r) || jsonb_build_object('outcome','existing');
          END IF;
          IF EXISTS (SELECT 1 FROM public.dashboard_conversation_turn_sessions
            WHERE message_id=p_message AND completed_at IS NULL) THEN
            RETURN jsonb_build_object('outcome','conflict');
          END IF;
          INSERT INTO public.dashboard_fast_answer_runtimes
            (message_id,session_id,owner_instance_id,heartbeat_at,lease_expires_at,
              created_at,updated_at)
            VALUES (p_message,p_session,p_owner,stamp,stamp+interval '60 seconds',stamp,stamp)
            RETURNING * INTO r;
          INSERT INTO public.dashboard_conversation_turn_sessions
            (message_id,session_id,request_id,butler_name,phase,registered_at,
              invoke_claimed_at,invoke_active)
            VALUES (p_message,p_session,p_request,'switchboard','fast_answer',stamp,stamp,true);
          RETURN to_jsonb(r) || jsonb_build_object('outcome','registered');
        END $$;

        CREATE OR REPLACE FUNCTION public.dashboard_fast_answer_control(
          p_message uuid,p_owner uuid,p_generation bigint,p_action text,p_data jsonb DEFAULT '{}')
        RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE t public.dashboard_conversation_turns%ROWTYPE;
          r public.dashboard_fast_answer_runtimes%ROWTYPE;
          stamp timestamptz := public.dashboard_fast_answer_now();
          reply_id uuid; existing public.dashboard_messages%ROWTYPE;
        BEGIN
          PERFORM public.dashboard_turn_require_role('butler_switchboard_rw');
          IF p_action IS NULL OR p_action NOT IN ('heartbeat','advance','attribution',
            'fallback','failed','cancelled','uncertain','reply_claim','reply') OR
            jsonb_typeof(p_data) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'invalid fast runtime operation' USING ERRCODE='22023';
          END IF;
          SELECT * INTO t FROM public.dashboard_conversation_turns
            WHERE message_id=p_message FOR UPDATE;
          SELECT * INTO r FROM public.dashboard_fast_answer_runtimes
            WHERE message_id=p_message FOR UPDATE;
          IF NOT FOUND OR r.owner_instance_id IS DISTINCT FROM p_owner OR
            r.lease_generation IS DISTINCT FROM p_generation THEN
            RETURN jsonb_build_object('outcome','fenced');
          END IF;
          IF p_action='reply' AND r.reply_message_id IS NOT NULL THEN
            SELECT * INTO existing FROM public.dashboard_messages WHERE id=r.reply_message_id;
            IF r.reply_digest IS DISTINCT FROM p_data->>'digest' OR
              t.conversation_id::text IS DISTINCT FROM p_data->>'conversation_id' OR
              existing.content IS DISTINCT FROM p_data->>'message' OR
              r.source_envelopes IS DISTINCT FROM p_data->'sources' OR
              existing.sources IS DISTINCT FROM p_data->'source_labels' OR
              existing.citations IS DISTINCT FROM p_data->'citations' THEN
              RETURN jsonb_build_object('outcome','conflict');
            END IF;
            RETURN to_jsonb(r) || jsonb_build_object('outcome','completed');
          END IF;
          IF r.state <> 'active' OR r.reconcile_anchor_expires_at IS NOT NULL OR
            r.lease_expires_at <= stamp THEN RETURN jsonb_build_object('outcome','fenced'); END IF;
          IF t.cancel_requested_at IS NOT NULL AND p_action NOT IN ('cancelled','uncertain') THEN
            RETURN jsonb_build_object('outcome','stopped');
          END IF;
          IF t.terminal_at IS NOT NULL THEN RETURN jsonb_build_object('outcome','finished'); END IF;
          IF p_action='heartbeat' THEN
            UPDATE public.dashboard_fast_answer_runtimes SET heartbeat_at=stamp,
              lease_expires_at=stamp+interval '60 seconds',updated_at=stamp
              WHERE message_id=p_message;
          ELSIF p_action='advance' THEN
            IF p_data->>'phase' IS NULL OR
              p_data->>'phase' NOT IN ('catalog','classification','reads','phrasing','reply') OR
              array_position(ARRAY['catalog','classification','reads','phrasing','reply'],
                p_data->>'phase') < array_position(
                ARRAY['catalog','classification','reads','phrasing','reply'], r.phase) THEN
              RAISE EXCEPTION 'invalid fast runtime phase';
            END IF;
            UPDATE public.dashboard_fast_answer_runtimes SET phase=p_data->>'phase',updated_at=stamp
              WHERE message_id=p_message;
          ELSIF p_action='attribution' THEN
            IF NOT (p_data ?& ARRAY['purpose','requested_tier','effective_tier','runtime_type',
              'resolution_source','model','catalog_entry_id','execution_timeout','outcome']) OR
              p_data->>'purpose' NOT IN ('dashboard_fast_answer_classification',
              'dashboard_fast_answer_phrasing') OR
              p_data->>'requested_tier' IS DISTINCT FROM 'cheap' OR
              p_data->>'effective_tier' IS DISTINCT FROM 'cheap' OR
              p_data->>'runtime_type' IS DISTINCT FROM 'api' OR
              p_data->>'resolution_source' IS DISTINCT FROM 'catalog' OR
              p_data->>'outcome' NOT IN ('started','received','failed') OR
              coalesce(length(p_data->>'model'),0)=0 OR
              jsonb_typeof(p_data->'execution_timeout') IS DISTINCT FROM 'number' OR
              NOT (p_data->>'execution_timeout' ~ '^[1-9][0-9]*$') OR
              coalesce(p_data->>'catalog_entry_id','') !~
                '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$' OR
              (SELECT count(*) FROM jsonb_object_keys(p_data))<>9 THEN
              RAISE EXCEPTION 'invalid fast model attribution';
            END IF;
            UPDATE public.dashboard_fast_answer_runtimes SET attribution=attribution||
              jsonb_build_array(p_data),updated_at=stamp WHERE message_id=p_message;
          ELSIF p_action IN ('fallback','failed','cancelled') THEN
            IF (r.reply_claimed_at IS NOT NULL AND p_action <> 'cancelled') OR
              (p_action='cancelled' AND
              t.cancel_requested_at IS NULL) THEN
              RETURN jsonb_build_object('outcome','conflict'); END IF;
            UPDATE public.dashboard_fast_answer_runtimes SET state=p_action,reply_claimed_at=NULL,
              failure_reason=CASE WHEN p_action='failed' THEN 'fast_answer_runtime_failed' END,
              updated_at=stamp WHERE message_id=p_message;
            UPDATE public.dashboard_conversation_turn_sessions SET invoke_active=false,
              completed_at=stamp,success=(p_action='fallback'),
              cancel_acknowledged_at=CASE WHEN p_action='cancelled' THEN stamp END
              WHERE session_id=r.session_id;
            IF p_action <> 'fallback' THEN
              UPDATE public.dashboard_conversation_turns SET terminal_state=
                CASE WHEN p_action='cancelled' THEN 'cancelled' ELSE 'failed' END,
                terminal_at=stamp,cancel_confirmed_at=CASE WHEN p_action='cancelled' THEN stamp END,
                updated_at=stamp WHERE message_id=p_message;
            END IF;
          ELSIF p_action='uncertain' THEN
            UPDATE public.dashboard_fast_answer_runtimes SET
              state=CASE WHEN t.cancel_requested_at IS NOT NULL THEN 'pending_cancellation'
                ELSE 'pending_reconciliation' END,updated_at=stamp WHERE message_id=p_message;
            -- Keep invoke/reply claims active: absence of a task proves no settlement.
          ELSIF p_action='reply_claim' THEN
            IF r.reply_claimed_at IS NOT NULL OR p_data->>'digest' IS NULL OR
              NOT (p_data->>'digest' ~ '^[0-9a-f]{64}$') THEN
              RETURN jsonb_build_object('outcome','conflict');
            END IF;
            UPDATE public.dashboard_fast_answer_runtimes SET reply_claimed_at=stamp,
              reply_digest=p_data->>'digest',phase='reply',updated_at=stamp
              WHERE message_id=p_message;
          ELSIF p_action='reply' THEN
            IF r.reply_claimed_at IS NULL OR r.reply_digest IS DISTINCT FROM p_data->>'digest' OR
              t.conversation_id::text IS DISTINCT FROM p_data->>'conversation_id' OR
              jsonb_typeof(p_data->'sources') IS DISTINCT FROM 'array' OR
              jsonb_array_length(p_data->'sources')=0 OR
              jsonb_typeof(p_data->'source_labels') IS DISTINCT FROM 'array' OR
              jsonb_typeof(p_data->'citations') IS DISTINCT FROM 'array' OR
              jsonb_typeof(p_data->'message') IS DISTINCT FROM 'string' OR
              length(btrim(p_data->>'message'))=0 THEN
              RETURN jsonb_build_object('outcome','conflict');
            END IF;
            reply_id := (p_data->>'reply_id')::uuid;
            INSERT INTO public.dashboard_messages (id,conversation_id,role,content,created_at,
              session_id,request_id,sources,citations,routed_butler)
              VALUES (reply_id,t.conversation_id,'assistant',p_data->>'message',stamp,
                r.session_id,t.request_id,p_data->'source_labels',p_data->'citations','concierge')
              ON CONFLICT (id) DO NOTHING;
            SELECT * INTO existing FROM public.dashboard_messages WHERE id=reply_id;
            IF existing.conversation_id IS DISTINCT FROM t.conversation_id OR
              existing.content IS DISTINCT FROM p_data->>'message' OR
              existing.sources IS DISTINCT FROM p_data->'source_labels' OR
              existing.session_id IS DISTINCT FROM r.session_id THEN
              RAISE EXCEPTION 'fast reply identity conflict' USING ERRCODE='23505';
            END IF;
            UPDATE public.dashboard_fast_answer_runtimes SET state='completed',
              reply_message_id=reply_id,source_envelopes=p_data->'sources',
              reply_claimed_at=NULL,updated_at=stamp
              WHERE message_id=p_message;
            UPDATE public.dashboard_conversation_turn_sessions SET invoke_active=false,
              completed_at=stamp,success=true WHERE session_id=r.session_id;
            UPDATE public.dashboard_conversation_turns SET terminal_state='completed',
              terminal_at=stamp,updated_at=stamp WHERE message_id=p_message;
            UPDATE public.dashboard_conversations SET updated_at=stamp WHERE id=t.conversation_id;
          END IF;
          SELECT * INTO r FROM public.dashboard_fast_answer_runtimes WHERE message_id=p_message;
          RETURN to_jsonb(r) || jsonb_build_object('outcome',
            CASE WHEN r.state='active' THEN 'accepted' ELSE r.state END);
        END $$;

        CREATE OR REPLACE FUNCTION public.dashboard_fast_answer_reconcile(p_owner uuid)
        RETURNS SETOF jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
        DECLARE key uuid; t public.dashboard_conversation_turns%ROWTYPE;
          r public.dashboard_fast_answer_runtimes%ROWTYPE;
          stamp timestamptz := public.dashboard_fast_answer_now(); terminal text;
        BEGIN
          PERFORM public.dashboard_turn_require_role('butler_switchboard_rw');
          IF p_owner IS NULL THEN RAISE EXCEPTION 'missing reconciler owner'; END IF;
          FOR key IN SELECT message_id FROM public.dashboard_fast_answer_runtimes
            WHERE state IN ('active','pending_reconciliation','pending_cancellation')
              AND (lease_expires_at<=stamp OR reconcile_anchor_expires_at IS NOT NULL)
            ORDER BY message_id LOOP
            SELECT * INTO t FROM public.dashboard_conversation_turns
              WHERE message_id=key FOR UPDATE;
            SELECT * INTO r FROM public.dashboard_fast_answer_runtimes
              WHERE message_id=key FOR UPDATE;
            IF r.state NOT IN ('active','pending_reconciliation','pending_cancellation') OR
              (r.lease_expires_at>stamp AND r.reconcile_anchor_expires_at IS NULL)
              THEN CONTINUE; END IF;
            terminal := NULL;
            IF r.reply_message_id IS NOT NULL THEN terminal := 'completed';
            ELSIF t.terminal_state IN ('completed','cancelled','failed')
              THEN terminal:=t.terminal_state;
            ELSIF EXISTS (SELECT 1 FROM public.dashboard_conversation_turn_sessions
              WHERE session_id=r.session_id AND NOT invoke_active AND
                cancel_acknowledged_at IS NOT NULL) THEN terminal:='cancelled'; END IF;
            IF terminal IS NULL AND r.reconcile_anchor_expires_at IS NULL THEN
              UPDATE public.dashboard_fast_answer_runtimes SET
                reconcile_anchor_expires_at=r.lease_expires_at,
                reconcile_deadline_at=r.lease_expires_at+interval '15 minutes',
                lease_generation=lease_generation+1,owner_instance_id=p_owner,
                state=CASE WHEN t.cancel_requested_at IS NULL THEN 'pending_reconciliation'
                  ELSE 'pending_cancellation' END,updated_at=stamp
                WHERE message_id=key RETURNING * INTO r;
            END IF;
            IF terminal IS NULL AND stamp >= r.reconcile_deadline_at
              THEN terminal:='ambiguous'; END IF;
            IF terminal IS NOT NULL THEN
              UPDATE public.dashboard_fast_answer_runtimes SET state=terminal,
                reply_claimed_at=NULL,failure_reason=CASE WHEN terminal='ambiguous' THEN
                  'fast_answer_runtime_outcome_unknown' ELSE failure_reason END,
                updated_at=stamp WHERE message_id=key RETURNING * INTO r;
              UPDATE public.dashboard_conversation_turn_sessions SET invoke_active=false,
                completed_at=coalesce(completed_at,stamp),success=(terminal='completed')
                WHERE session_id=r.session_id;
              UPDATE public.dashboard_conversation_turns SET terminal_state=terminal,
                terminal_at=coalesce(terminal_at,stamp),updated_at=stamp WHERE message_id=key;
            END IF;
            RETURN NEXT to_jsonb(r);
          END LOOP;
        END $$;
    """)
    # Legacy turn functions have no generation argument. Keep their ordinary
    # behavior intact, but do not let them release or complete a fast claim.
    # The fenced functions above first settle their owning runtime row, then
    # project that same outcome into the existing turn/session tables.
    op.execute("""
        CREATE OR REPLACE FUNCTION public.dashboard_fast_answer_guard_legacy_projection()
        RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
        DECLARE r public.dashboard_fast_answer_runtimes%ROWTYPE;
        BEGIN
          IF TG_OP='INSERT' THEN
            SELECT * INTO r FROM public.dashboard_fast_answer_runtimes
              WHERE message_id=NEW.message_id;
            IF FOUND AND r.state<>'fallback' AND
              (NEW.session_id IS DISTINCT FROM r.session_id OR NEW.phase<>'fast_answer') THEN
              RAISE EXCEPTION 'fast runtime requires its single fenced session'
                USING ERRCODE='42501';
            END IF;
            RETURN NEW;
          END IF;
          SELECT * INTO r FROM public.dashboard_fast_answer_runtimes
            WHERE message_id=OLD.message_id;
          IF NOT FOUND OR r.state='fallback' THEN
            IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
          END IF;
          IF TG_TABLE_NAME='dashboard_conversation_turn_sessions' THEN
            IF OLD.session_id<>r.session_id THEN RETURN NEW; END IF;
            IF TG_OP='DELETE' OR r.state IN
              ('active','pending_reconciliation','pending_cancellation') OR
              NEW.invoke_active OR NEW.completed_at IS NULL OR
              NEW.success IS DISTINCT FROM (r.state='completed') OR
              (NEW.cancel_acknowledged_at IS NOT NULL AND r.state<>'cancelled') OR
              (OLD.completed_at IS NOT NULL AND NEW IS DISTINCT FROM OLD) THEN
              RAISE EXCEPTION 'fast runtime requires a fenced session projection'
                USING ERRCODE='42501';
            END IF;
          ELSIF TG_OP='DELETE' OR
            (r.state IN ('active','pending_reconciliation','pending_cancellation') AND
              (NEW.terminal_at IS DISTINCT FROM OLD.terminal_at OR
               NEW.terminal_state IS DISTINCT FROM OLD.terminal_state OR
               NEW.target_kind IS DISTINCT FROM OLD.target_kind OR
               NEW.target_butler IS DISTINCT FROM OLD.target_butler OR
               NEW.cancel_confirmed_at IS DISTINCT FROM OLD.cancel_confirmed_at)) OR
            (r.state NOT IN ('active','pending_reconciliation','pending_cancellation') AND
              (NEW.terminal_state IS DISTINCT FROM r.state OR NEW.terminal_at IS NULL OR
               (OLD.terminal_at IS NOT NULL AND
                 (NEW.terminal_at IS DISTINCT FROM OLD.terminal_at OR
                  NEW.terminal_state IS DISTINCT FROM OLD.terminal_state)))) THEN
            RAISE EXCEPTION 'fast runtime requires a fenced turn projection'
              USING ERRCODE='42501';
          END IF;
          RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS dashboard_fast_answer_session_projection
          ON public.dashboard_conversation_turn_sessions;
        CREATE TRIGGER dashboard_fast_answer_session_projection
          BEFORE INSERT OR UPDATE OR DELETE ON public.dashboard_conversation_turn_sessions
          FOR EACH ROW EXECUTE FUNCTION public.dashboard_fast_answer_guard_legacy_projection();
        DROP TRIGGER IF EXISTS dashboard_fast_answer_turn_projection
          ON public.dashboard_conversation_turns;
        CREATE TRIGGER dashboard_fast_answer_turn_projection
          BEFORE UPDATE OR DELETE ON public.dashboard_conversation_turns
          FOR EACH ROW EXECUTE FUNCTION public.dashboard_fast_answer_guard_legacy_projection();
    """)
    op.execute("""
        REVOKE ALL ON FUNCTION public.dashboard_fast_answer_register(uuid,uuid,uuid,uuid)
          FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.dashboard_fast_answer_control(uuid,uuid,bigint,text,jsonb)
          FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.dashboard_fast_answer_reconcile(uuid) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.dashboard_turn_intent_lane(uuid,text) FROM PUBLIC;
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='butler_switchboard_rw') THEN
            GRANT EXECUTE ON FUNCTION public.dashboard_fast_answer_register(uuid,uuid,uuid,uuid),
              public.dashboard_fast_answer_control(uuid,uuid,bigint,text,jsonb),
              public.dashboard_fast_answer_reconcile(uuid),
              public.dashboard_turn_intent_lane(uuid,text) TO butler_switchboard_rw;
            REVOKE ALL ON public.dashboard_fast_answer_runtimes FROM butler_switchboard_rw;
          END IF;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER IF EXISTS dashboard_fast_answer_session_projection
          ON public.dashboard_conversation_turn_sessions;
        DROP TRIGGER IF EXISTS dashboard_fast_answer_turn_projection
          ON public.dashboard_conversation_turns;
        DROP FUNCTION IF EXISTS public.dashboard_fast_answer_guard_legacy_projection();
        DROP FUNCTION IF EXISTS public.dashboard_fast_answer_reconcile(uuid);
        DROP FUNCTION IF EXISTS public.dashboard_fast_answer_control(uuid,uuid,bigint,text,jsonb);
        DROP FUNCTION IF EXISTS public.dashboard_fast_answer_register(uuid,uuid,uuid,uuid);
        DROP FUNCTION IF EXISTS public.dashboard_turn_intent_lane(uuid,text);
        DROP FUNCTION IF EXISTS public.dashboard_fast_answer_now();
        DROP TABLE IF EXISTS public.dashboard_fast_answer_runtimes;
        ALTER TABLE public.dashboard_conversation_turns DROP COLUMN IF EXISTS intent_lane;
        DELETE FROM public.dashboard_conversation_turn_sessions WHERE phase='fast_answer';
        ALTER TABLE public.dashboard_conversation_turn_sessions
          DROP CONSTRAINT dashboard_conversation_turn_sessions_phase_check;
        ALTER TABLE public.dashboard_conversation_turn_sessions
          ADD CONSTRAINT dashboard_conversation_turn_sessions_phase_check
          CHECK (phase IN ('classification','route'));
    """)
