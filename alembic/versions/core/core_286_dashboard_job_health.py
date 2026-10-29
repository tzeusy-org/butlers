"""Persist value-free dashboard supervised-job health.

Revision ID: core_286
Revises: core_282 (actual published base; reserved 283/284/285 are not adopted).
"""

from alembic import op

revision = "core_286"
down_revision = "core_282"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        DO $job_health_owner$
        DECLARE owner_name NAME; bootstrap_name NAME; table_name TEXT;
        BEGIN
            -- core_278 bootstrap exposes this immutable configuration to the
            -- ordinary migration login. Do not infer the API owner from the
            -- migration's current creator, database owner, or a session GUC.
            SELECT cfg.migration_role, cfg.bootstrap_role
              INTO STRICT owner_name, bootstrap_name
              FROM dashboard_attention.bootstrap_configuration cfg
              JOIN pg_namespace n ON n.nspname='dashboard_attention'
              JOIN pg_class c ON c.relnamespace=n.oid
                  AND c.relname='bootstrap_configuration' AND c.relkind='r'
              JOIN pg_roles b ON b.rolname=cfg.bootstrap_role
              JOIN pg_roles m ON m.rolname=cfg.migration_role
             WHERE cfg.singleton AND b.rolsuper AND c.relowner=b.oid
               AND n.nspowner=b.oid
               AND obj_description(n.oid,'pg_namespace')='butlers:dashboard-attention:core_278'
               AND m.rolcanlogin AND NOT (m.rolsuper OR m.rolbypassrls
                   OR m.rolcreatedb OR m.rolcreaterole OR m.rolreplication)
               AND current_user IN (cfg.migration_role,cfg.bootstrap_role);
            FOREACH table_name IN ARRAY
                ARRAY['dashboard_job_health','dashboard_job_transitions'] LOOP
                IF EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname='public' AND c.relname=table_name
                      AND (c.relkind<>'r' OR c.relpersistence<>'p'
                        OR pg_get_userbyid(c.relowner) NOT IN (owner_name,bootstrap_name)))
                    OR EXISTS (SELECT FROM pg_policies p WHERE p.schemaname='public'
                        AND p.tablename=table_name AND p.policyname<>'dashboard_job_owner')
                THEN RAISE EXCEPTION 'dashboard job health ownership or policy is untrusted';
                END IF;
            END LOOP;
        END $job_health_owner$;
        CREATE TABLE IF NOT EXISTS public.dashboard_job_health (
            job_name TEXT PRIMARY KEY CHECK (job_name ~ '^[a-z][a-z0-9_]{0,63}$'),
            job_class TEXT NOT NULL CHECK (job_class IN ('periodic','continuous')),
            process_id UUID NOT NULL,
            process_started_at TIMESTAMPTZ NOT NULL,
            interval_s DOUBLE PRECISION NOT NULL CHECK (interval_s > 0 AND interval_s < 'Infinity'),
            grace_s DOUBLE PRECISION NOT NULL CHECK (grace_s >= 0 AND grace_s < 'Infinity'),
            state TEXT NOT NULL DEFAULT 'starting'
                CHECK (state IN ('starting','ok','degraded','error','disabled','unknown')),
            reason TEXT NOT NULL DEFAULT 'started'
                CHECK (reason IN ('started','tick_ok','tick_failed','partial_failure','connected',
                    'reconnecting','loop_crashed','loop_returned','disabled','stopped')),
            last_success_at TIMESTAMPTZ,
            last_progress_at TIMESTAMPTZ,
            error_count BIGINT NOT NULL DEFAULT 0 CHECK (error_count>=0),
            consecutive_failures BIGINT NOT NULL DEFAULT 0 CHECK (consecutive_failures>=0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS public.dashboard_job_transitions (
            id UUID PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            job_name TEXT NOT NULL REFERENCES public.dashboard_job_health(job_name)
                ON DELETE CASCADE,
            process_id UUID NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('starting','ok','degraded','error','disabled','unknown')),
            reason TEXT NOT NULL
                CHECK (reason IN ('started','tick_ok','tick_failed','partial_failure','connected',
                    'reconnecting','loop_crashed','loop_returned','disabled','stopped')),
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS dashboard_job_transition_recent
            ON public.dashboard_job_transitions(job_name,occurred_at DESC,id DESC);
        REVOKE ALL ON public.dashboard_job_health, public.dashboard_job_transitions FROM PUBLIC;
        ALTER TABLE public.dashboard_job_health ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dashboard_job_health FORCE ROW LEVEL SECURITY;
        ALTER TABLE public.dashboard_job_transitions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE public.dashboard_job_transitions FORCE ROW LEVEL SECURITY;
        DO $$ DECLARE table_name TEXT; owner_name NAME; BEGIN
            SELECT migration_role INTO STRICT owner_name
                FROM dashboard_attention.bootstrap_configuration WHERE singleton;
            FOREACH table_name IN ARRAY
                ARRAY['dashboard_job_health','dashboard_job_transitions'] LOOP
                EXECUTE format('ALTER TABLE public.%I OWNER TO %I',table_name,owner_name);
                EXECUTE format('DROP POLICY IF EXISTS dashboard_job_owner ON public.%I',table_name);
                EXECUTE format('CREATE POLICY dashboard_job_owner ON public.%I TO %I '
                    'USING (current_user = %L::name) WITH CHECK (current_user = %L::name)',
                    table_name,owner_name,owner_name,owner_name);
            END LOOP;
        END $$;
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS public.dashboard_job_transitions;
        DROP TABLE IF EXISTS public.dashboard_job_health;
    """)
