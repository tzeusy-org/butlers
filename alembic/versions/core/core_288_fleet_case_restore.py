"""Owner-only offline Fleet history insertion; runtime authority stays fenced.

Revision ID: core_288
Revises: core_282
"""

from alembic import op

revision = "core_288"
down_revision = "core_282"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $fleet_owner$
        DECLARE owner_name name; item record;
        BEGIN
          SELECT pg_get_userbyid(relowner) INTO owner_name
          FROM pg_class WHERE oid='public.fleet_cases'::regclass;
          IF owner_name IS NULL OR 3 <> (
            SELECT count(*) FROM pg_class WHERE oid IN (
              'public.fleet_cases'::regclass, 'public.fleet_case_evidence'::regclass,
              'public.fleet_case_links'::regclass)
            AND relowner=owner_name::regrole
          ) THEN RAISE EXCEPTION 'Fleet restore requires one existing table owner'; END IF;
          FOR item IN SELECT * FROM (VALUES
              ('fleet_cases'),('fleet_case_links')) AS t(relation_name)
          LOOP
            EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
              item.relation_name || '_restore_owner', item.relation_name);
            EXECUTE format('CREATE POLICY %I ON public.%I FOR INSERT '
              'WITH CHECK (current_user = %L::name)',
              item.relation_name || '_restore_owner', item.relation_name, owner_name);
          END LOOP;
        END $fleet_owner$;
        CREATE OR REPLACE FUNCTION public.fleet_case_restore_row(
          relation_name text, payload jsonb
        ) RETURNS void LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp SET row_security = on
        AS $fleet_row$
        BEGIN
          CASE relation_name
            WHEN 'fleet_cases' THEN
              INSERT INTO public.fleet_cases
              SELECT (jsonb_populate_record(NULL::public.fleet_cases,payload)).*;
            WHEN 'fleet_case_links' THEN
              INSERT INTO public.fleet_case_links
              SELECT (jsonb_populate_record(NULL::public.fleet_case_links,payload)).*;
            ELSE RAISE EXCEPTION 'unsupported Fleet restore relation';
          END CASE;
        END $fleet_row$;
        DO $fleet_acl$
        DECLARE owner_name name; grantee_name name;
        BEGIN
          SELECT pg_get_userbyid(relowner) INTO STRICT owner_name
            FROM pg_class WHERE oid='public.fleet_cases'::regclass;
          EXECUTE format('ALTER FUNCTION public.fleet_case_restore_row(text,jsonb) OWNER TO %I',
                         owner_name);
          REVOKE ALL ON FUNCTION public.fleet_case_restore_row(text,jsonb) FROM PUBLIC;
          FOR grantee_name IN
            SELECT DISTINCT r.rolname FROM pg_proc p
              CROSS JOIN LATERAL aclexplode(p.proacl) a
              JOIN pg_roles r ON r.oid=a.grantee
            WHERE p.oid='public.fleet_case_restore_row(text,jsonb)'::regprocedure
              AND a.grantee<>p.proowner
          LOOP
            EXECUTE format('REVOKE ALL ON FUNCTION public.fleet_case_restore_row(text,jsonb) '
                           'FROM %I',grantee_name);
          END LOOP;
        END $fleet_acl$;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS public.fleet_case_restore_row(text,jsonb)")
    op.execute("DROP POLICY IF EXISTS fleet_cases_restore_owner ON public.fleet_cases")
    op.execute("DROP POLICY IF EXISTS fleet_case_links_restore_owner ON public.fleet_case_links")
