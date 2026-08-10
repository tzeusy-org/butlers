-- init-db.sql: privileged bootstrap for Butlers runtime + migration ACLs
--
-- Run this script as a superuser (or database owner) against the target
-- application database before the first Alembic run. It is safe to re-run.
--
-- Usage:
--   psql -h <host> -U postgres -d butlers -f scripts/init-db.sql
--
-- Override the migration/runtime user (defaults to "butlers"):
--   PGOPTIONS="-c butlers.connecting_user=myappuser" \
--     psql -h <host> -U postgres -d butlers -f scripts/init-db.sql
--
-- What this script does:
--   1. Installs required extensions.
--   2. Creates managed schemas and runtime roles if missing.
--   3. Grants role membership so the migration/runtime user can SET ROLE.
--   4. Grants database/schema ACLs to runtime roles.
--   5. Grants schema CREATE/USAGE to the migration/runtime user so Alembic can
--      create objects while ownership stays with the object creator.
--   6. Configures ALTER DEFAULT PRIVILEGES FOR ROLE <migration user> so
--      future Alembic-created objects inherit the runtime ACLs immediately.
--
-- Design tradeoff:
--   To avoid a second privileged "grant repair" step after Alembic runs, this
--   bootstrap grants DML on public-schema tables created by the migration user
--   to all runtime roles. That is broader than the older targeted public-table
--   grants, but it keeps the operational model to a single privileged entrypoint.
--
-- Important ownership note:
--   Database and schema ownership remain with the privileged bootstrap role.
--   Tables, sequences, and functions created later by Alembic are owned by the
--   migration user (typically "butlers"), which is required for non-privileged
--   future ALTER TABLE migrations to succeed.

-- ── Extensions ────────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- ── Roles, schemas, grants, and default privileges ───────────────────────────

DO $$
DECLARE
    _butler_schemas TEXT[] := ARRAY[
        'chronicler',
        'education',
        'finance',
        'general',
        'health',
        'home',
        'lifestyle',
        'messenger',
        'qa',
        'relationship',
        'switchboard',
        'travel'
    ];
    _connector_schema TEXT := 'connectors';
    _switchboard_schema TEXT := 'switchboard';
    _managed_schemas TEXT[] := ARRAY[
        'chronicler',
        'education',
        'finance',
        'general',
        'health',
        'home',
        'lifestyle',
        'messenger',
        'qa',
        'relationship',
        'switchboard',
        'travel',
        'connectors'
    ];
    _butler_roles TEXT[] := ARRAY[
        'butler_chronicler_rw',
        'butler_education_rw',
        'butler_finance_rw',
        'butler_general_rw',
        'butler_health_rw',
        'butler_home_rw',
        'butler_lifestyle_rw',
        'butler_messenger_rw',
        'butler_qa_rw',
        'butler_relationship_rw',
        'butler_switchboard_rw',
        'butler_travel_rw'
    ];
    _connector_role TEXT := 'connector_writer';
    _all_runtime_roles TEXT[] := ARRAY[
        'butler_chronicler_rw',
        'butler_education_rw',
        'butler_finance_rw',
        'butler_general_rw',
        'butler_health_rw',
        'butler_home_rw',
        'butler_lifestyle_rw',
        'butler_messenger_rw',
        'butler_qa_rw',
        'butler_relationship_rw',
        'butler_switchboard_rw',
        'butler_travel_rw',
        'connector_writer'
    ];
    _restore_drill_executor_role TEXT := 'restore_drill_executor';
    _restore_drill_executor_owner_role TEXT := 'restore_drill_executor_owner';
    _restore_drill_executor_schema TEXT := 'restore_drill_executor';
    _migration_user TEXT := COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers');
    _db_name TEXT := current_database();
    _schema TEXT;
    _role TEXT;
    _table TEXT;
    _idx INTEGER;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _migration_user) THEN
        RAISE EXCEPTION
            'Migration/runtime user "%" does not exist. Create it first or set PGOPTIONS="-c butlers.connecting_user=<existing role>".',
            _migration_user;
    END IF;

    -- The migration/connecting user is shared by migrations and normal
    -- dashboard/runtime processes. It must never inherit the recovery-only
    -- database-creation capability.
    EXECUTE format('ALTER ROLE %I NOCREATEDB', _migration_user);

    -- Ensure the migration/runtime user can connect and create objects in the
    -- schemas it manages. Tables/functions created later remain owned by that
    -- user, which lets unprivileged Alembic runs alter them in future.
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', _db_name, _migration_user);
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA public TO %I', _migration_user);

    -- Create managed schemas up front so Alembic can run without privileged
    -- follow-up and so reruns can bootstrap newly-added schemas.
    FOREACH _schema IN ARRAY _managed_schemas LOOP
        EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', _schema);
        EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO %I', _schema, _migration_user);
    END LOOP;

    -- Create runtime roles if missing. LOGIN matches the current migration
    -- baseline; these roles are normally used through SET ROLE rather than
    -- direct logins.
    FOREACH _role IN ARRAY _all_runtime_roles LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _role) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOCREATEDB', _role);
            RAISE NOTICE 'Created role "%"', _role;
        END IF;
        EXECUTE format('ALTER ROLE %I NOCREATEDB', _role);
    END LOOP;

    -- Reserve a distinct executor role without making it a normal runtime
    -- login. The one-shot managed provisioner is the only path that enables
    -- LOGIN + CREATEDB and supplies its file-backed password.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _restore_drill_executor_role) THEN
        EXECUTE format(
            'CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
            _restore_drill_executor_role
        );
        RAISE NOTICE 'Reserved isolated restore-drill executor role "%"', _restore_drill_executor_role;
    END IF;
    -- Preserve the provisioner's LOGIN/CREATEDB attributes on re-runs while
    -- continuously repairing the remaining least-privilege attributes.
    EXECUTE format(
        'ALTER ROLE %I NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION',
        _restore_drill_executor_role
    );

    -- A distinct NOLOGIN owner holds the SECURITY DEFINER functions. The
    -- executor can call the narrow interface but cannot alter it, while the
    -- shared migration/dashboard login does not retain owner bypasses.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _restore_drill_executor_owner_role) THEN
        EXECUTE format(
            'CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
            _restore_drill_executor_owner_role
        );
        RAISE NOTICE 'Created restore-drill interface owner "%"', _restore_drill_executor_owner_role;
    END IF;
    EXECUTE format(
        'ALTER ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
        _restore_drill_executor_owner_role
    );
    EXECUTE format(
        'CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I',
        _restore_drill_executor_schema,
        _restore_drill_executor_owner_role
    );
    EXECUTE format(
        'ALTER SCHEMA %I OWNER TO %I',
        _restore_drill_executor_schema,
        _restore_drill_executor_owner_role
    );
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM PUBLIC', _restore_drill_executor_schema);

    -- No normal role may assume the executor or vice versa. The second revoke
    -- direction prevents a compromised executor from inheriting ordinary
    -- application privileges through a role membership.
    EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_role, _migration_user);
    EXECUTE format('REVOKE %I FROM %I', _migration_user, _restore_drill_executor_role);
    EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_owner_role, _migration_user);
    EXECUTE format('REVOKE %I FROM %I', _migration_user, _restore_drill_executor_owner_role);
    FOREACH _role IN ARRAY _all_runtime_roles LOOP
        EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_role, _role);
        EXECUTE format('REVOKE %I FROM %I', _role, _restore_drill_executor_role);
        EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_owner_role, _role);
        EXECUTE format('REVOKE %I FROM %I', _role, _restore_drill_executor_owner_role);
    END LOOP;

    -- The executor may connect but receives no general public-schema access.
    -- The post-migration fixed ownership finalizer grants only its dedicated
    -- schema USAGE and the two exact functions.
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', _db_name, _restore_drill_executor_role);
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM %I', _db_name, _restore_drill_executor_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA public FROM %I', _restore_drill_executor_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I', _restore_drill_executor_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM %I', _restore_drill_executor_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public FROM %I', _restore_drill_executor_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I', _restore_drill_executor_schema, _restore_drill_executor_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I', _restore_drill_executor_schema, _migration_user);
    FOREACH _role IN ARRAY _all_runtime_roles LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I', _restore_drill_executor_schema, _role);
    END LOOP;

    -- Allow the migration/runtime user to SET ROLE into each runtime role.
    -- On PostgreSQL 16+, bare membership is not sufficient if the membership
    -- row lacks SET TRUE. Re-issuing the grants with explicit option flags is
    -- idempotent and repairs older memberships that only had ADMIN OPTION.
    FOREACH _role IN ARRAY _all_runtime_roles LOOP
        EXECUTE format('GRANT %I TO %I WITH INHERIT TRUE', _role, _migration_user);
        EXECUTE format('GRANT %I TO %I WITH SET TRUE', _role, _migration_user);
    END LOOP;

    -- Butler runtime roles: own-schema DML + broad public DML for shared data.
    FOR _idx IN 1 .. array_length(_butler_schemas, 1) LOOP
        _schema := _butler_schemas[_idx];
        _role := _butler_roles[_idx];

        EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', _db_name, _role);
        EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO %I', _schema, _role);
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES ON ALL TABLES IN SCHEMA %I TO %I',
            _schema,
            _role
        );
        EXECUTE format(
            'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I',
            _schema,
            _role
        );
        EXECUTE format(
            'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO %I',
            _schema,
            _role
        );

        EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', _role);
        EXECUTE format(
            'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
            _role
        );
        EXECUTE format(
            'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I',
            _role
        );

        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE, TRIGGER, REFERENCES ON TABLES TO %I',
            _migration_user,
            _schema,
            _role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
            _migration_user,
            _schema,
            _role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT EXECUTE ON FUNCTIONS TO %I',
            _migration_user,
            _schema,
            _role
        );

        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
            _migration_user,
            _role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
            _migration_user,
            _role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM %I',
            _migration_user,
            _role
        );

        -- Butler roles may read connector-owned tables (for dashboards/routes).
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', _connector_schema, _role);
        EXECUTE format(
            'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I',
            _connector_schema,
            _role
        );
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO %I',
            _migration_user,
            _connector_schema,
            _role
        );
    END LOOP;

    -- Relationship follow-up jobs aggregate recent inbound interactions from the
    -- switchboard message inbox, so the relationship runtime role needs
    -- read-only access to the switchboard schema.
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', _switchboard_schema, 'butler_relationship_rw');
    EXECUTE format(
        'GRANT SELECT ON ALL TABLES IN SCHEMA %I TO %I',
        _switchboard_schema,
        'butler_relationship_rw'
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT ON TABLES TO %I',
        _migration_user,
        _switchboard_schema,
        'butler_relationship_rw'
    );

    -- Chronicler reads only the specific evidence surfaces declared in RFC 0014
    -- source compatibility contracts (butler_chronicler_rw = read-only role).
    --
    -- Approved evidence surfaces (v1):
    --   {schema}.sessions               — CoreSessionsAdapter (all butler schemas)
    --   {schema}.calendar_event_instances — CalendarCompletedAdapter (optional)
    --   relationship.entity_facts       — CoreSessionsAdapter contact resolution
    --                                      (bu-hjo3i) + comms.message_bursts
    --                                      participant resolution (bu-jc6htw.1)
    --
    -- Planned (PLANNED compatibility; tables may not yet exist):
    --   connectors.steam_play_history
    --   connectors.owntracks_points
    --   connectors.home_assistant_history
    --
    -- Adding a new evidence surface requires an explicit grant here plus a
    -- compatibility declaration in src/butlers/chronicler/contracts.py.
    -- Do NOT restore GRANT SELECT ON ALL TABLES — that violates RFC 0014 §D1.
    FOR _idx IN 1 .. array_length(_butler_schemas, 1) LOOP
        _schema := _butler_schemas[_idx];
        IF _schema = 'chronicler' THEN
            CONTINUE;
        END IF;
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', _schema, 'butler_chronicler_rw');
        -- sessions (CoreSessionsAdapter — present in every butler schema)
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = _schema AND table_name = 'sessions'
        ) THEN
            EXECUTE format(
                'GRANT SELECT ON TABLE %I.sessions TO butler_chronicler_rw',
                _schema
            );
        END IF;
        -- calendar_event_instances (CalendarCompletedAdapter — optional module)
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = _schema AND table_name = 'calendar_event_instances'
        ) THEN
            EXECUTE format(
                'GRANT SELECT ON TABLE %I.calendar_event_instances TO butler_chronicler_rw',
                _schema
            );
        END IF;
        -- entity_facts (CoreSessionsAdapter contact resolution + the
        -- comms.message_bursts participant resolution — relationship schema only)
        IF _schema = 'relationship' AND EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = _schema AND table_name = 'entity_facts'
        ) THEN
            EXECUTE format(
                'GRANT SELECT ON TABLE %I.entity_facts TO butler_chronicler_rw',
                _schema
            );
        END IF;
    END LOOP;

    -- Connectors evidence surfaces for PLANNED adapters (grant when tables exist).
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', _connector_schema, 'butler_chronicler_rw');
    FOREACH _table IN ARRAY ARRAY[
        'steam_play_history',
        'owntracks_points',
        'home_assistant_history'
    ] LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = _connector_schema AND table_name = _table
        ) THEN
            EXECUTE format(
                'GRANT SELECT ON TABLE %I.%I TO butler_chronicler_rw',
                _connector_schema,
                _table
            );
        END IF;
    END LOOP;

    -- Connector role: write access to connector schema, switchboard operational
    -- tables, and shared public tables.
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I', _db_name, _connector_role);
    EXECUTE format('GRANT USAGE, CREATE ON SCHEMA %I TO %I', _connector_schema, _connector_role);
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I',
        _connector_schema,
        _connector_role
    );
    EXECUTE format(
        'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I',
        _connector_schema,
        _connector_role
    );
    EXECUTE format(
        'GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA %I TO %I',
        _connector_schema,
        _connector_role
    );
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO %I', _switchboard_schema, _connector_role);
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO %I',
        _switchboard_schema,
        _connector_role
    );
    EXECUTE format(
        'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA %I TO %I',
        _switchboard_schema,
        _connector_role
    );
    EXECUTE format('GRANT USAGE ON SCHEMA public TO %I', _connector_role);
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
        _connector_role
    );
    EXECUTE format(
        'GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I',
        _connector_role
    );

    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        _migration_user,
        _connector_schema,
        _connector_role
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
        _migration_user,
        _connector_schema,
        _connector_role
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT EXECUTE ON FUNCTIONS TO %I',
        _migration_user,
        _connector_schema,
        _connector_role
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        _migration_user,
        _switchboard_schema,
        _connector_role
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA %I GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
        _migration_user,
        _switchboard_schema,
        _connector_role
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
        _migration_user,
        _connector_role
    );
    EXECUTE format(
        'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
        _migration_user,
        _connector_role
    );

    RAISE NOTICE 'Bootstrap complete for database "%" (migration/runtime user "%")', _db_name, _migration_user;
END
$$;

-- ── Restore-drill interface ownership handoff ──────────────────────────────
--
-- Alembic normally runs as the same NOCREATEDB login used by dashboard-api.
-- It may create the versioned function definitions, but cannot remain their
-- owner: object ownership bypasses EXECUTE ACLs. This bootstrap-owned, fixed
-- handoff transfers only the two known functions to a NOLOGIN role, then
-- removes the temporary migration grants in the same migration transaction.

CREATE SCHEMA IF NOT EXISTS restore_drill_executor_admin;
REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor_admin FROM PUBLIC;

CREATE TABLE IF NOT EXISTS restore_drill_executor_admin.bootstrap_configuration (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    migration_role NAME NOT NULL
);
REVOKE ALL PRIVILEGES ON TABLE restore_drill_executor_admin.bootstrap_configuration FROM PUBLIC;

INSERT INTO restore_drill_executor_admin.bootstrap_configuration (singleton, migration_role)
VALUES (
    true,
    COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers')::name
)
ON CONFLICT (singleton) DO UPDATE SET migration_role = EXCLUDED.migration_role;

CREATE OR REPLACE FUNCTION restore_drill_executor_admin.finalize_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
DECLARE
    v_migration_role NAME;
    v_runtime_role NAME;
BEGIN
    SELECT migration_role
    INTO v_migration_role
    FROM restore_drill_executor_admin.bootstrap_configuration
    WHERE singleton;

    IF v_migration_role IS NULL THEN
        RAISE EXCEPTION 'restore-drill bootstrap configuration is missing';
    END IF;
    IF to_regprocedure('restore_drill_executor.is_due(integer)') IS NULL
       OR to_regprocedure('restore_drill_executor.record_result(text,text,text,integer)') IS NULL
       OR to_regprocedure('restore_drill_executor.latest_result()') IS NULL THEN
        RAISE EXCEPTION 'restore-drill interface functions must exist before ownership finalization';
    END IF;

    EXECUTE 'GRANT USAGE ON SCHEMA public TO restore_drill_executor_owner';
    EXECUTE 'GRANT SELECT, INSERT ON TABLE public.audit_log TO restore_drill_executor_owner';
    EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE public.audit_log_id_seq TO restore_drill_executor_owner';

    EXECUTE 'ALTER TABLE restore_drill_executor.restore_drill_results '
        || 'OWNER TO restore_drill_executor_owner';
    EXECUTE 'ALTER SEQUENCE restore_drill_executor.restore_drill_results_id_seq '
        || 'OWNER TO restore_drill_executor_owner';
    EXECUTE 'ALTER FUNCTION restore_drill_executor.is_due(integer) OWNER TO restore_drill_executor_owner';
    EXECUTE 'ALTER FUNCTION restore_drill_executor.record_result(text, text, text, integer) OWNER TO restore_drill_executor_owner';
    EXECUTE 'ALTER FUNCTION restore_drill_executor.latest_result() OWNER TO restore_drill_executor_owner';
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM restore_drill_executor';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA restore_drill_executor FROM restore_drill_executor';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA restore_drill_executor FROM restore_drill_executor';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA restore_drill_executor FROM restore_drill_executor';
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM %I', v_migration_role);
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA restore_drill_executor FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA restore_drill_executor FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA restore_drill_executor FROM %I',
        v_migration_role
    );
    FOREACH v_runtime_role IN ARRAY ARRAY[
        'butler_chronicler_rw',
        'butler_education_rw',
        'butler_finance_rw',
        'butler_general_rw',
        'butler_health_rw',
        'butler_home_rw',
        'butler_lifestyle_rw',
        'butler_messenger_rw',
        'butler_qa_rw',
        'butler_relationship_rw',
        'butler_switchboard_rw',
        'butler_travel_rw',
        'connector_writer'
    ]::name[] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime_role) THEN
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE restore_drill_executor.restore_drill_results FROM %I',
                v_runtime_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE restore_drill_executor.restore_drill_results_id_seq FROM %I',
                v_runtime_role
            );
        END IF;
    END LOOP;
    EXECUTE 'GRANT USAGE ON SCHEMA restore_drill_executor TO restore_drill_executor';
    EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor.is_due(integer) TO restore_drill_executor';
    EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor.record_result(text, text, text, integer) TO restore_drill_executor';
    EXECUTE format('GRANT USAGE ON SCHEMA restore_drill_executor TO %I', v_migration_role);
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION restore_drill_executor.latest_result() TO %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() FROM %I',
        v_migration_role
    );
    EXECUTE format('REVOKE USAGE ON SCHEMA restore_drill_executor_admin FROM %I', v_migration_role);
END;
$$;

REVOKE ALL PRIVILEGES ON FUNCTION restore_drill_executor_admin.finalize_interface() FROM PUBLIC;

DO $$
DECLARE
    _migration_user TEXT := COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers');
BEGIN
    IF to_regprocedure('restore_drill_executor.is_due(integer)') IS NOT NULL
       AND to_regprocedure('restore_drill_executor.record_result(text,text,text,integer)') IS NOT NULL
       AND to_regprocedure('restore_drill_executor.latest_result()') IS NOT NULL THEN
        PERFORM restore_drill_executor_admin.finalize_interface();
    ELSE
        EXECUTE format('GRANT USAGE, CREATE ON SCHEMA restore_drill_executor TO %I', _migration_user);
        EXECUTE format('GRANT USAGE ON SCHEMA restore_drill_executor_admin TO %I', _migration_user);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() TO %I',
            _migration_user
        );
    END IF;
END;
$$;
