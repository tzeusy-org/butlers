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
    _migration_user TEXT := COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers');
    _db_name TEXT := current_database();
    _schema TEXT;
    _role TEXT;
    _idx INTEGER;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _migration_user) THEN
        RAISE EXCEPTION
            'Migration/runtime user "%" does not exist. Create it first or set PGOPTIONS="-c butlers.connecting_user=<existing role>".',
            _migration_user;
    END IF;

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
            EXECUTE format('CREATE ROLE %I LOGIN', _role);
            RAISE NOTICE 'Created role "%"', _role;
        END IF;
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
