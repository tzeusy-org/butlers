"""Durable default-OFF final-registry publication and role-owned current leases.

Revision ID: core_275
Revises: core_273 (delivered parent; core_274 is a separately held M6 slot).

Shared public metadata only; no private payloads, keys, tools or activation.
The bootstrap may regrant public DML; enabled RLS and immutable triggers remain
mandatory for non-owner runtimes. The stable database owner retains ordinary
backup/restore maintenance (non-FORCE); no runtime may assume that owner.
Core-only databases are supported.
"""

from alembic import op

revision = "core_275"
down_revision = "core_273"
branch_labels = None
depends_on = None

_DOMAINS = (
    "chronicler",
    "concierge",
    "education",
    "finance",
    "general",
    "health",
    "home",
    "lifestyle",
    "messenger",
    "qa",
    "relationship",
    "switchboard",
    "travel",
)


def _maintenance_owner(table: str) -> None:
    # Creator identity changes during managed downgrade/re-upgrade. Normalize
    # to the database's existing trusted owner, never the bootstrap executor or
    # a new runtime/definer role. This also preserves ordinary pg_dump's full
    # row_security=off contract without a new data exclusion or BYPASS role.
    roles = ",".join("'butler_" + domain + "_rw'" for domain in (*_DOMAINS, "calendar"))
    op.execute(f"""DO $$ DECLARE trusted_owner oid; BEGIN
        SELECT datdba INTO STRICT trusted_owner FROM pg_database WHERE datname=current_database();
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ({roles},'connector_writer')
            AND (rolsuper OR rolbypassrls OR pg_has_role(oid,trusted_owner,'MEMBER'))) THEN
            RAISE EXCEPTION 'Runtime may not bypass or assume catalog maintenance owner';
        END IF;
        EXECUTE format('ALTER TABLE public.%I OWNER TO %I',
            '{table}',pg_get_userbyid(trusted_owner));
        EXECUTE format('ALTER TABLE public.%I NO FORCE ROW LEVEL SECURITY','{table}');
    END $$""")


def upgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_275:catalog',0))")
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.capability_catalog_installations (
            schema_name TEXT PRIMARY KEY,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
    """)
    _maintenance_owner("capability_catalog_installations")
    op.execute("""
        ALTER TABLE public.capability_catalog_installations ENABLE ROW LEVEL SECURITY;
        DROP POLICY IF EXISTS catalog_installation_owner ON public.capability_catalog_installations;
        CREATE POLICY catalog_installation_owner ON public.capability_catalog_installations
            USING (current_user::text=(SELECT pg_get_userbyid(relowner) FROM pg_class
                WHERE oid='public.capability_catalog_installations'::regclass));
        INSERT INTO public.capability_catalog_installations(schema_name)
            VALUES(current_schema()) ON CONFLICT DO NOTHING;
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.capability_catalog_control (
            singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
            enabled BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        );
        INSERT INTO public.capability_catalog_control (singleton)
        SELECT true WHERE NOT EXISTS (SELECT 1 FROM public.capability_catalog_control);
        CREATE TABLE IF NOT EXISTS public.capability_catalog_members (
            domain TEXT PRIMARY KEY,
            included BOOLEAN NOT NULL,
            exclusion_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            CHECK ((included AND exclusion_reason IS NULL) OR
                   (NOT included AND length(exclusion_reason)>0))
        );
        CREATE TABLE IF NOT EXISTS public.capability_catalog_history (
            domain TEXT NOT NULL REFERENCES public.capability_catalog_members(domain),
            epoch BIGINT NOT NULL CHECK (epoch>0),
            instance_id UUID NOT NULL,
            revision_id UUID NOT NULL UNIQUE,
            body JSONB NOT NULL CHECK (jsonb_typeof(body)='object'),
            body_digest TEXT NOT NULL CHECK (body_digest ~ '^[0-9a-f]{64}$'),
            authorization_digest TEXT NOT NULL CHECK (authorization_digest ~ '^[0-9a-f]{64}$'),
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (domain,epoch),
            UNIQUE (domain,epoch,revision_id,instance_id),
            CHECK ((body->>'domain'=domain AND body->'version'='1'::jsonb
                   AND body ?& ARRAY['version','domain','generation_digest','descriptors']
                   AND body-ARRAY['version','domain','generation_digest','descriptors']='{}'::jsonb
                   AND (body->>'generation_digest') ~ '^[0-9a-f]{64}$'
                   AND jsonb_typeof(body->'descriptors')='array'
                   AND jsonb_array_length(body->'descriptors')>0) IS TRUE)
        );
        CREATE TABLE IF NOT EXISTS public.capability_catalog_current (
            domain TEXT PRIMARY KEY REFERENCES public.capability_catalog_members(domain),
            epoch BIGINT NOT NULL,
            instance_id UUID NOT NULL,
            revision_id UUID NOT NULL,
            active BOOLEAN NOT NULL,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            FOREIGN KEY (domain,epoch,revision_id,instance_id) REFERENCES
                public.capability_catalog_history(domain,epoch,revision_id,instance_id)
        );
    """)
    for domain in _DOMAINS:
        op.execute(f"""
            INSERT INTO public.capability_catalog_members(domain,included)
            SELECT '{domain}',true WHERE NOT EXISTS (
                SELECT 1 FROM public.capability_catalog_members WHERE domain='{domain}'
            )
        """)
    op.execute("""
        INSERT INTO public.capability_catalog_members(domain,included,exclusion_reason)
        SELECT 'calendar',false,'module, not a standalone roster daemon'
        WHERE NOT EXISTS (SELECT 1 FROM public.capability_catalog_members WHERE domain='calendar');

        CREATE OR REPLACE FUNCTION public.guard_capability_catalog() RETURNS trigger
        LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
            IF TG_TABLE_NAME='capability_catalog_history' THEN
                RAISE EXCEPTION 'catalog history is immutable' USING ERRCODE='23514';
            END IF;
            IF TG_OP='DELETE' THEN
                RAISE EXCEPTION 'catalog registration cannot be deleted' USING ERRCODE='23514';
            END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.epoch<>1 THEN
                    RAISE EXCEPTION 'catalog first epoch must be one' USING ERRCODE='23514';
                END IF;
            ELSE
                IF NEW.domain IS DISTINCT FROM OLD.domain
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.epoch NOT IN (OLD.epoch,OLD.epoch+1) THEN
                    RAISE EXCEPTION 'catalog generation must advance' USING ERRCODE='23514';
                END IF;
                IF NEW.epoch=OLD.epoch AND (
                    NEW.instance_id IS DISTINCT FROM OLD.instance_id
                    OR NEW.revision_id IS DISTINCT FROM OLD.revision_id
                    OR (NOT OLD.active AND NEW.active)
                    OR (OLD.expires_at<=clock_timestamp() AND NEW.active)
                ) THEN
                    RAISE EXCEPTION 'catalog lease cannot resurrect or change identity'
                        USING ERRCODE='23514';
                END IF;
            END IF;
            IF NEW.active AND NEW.expires_at>clock_timestamp()+interval '90 seconds' THEN
                RAISE EXCEPTION 'catalog lease exceeds bound' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
        END $$;
        DROP TRIGGER IF EXISTS immutable_capability_catalog_history
            ON public.capability_catalog_history;
        CREATE TRIGGER immutable_capability_catalog_history BEFORE UPDATE OR DELETE
            ON public.capability_catalog_history FOR EACH ROW
            EXECUTE FUNCTION public.guard_capability_catalog();
        DROP TRIGGER IF EXISTS fenced_capability_catalog_current
            ON public.capability_catalog_current;
        CREATE TRIGGER fenced_capability_catalog_current BEFORE INSERT OR UPDATE OR DELETE
            ON public.capability_catalog_current FOR EACH ROW
            EXECUTE FUNCTION public.guard_capability_catalog();

        CREATE OR REPLACE FUNCTION public.publish_capability_catalog(
            p_domain TEXT,p_instance UUID,p_revision UUID,p_body JSONB,
            p_digest TEXT,p_authorization TEXT,p_lease INTEGER,p_expected UUID
        ) RETURNS void LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        DECLARE next_epoch BIGINT; prior public.capability_catalog_current%ROWTYPE;
        BEGIN
            IF current_user::text <> 'butler_'||p_domain||'_rw' OR p_lease<>90 THEN
                RAISE EXCEPTION 'catalog publisher role required' USING ERRCODE='42501';
            END IF;
            PERFORM 1 FROM public.capability_catalog_control WHERE singleton AND enabled;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'catalog publication disabled' USING ERRCODE='42501';
            END IF;
            -- Serialize first publication and replacement even without a row.
            -- No cross-domain row lock or UPDATE authority on membership.
            PERFORM pg_advisory_xact_lock(hashtextextended('capability_catalog:'||p_domain,0));
            PERFORM 1 FROM public.capability_catalog_members
                WHERE domain=p_domain AND included;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'catalog member unavailable' USING ERRCODE='42501';
            END IF;
            SELECT * INTO prior FROM public.capability_catalog_current
                WHERE domain=p_domain FOR UPDATE OF capability_catalog_current;
            IF p_expected IS NULL THEN
                -- A genuinely new boot may replace the previous process. A boot
                -- that ever published cannot erase its fence and reacquire.
                IF EXISTS (SELECT 1 FROM public.capability_catalog_history
                           WHERE domain=p_domain AND instance_id=p_instance) THEN
                    RAISE EXCEPTION 'catalog publisher is not current' USING ERRCODE='23514';
                END IF;
            ELSIF prior.instance_id IS DISTINCT FROM p_instance
               OR prior.revision_id IS DISTINCT FROM p_expected
               OR prior.active IS DISTINCT FROM true
               OR prior.expires_at<=clock_timestamp() THEN
                RAISE EXCEPTION 'catalog publisher is not current' USING ERRCODE='23514';
            END IF;
            next_epoch:=COALESCE(prior.epoch+1,1);
            INSERT INTO public.capability_catalog_history
                (domain,epoch,instance_id,revision_id,body,body_digest,authorization_digest)
            VALUES(p_domain,next_epoch,p_instance,p_revision,p_body,p_digest,p_authorization);
            -- BEFORE INSERT enforces epoch one before conflict resolution.
            -- The serializer and locked prior row make these separate writes
            -- safe while preserving the distinct INSERT and UPDATE guards.
            IF prior.domain IS NULL THEN
                INSERT INTO public.capability_catalog_current
                    (domain,epoch,instance_id,revision_id,active,expires_at)
                VALUES(p_domain,next_epoch,p_instance,p_revision,true,
                       clock_timestamp()+make_interval(secs=>p_lease));
            ELSE
                UPDATE public.capability_catalog_current
                    SET epoch=next_epoch,instance_id=p_instance,revision_id=p_revision,
                        active=true,expires_at=clock_timestamp()+make_interval(secs=>p_lease),
                        updated_at=clock_timestamp()
                    WHERE domain=p_domain;
            END IF;
        END $$;
        CREATE OR REPLACE FUNCTION public.renew_capability_catalog(
            p_domain TEXT,p_instance UUID,p_revision UUID,p_lease INTEGER
        ) RETURNS BOOLEAN LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
        BEGIN
            IF current_user::text <> 'butler_'||p_domain||'_rw' OR p_lease<>90 THEN
                RAISE EXCEPTION 'catalog publisher role required' USING ERRCODE='42501';
            END IF;
            PERFORM 1 FROM public.capability_catalog_control WHERE singleton AND enabled;
            IF NOT FOUND THEN RETURN false; END IF;
            PERFORM 1 FROM public.capability_catalog_members
                WHERE domain=p_domain AND included;
            IF NOT FOUND THEN RETURN false; END IF;
            UPDATE public.capability_catalog_current
                SET expires_at=clock_timestamp()+make_interval(secs=>p_lease),
                    updated_at=clock_timestamp()
                WHERE domain=p_domain AND instance_id=p_instance AND revision_id=p_revision
                  AND active AND expires_at>clock_timestamp();
            RETURN FOUND;
        END $$;
        REVOKE ALL ON FUNCTION public.publish_capability_catalog(
            TEXT,UUID,UUID,JSONB,TEXT,TEXT,INTEGER,UUID) FROM PUBLIC;
        REVOKE ALL ON FUNCTION public.renew_capability_catalog(TEXT,UUID,UUID,INTEGER) FROM PUBLIC;
    """)
    for table in ("control", "members", "history", "current"):
        name = f"public.capability_catalog_{table}"
        op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
        _maintenance_owner(f"capability_catalog_{table}")
        op.execute(f"DROP POLICY IF EXISTS catalog_read ON {name}")
        op.execute(f"CREATE POLICY catalog_read ON {name} FOR SELECT USING (true)")
        if table in {"history", "current"}:
            op.execute(f"DROP POLICY IF EXISTS catalog_insert ON {name}")
            op.execute(f"""CREATE POLICY catalog_insert ON {name} FOR INSERT WITH CHECK (
                current_user::text='butler_'||domain||'_rw'
                AND EXISTS (SELECT 1 FROM public.capability_catalog_control
                            WHERE singleton AND enabled)
                AND EXISTS (SELECT 1 FROM public.capability_catalog_members m
                            WHERE m.domain={"capability_catalog_" + table}.domain AND m.included)
            )""")
        if table == "current":
            op.execute(f"DROP POLICY IF EXISTS catalog_update ON {name}")
            op.execute(f"""CREATE POLICY catalog_update ON {name} FOR UPDATE
                USING (current_user::text='butler_'||domain||'_rw')
                WITH CHECK (current_user::text='butler_'||domain||'_rw')""")
        for domain in _DOMAINS:
            role = f"butler_{domain}_rw"
            extra = (
                ",INSERT" if table == "history" else ",INSERT,UPDATE" if table == "current" else ""
            )
            op.execute(f"""DO $$ BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                    GRANT SELECT{extra} ON {name} TO {role};
                END IF;
            END $$""")
    for domain in _DOMAINS:
        role = f"butler_{domain}_rw"
        op.execute(f"""DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') THEN
                GRANT EXECUTE ON FUNCTION
                    public.publish_capability_catalog(TEXT,UUID,UUID,JSONB,TEXT,TEXT,INTEGER,UUID),
                    public.renew_capability_catalog(TEXT,UUID,UUID,INTEGER) TO {role};
            END IF;
        END $$""")


def downgrade() -> None:
    op.execute("SELECT pg_advisory_xact_lock(hashtextextended('butlers:core_275:catalog',0))")
    # Shared metadata is not silently discarded on one schema's downgrade.
    # Empty state only: operator must explicitly export/retire history first.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.capability_catalog_history) THEN
            RAISE EXCEPTION 'cannot discard catalog publication history';
        END IF;
    END $$""")
    # Track actual installation, not numeric revision ordering: M6's held
    # core_274 will descend from 275 despite having a lower numeric suffix.
    op.execute(
        "DELETE FROM public.capability_catalog_installations WHERE schema_name=current_schema()"
    )
    if (
        op.get_bind()
        .exec_driver_sql("SELECT EXISTS (SELECT 1 FROM public.capability_catalog_installations)")
        .scalar()
    ):
        return
    op.execute("""
        DROP FUNCTION IF EXISTS public.publish_capability_catalog(
            TEXT,UUID,UUID,JSONB,TEXT,TEXT,INTEGER,UUID);
        DROP FUNCTION IF EXISTS public.renew_capability_catalog(TEXT,UUID,UUID,INTEGER);
        DROP TABLE IF EXISTS public.capability_catalog_current;
        DROP TABLE IF EXISTS public.capability_catalog_history;
        DROP TABLE IF EXISTS public.capability_catalog_members;
        DROP TABLE IF EXISTS public.capability_catalog_control;
        DROP TABLE IF EXISTS public.capability_catalog_installations;
        DROP FUNCTION IF EXISTS public.guard_capability_catalog();
    """)
