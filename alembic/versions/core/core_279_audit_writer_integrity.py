"""Bind new audit writers to the inserting DB role and enforce append-only rows.

Historical writer identity is unknown and stays NULL. This is the runtime-role
boundary, not protection against the shared table owner disabling triggers.

Revision ID: core_279
Revises: core_278
"""

from alembic import op

revision = "core_279"
down_revision = "core_278"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.audit_log ADD COLUMN IF NOT EXISTS writer_role TEXT;

        CREATE OR REPLACE FUNCTION public.audit_log_bind_writer()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        BEGIN
            IF NEW.writer_role IS NOT NULL
               AND NEW.writer_role IS DISTINCT FROM current_user::text THEN
                RAISE EXCEPTION 'audit writer identity must match the inserting database role'
                    USING ERRCODE = '42501';
            END IF;
            NEW.writer_role := current_user::text;
            RETURN NEW;
        END;
        $body$;

        CREATE OR REPLACE FUNCTION public.audit_log_refuse_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, pg_temp
        AS $body$
        BEGIN
            RAISE EXCEPTION 'audit history is append-only' USING ERRCODE = '42501';
        END;
        $body$;

        DROP TRIGGER IF EXISTS audit_log_writer_identity ON public.audit_log;
        CREATE TRIGGER audit_log_writer_identity
            BEFORE INSERT ON public.audit_log
            FOR EACH ROW EXECUTE FUNCTION public.audit_log_bind_writer();
        ALTER TABLE public.audit_log ENABLE ALWAYS TRIGGER audit_log_writer_identity;

        DROP TRIGGER IF EXISTS audit_log_no_row_mutation ON public.audit_log;
        CREATE TRIGGER audit_log_no_row_mutation
            BEFORE UPDATE OR DELETE ON public.audit_log
            FOR EACH ROW EXECUTE FUNCTION public.audit_log_refuse_mutation();
        ALTER TABLE public.audit_log ENABLE ALWAYS TRIGGER audit_log_no_row_mutation;

        DROP TRIGGER IF EXISTS audit_log_no_truncate ON public.audit_log;
        CREATE TRIGGER audit_log_no_truncate
            BEFORE TRUNCATE ON public.audit_log
            FOR EACH STATEMENT EXECUTE FUNCTION public.audit_log_refuse_mutation();
        ALTER TABLE public.audit_log ENABLE ALWAYS TRIGGER audit_log_no_truncate;
    """)


def downgrade() -> None:
    # Older code ignores the additive column. Keep every event and its observed
    # identity; rolling back enforcement must not erase provenance/history.
    op.execute("""
        DROP TRIGGER IF EXISTS audit_log_no_truncate ON public.audit_log;
        DROP TRIGGER IF EXISTS audit_log_no_row_mutation ON public.audit_log;
        DROP TRIGGER IF EXISTS audit_log_writer_identity ON public.audit_log;
        DROP FUNCTION IF EXISTS public.audit_log_refuse_mutation();
        DROP FUNCTION IF EXISTS public.audit_log_bind_writer();
    """)
