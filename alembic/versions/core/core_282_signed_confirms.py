"""Durable informational confirms, dark until the goal-runtime switch.

Revision ID: core_282
Revises: core_281
"""

from alembic import op

revision = "core_282"
down_revision = "core_281"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.pending_confirms (
            id UUID PRIMARY KEY,
            producer TEXT NOT NULL,
            producer_key TEXT NOT NULL,
            prompt JSONB NOT NULL CHECK (jsonb_typeof(prompt) = 'object'),
            recipient_entity UUID NOT NULL,
            recipient_sender TEXT NOT NULL,
            recipient_chat TEXT NOT NULL,
            recipient_owner BOOLEAN NOT NULL,
            goal_delivery_ref TEXT UNIQUE,
            sink TEXT,
            sink_ref TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            expires_at TIMESTAMPTZ NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'answered', 'expired')),
            answer_index INTEGER CHECK (answer_index BETWEEN 0 AND 3),
            answered_at TIMESTAMPTZ,
            answer_channel TEXT CHECK (answer_channel IN ('telegram','telegram_owner','dashboard')),
            application_status TEXT NOT NULL DEFAULT 'idle'
                CHECK (application_status IN ('idle', 'pending', 'applied', 'error')),
            application_receipt JSONB,
            claim_token UUID,
            claim_until TIMESTAMPTZ,
            message_ref TEXT,
            delivery_status TEXT NOT NULL DEFAULT 'new'
                CHECK (delivery_status IN ('new','sending','confirmed','ambiguous','failed')),
            rendered_status TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            UNIQUE (producer, producer_key),
            CHECK (expires_at > created_at),
            CHECK ((sink IS NULL AND sink_ref IS NULL AND goal_delivery_ref IS NOT NULL)
                OR (sink IS NOT NULL AND sink_ref IS NOT NULL AND goal_delivery_ref IS NULL)),
            CHECK ((status='answered' AND answer_index IS NOT NULL AND answered_at IS NOT NULL)
                OR (status IN ('pending','expired') AND answer_index IS NULL)),
            CHECK (status <> 'pending' OR application_status='idle')
        );
        CREATE INDEX IF NOT EXISTS pending_confirms_work
            ON public.pending_confirms (application_status, claim_until, expires_at);
        CREATE INDEX IF NOT EXISTS pending_confirms_owner_page
            ON public.pending_confirms (created_at DESC,id DESC) WHERE recipient_owner;
        ALTER TABLE public.pending_confirms ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.pending_confirms FORCE ROW LEVEL SECURITY;
        REVOKE ALL ON public.pending_confirms FROM PUBLIC;
        DO $$ DECLARE role_name TEXT; BEGIN
            FOREACH role_name IN ARRAY ARRAY['butler_switchboard_rw','butler_messenger_rw'] LOOP
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
                    EXECUTE format('GRANT SELECT, INSERT, UPDATE ON public.pending_confirms TO %I',
                                   role_name);
                END IF;
            END LOOP;
        END $$;
        DROP POLICY IF EXISTS confirm_service ON public.pending_confirms;
        CREATE POLICY confirm_service ON public.pending_confirms
            USING (current_user IN ('butler_switchboard_rw','butler_messenger_rw'))
            WITH CHECK (current_user IN ('butler_switchboard_rw','butler_messenger_rw'));
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.guard_confirm_identity() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$ BEGIN
            IF ROW(NEW.id,NEW.producer,NEW.producer_key,NEW.prompt,NEW.recipient_entity,
                   NEW.recipient_sender,NEW.recipient_chat,NEW.recipient_owner,
                   NEW.goal_delivery_ref,NEW.sink,NEW.sink_ref,NEW.created_at,NEW.expires_at)
               IS DISTINCT FROM
               ROW(OLD.id,OLD.producer,OLD.producer_key,OLD.prompt,OLD.recipient_entity,
                   OLD.recipient_sender,OLD.recipient_chat,OLD.recipient_owner,
                   OLD.goal_delivery_ref,OLD.sink,OLD.sink_ref,OLD.created_at,OLD.expires_at)
            THEN RAISE EXCEPTION 'confirm prompt identity is immutable'; END IF;
            IF OLD.status <> 'pending' AND
               ROW(NEW.status,NEW.answer_index,NEW.answered_at,NEW.answer_channel) IS DISTINCT FROM
               ROW(OLD.status,OLD.answer_index,OLD.answered_at,OLD.answer_channel)
            THEN RAISE EXCEPTION 'confirm choice is immutable'; END IF;
            NEW.updated_at := clock_timestamp();
            RETURN NEW;
        END $$;
        REVOKE ALL ON FUNCTION public.guard_confirm_identity() FROM PUBLIC;
        DROP TRIGGER IF EXISTS confirm_identity_guard ON public.pending_confirms;
        CREATE TRIGGER confirm_identity_guard BEFORE UPDATE ON public.pending_confirms
            FOR EACH ROW EXECUTE FUNCTION public.guard_confirm_identity();
    """)
    op.execute("""
        DO $$ DECLARE owner_name NAME; BEGIN
            SELECT pg_get_userbyid(relowner) INTO STRICT owner_name
            FROM pg_class WHERE oid='public.pending_confirms'::regclass;
            DROP POLICY IF EXISTS confirm_backup_read ON public.pending_confirms;
            DROP POLICY IF EXISTS confirm_restore_owner ON public.pending_confirms;
            EXECUTE format(
                'CREATE POLICY confirm_backup_read ON public.pending_confirms '
                'FOR SELECT USING (current_user = %L::name)', owner_name
            );
            EXECUTE format(
                'CREATE POLICY confirm_restore_owner ON public.pending_confirms '
                'FOR INSERT WITH CHECK (current_user = %L::name)', owner_name
            );
        END $$;
        CREATE OR REPLACE FUNCTION public.confirm_restore_row(payload JSONB) RETURNS void
        LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog SET row_security=on AS $$ BEGIN
            IF current_user <> (
                SELECT pg_get_userbyid(relowner) FROM pg_class
                WHERE oid='public.pending_confirms'::regclass
            ) THEN
                RAISE EXCEPTION 'confirm restore requires the exact table owner';
            END IF;
            INSERT INTO public.pending_confirms
            SELECT (jsonb_populate_record(NULL::public.pending_confirms,payload)).*;
        END $$;
        REVOKE ALL ON FUNCTION public.confirm_restore_row(JSONB) FROM PUBLIC;
        DO $$ DECLARE role_name NAME; BEGIN
            FOR role_name IN SELECT rolname FROM pg_roles
                WHERE rolname LIKE 'butler\\_%\\_rw' ESCAPE '\\'
                   OR rolname='connector_writer'
            LOOP
                EXECUTE format(
                    'REVOKE ALL ON FUNCTION public.confirm_restore_row(JSONB) FROM %I',role_name
                );
            END LOOP;
        END $$;
    """)


def downgrade():
    # Shared coordination history survives per-schema rollback. Another schema
    # may still use this table; discarding accepted answers is never reversible.
    pass
