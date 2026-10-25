-- Operator-only roles/extensions/ownership preparation. No application function
-- bodies, application-table grants or future-object privileges live here.
-- The source compiler supplies butlers.source_manifest on this owned session;
-- psql callers use the documented `butlers db bootstrap` entry point.
\set ON_ERROR_STOP on
BEGIN;
DO $prepare_authority$
DECLARE
    manifest jsonb := NULLIF(current_setting('butlers.source_manifest', true), '')::jsonb;
    migration_name name;
    item text;
    actual_owner name;
BEGIN
    IF session_user <> current_user
       OR NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname=session_user), false) THEN
        RAISE EXCEPTION 'explicit privileged bootstrap identity required' USING ERRCODE='42501';
    END IF;
    IF manifest IS NULL OR jsonb_typeof(manifest) <> 'object'
       OR (SELECT array_agg(key ORDER BY key) FROM jsonb_object_keys(manifest) key)
          <> ARRAY['database','migration_role','restore_executor','roles','schemas']::text[]
       OR manifest->>'database' IS DISTINCT FROM current_database()
       OR jsonb_typeof(manifest->'roles') <> 'array'
       OR jsonb_typeof(manifest->'schemas') <> 'array'
       OR manifest->>'restore_executor' IS DISTINCT FROM 'restore_drill_executor'
       OR manifest->>'migration_role' !~ '^[a-z_][a-z0-9_]{0,62}$' THEN
        RAISE EXCEPTION 'source-compiled deployment manifest required' USING ERRCODE='55000';
    END IF;
    migration_name := (manifest->>'migration_role')::name;
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname=migration_name
       AND NOT rolsuper AND NOT rolcreaterole AND NOT rolcreatedb
       AND NOT rolreplication AND NOT rolbypassrls)
       OR migration_name=current_user
       OR migration_name IN (SELECT value FROM jsonb_array_elements_text(manifest->'roles')) THEN
        RAISE EXCEPTION 'distinct provisioned migration login required' USING ERRCODE='55000';
    END IF;
    IF EXISTS(SELECT FROM jsonb_array_elements_text(manifest->'roles') role
          WHERE role.value !~ '^[a-z_][a-z0-9_]{0,62}$')
       OR EXISTS(SELECT FROM jsonb_array_elements_text(manifest->'schemas') schema
          WHERE schema.value !~ '^[a-z_][a-z0-9_]{0,62}$')
       OR (SELECT count(*)<>count(DISTINCT value)
           FROM jsonb_array_elements_text(manifest->'roles'))
       OR (SELECT count(*)<>count(DISTINCT value)
           FROM jsonb_array_elements_text(manifest->'schemas')) THEN
        RAISE EXCEPTION 'ambiguous deployment identities' USING ERRCODE='55000';
    END IF;
    -- Validate existing ownership before any preparation. There is no implicit
    -- adoption of another principal's namespace or of application objects.
    FOR item IN SELECT value FROM jsonb_array_elements_text(manifest->'schemas') LOOP
        SELECT pg_get_userbyid(nspowner) INTO actual_owner FROM pg_namespace WHERE nspname=item;
        IF actual_owner IS NOT NULL AND actual_owner NOT IN
              (migration_name, current_user, 'pg_database_owner') THEN
            RAISE EXCEPTION 'explicit schema ownership handoff required' USING ERRCODE='55000';
        END IF;
    END LOOP;
    IF EXISTS(SELECT FROM pg_roles WHERE rolname='dashboard_attention_owner'
          AND shobj_description(oid,'pg_authid') IS DISTINCT FROM
              'butlers:dashboard-attention:core_278')
       OR EXISTS(SELECT FROM pg_roles WHERE rolname='dashboard_stepup_owner'
          AND shobj_description(oid,'pg_authid') IS DISTINCT FROM
              'butlers:dashboard-stepup:core_277') THEN
        RAISE EXCEPTION 'independent dashboard owner is untrusted' USING ERRCODE='55000';
    END IF;
    FOR item IN SELECT value FROM jsonb_array_elements_text(manifest->'roles') LOOP
        IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname=item) THEN
            EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE '
                'NOCREATEDB NOREPLICATION NOBYPASSRLS',item);
            -- These independent, later dashboard boundaries retain their own
            -- source provenance. Only a newly created role receives its mark;
            -- an existing untrusted role is never adopted by stamping it.
            IF item='dashboard_attention_owner' THEN
                COMMENT ON ROLE dashboard_attention_owner
                    IS 'butlers:dashboard-attention:core_278';
            ELSIF item='dashboard_stepup_owner' THEN
                COMMENT ON ROLE dashboard_stepup_owner
                    IS 'butlers:dashboard-stepup:core_277';
            END IF;
        END IF;
        EXECUTE format('ALTER ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE '
            'NOCREATEDB NOREPLICATION NOBYPASSRLS',item);
    END LOOP;
    IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='restore_drill_executor') THEN
        CREATE ROLE restore_drill_executor LOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
          CREATEDB NOREPLICATION NOBYPASSRLS;
    END IF;
    -- No password, service activation or membership is created here.
    ALTER ROLE restore_drill_executor NOINHERIT NOSUPERUSER NOCREATEROLE
      CREATEDB NOREPLICATION NOBYPASSRLS;
    FOR item IN SELECT value FROM jsonb_array_elements_text(manifest->'schemas') LOOP
        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I',item,migration_name);
        EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC',item);
        -- Schema ownership itself is an explicit operator precondition. An
        -- existing schema is not re-owned behind a completion marker.
        EXECUTE format('GRANT USAGE,CREATE ON SCHEMA %I TO %I',item,migration_name);
    END LOOP;
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM PUBLIC',current_database());
    -- No broad default GRANT is supplied. Exact source rows are the migrator's
    -- finalization authority after the actual objects exist.
END;
$prepare_authority$;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;
COMMIT;
