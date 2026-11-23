"""Retained QA dashboard custody and fenced finding linkage (bu-u5euwb.17).

Additive and dark: this grants only the QA owner access, and registers no
transport, credential, control-plane projection or admission endpoint.
"""

from alembic import op

revision = "core_363"
down_revision = "core_362"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        SELECT pg_advisory_xact_lock(hashtextextended('butlers:qa-dashboard-inbox',0))
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.qa_dashboard_reports (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            terminal_action_id UUID NOT NULL,
            terminal_effect_id UUID NOT NULL,
            idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 1 AND 256),
            payload_hash TEXT NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{64}$'),
            payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
            inbox_state TEXT NOT NULL DEFAULT 'pending'
                CHECK (inbox_state IN ('pending','claimed','acknowledged')),
            generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
            lease_owner TEXT,
            lease_until TIMESTAMPTZ,
            finding_id UUID UNIQUE REFERENCES public.qa_findings(id) ON DELETE RESTRICT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (terminal_action_id,terminal_effect_id),
            CHECK (
                (inbox_state='pending' AND generation=0 AND lease_owner IS NULL
                 AND lease_until IS NULL AND finding_id IS NULL)
                OR (inbox_state='claimed' AND generation>0 AND lease_owner IS NOT NULL
                    AND lease_until IS NOT NULL AND finding_id IS NULL)
                OR (inbox_state='acknowledged' AND generation>0 AND lease_owner IS NOT NULL
                    AND lease_until IS NOT NULL AND finding_id IS NOT NULL)
            )
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_qa_dashboard_reports_pending
        ON public.qa_dashboard_reports (created_at,id)
        WHERE inbox_state IN ('pending','claimed')
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION public.qa_dashboard_reports_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('DELETE','TRUNCATE') THEN
                RAISE EXCEPTION 'dashboard custody must be retained' USING ERRCODE='23514';
            END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.inbox_state <> 'pending' THEN
                    RAISE EXCEPTION 'dashboard custody starts pending' USING ERRCODE='23514';
                END IF;
                RETURN NEW;
            END IF;
            IF (NEW.id,NEW.terminal_action_id,NEW.terminal_effect_id,NEW.idempotency_key,
                NEW.payload_hash,NEW.payload,NEW.created_at) IS DISTINCT FROM
               (OLD.id,OLD.terminal_action_id,OLD.terminal_effect_id,OLD.idempotency_key,
                OLD.payload_hash,OLD.payload,OLD.created_at)
               OR OLD.inbox_state='acknowledged' THEN
                RAISE EXCEPTION 'dashboard custody is immutable' USING ERRCODE='23514';
            END IF;
            IF NEW.inbox_state='claimed' AND NEW.generation=OLD.generation+1
               AND (OLD.inbox_state='pending' OR OLD.lease_until<=clock_timestamp())
               AND NEW.lease_until>clock_timestamp() THEN
                RETURN NEW;
            END IF;
            IF OLD.inbox_state='claimed' AND NEW.inbox_state='acknowledged'
               AND NEW.generation=OLD.generation AND NEW.lease_owner=OLD.lease_owner
               AND NEW.lease_until=OLD.lease_until AND OLD.lease_until>clock_timestamp() THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'dashboard claim is stale' USING ERRCODE='23514';
        END $$
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS qa_dashboard_reports_guard ON public.qa_dashboard_reports;
        CREATE TRIGGER qa_dashboard_reports_guard BEFORE INSERT OR UPDATE OR DELETE
        ON public.qa_dashboard_reports FOR EACH ROW
        EXECUTE FUNCTION public.qa_dashboard_reports_guard();
        DROP TRIGGER IF EXISTS qa_dashboard_reports_no_truncate ON public.qa_dashboard_reports;
        CREATE TRIGGER qa_dashboard_reports_no_truncate BEFORE TRUNCATE
        ON public.qa_dashboard_reports FOR EACH STATEMENT
        EXECUTE FUNCTION public.qa_dashboard_reports_guard()
    """)
    op.execute("REVOKE ALL ON public.qa_dashboard_reports FROM PUBLIC")
    op.execute("""
        DO $$ DECLARE role_name TEXT; BEGIN
            FOR role_name IN SELECT rolname FROM pg_roles
                WHERE starts_with(rolname,'butler_') AND right(rolname,3)='_rw'
            LOOP
                EXECUTE format('REVOKE ALL ON public.qa_dashboard_reports FROM %I',role_name);
            END LOOP;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='butler_qa_rw') THEN
                GRANT SELECT, INSERT, UPDATE ON public.qa_dashboard_reports TO butler_qa_rw;
            END IF;
        END $$
    """)
    op.execute("""
        ALTER TABLE public.qa_dashboard_reports ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS qa_dashboard_reports_owner ON public.qa_dashboard_reports;
        CREATE POLICY qa_dashboard_reports_owner ON public.qa_dashboard_reports
            FOR ALL TO PUBLIC USING (current_user='butler_qa_rw')
            WITH CHECK (current_user='butler_qa_rw')
    """)


def downgrade() -> None:
    # Binary rollback is safe: old code ignores this additive table. Schema
    # rollback retains both the rows and guards, even when another core plane
    # downgrades first. No migration may turn retained custody into not_found.
    pass
