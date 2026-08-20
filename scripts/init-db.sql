-- init-db.sql: privileged bootstrap for Butlers runtime + migration ACLs
--
-- Run this script as a cluster superuser against the target
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

-- Stop the documented psql -f invocation at the first rejected safety check.
\set ON_ERROR_STOP on

-- ── Read-only bootstrap preflight ───────────────────────────────────────────
-- INITIAL_READ_ONLY_BOOTSTRAP_PREFLIGHT: keep before every DDL/DCL mutation.

-- Refuse an unsafe connecting-user override before extension installation or
-- any role, membership, schema, or ACL mutation. The configured migration user
-- is deliberately a normal role and must never bootstrap its own privileges.
DO $$
DECLARE
    _migration_user NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _migration_user) THEN
        RAISE EXCEPTION
            'Migration/runtime user "%" does not exist. Create it first or set PGOPTIONS="-c butlers.connecting_user=<existing role>".',
            _migration_user;
    END IF;
    IF current_user::name = _migration_user THEN
        RAISE EXCEPTION 'restore-drill admin bootstrap cannot run as the shared migration role';
    END IF;
    IF NOT COALESCE(
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
        false
    ) THEN
        RAISE EXCEPTION 'restore-drill admin bootstrap requires a cluster superuser';
    END IF;
END;
$$;

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
    _restore_drill_audit_writer_role TEXT := 'restore_drill_executor_audit_writer';
    _restore_drill_executor_schema TEXT := 'restore_drill_executor';
    _optional_calendar_role TEXT := 'butler_calendar_rw';
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
    -- dashboard/runtime processes. It must never retain a privileged recovery
    -- capability that could bypass the isolated executor boundary.
    EXECUTE format(
        'ALTER ROLE %I NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
        _migration_user
    );

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
        -- Existing normal runtime roles may predate this bootstrap. Normalize
        -- every cluster-level privilege while preserving their LOGIN and
        -- INHERIT semantics and the migration user's explicit memberships.
        EXECUTE format(
            'ALTER ROLE %I NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
            _role
        );
    END LOOP;

    -- Calendar is an optional module role created best-effort by core_140/142.
    -- Do not create it or grant it to the migration user here; when it exists,
    -- it remains a normal login with no cluster-level recovery capability.
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _optional_calendar_role) THEN
        EXECUTE format(
            'ALTER ROLE %I NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
            _optional_calendar_role
        );
    END IF;

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
    -- The public-audit projection uses a second, purpose-bound NOLOGIN owner.
    -- It has no private-ledger authority, so a mutable public.audit_log trigger
    -- cannot run with the result-owner's effective privileges.
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _restore_drill_audit_writer_role) THEN
        EXECUTE format(
            'CREATE ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
            _restore_drill_audit_writer_role
        );
        RAISE NOTICE 'Created restore-drill audit projection writer "%"', _restore_drill_audit_writer_role;
    END IF;
    EXECUTE format(
        'ALTER ROLE %I NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOREPLICATION NOCREATEDB',
        _restore_drill_audit_writer_role
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
    EXECUTE format('REVOKE %I FROM %I', _restore_drill_audit_writer_role, _migration_user);
    EXECUTE format('REVOKE %I FROM %I', _migration_user, _restore_drill_audit_writer_role);
    EXECUTE format('REVOKE %I FROM %I', _restore_drill_audit_writer_role, _restore_drill_executor_role);
    EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_role, _restore_drill_audit_writer_role);
    EXECUTE format('REVOKE %I FROM %I', _restore_drill_audit_writer_role, _restore_drill_executor_owner_role);
    EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_owner_role, _restore_drill_audit_writer_role);
    FOREACH _role IN ARRAY _all_runtime_roles LOOP
        EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_role, _role);
        EXECUTE format('REVOKE %I FROM %I', _role, _restore_drill_executor_role);
        EXECUTE format('REVOKE %I FROM %I', _restore_drill_executor_owner_role, _role);
        EXECUTE format('REVOKE %I FROM %I', _role, _restore_drill_executor_owner_role);
        EXECUTE format('REVOKE %I FROM %I', _restore_drill_audit_writer_role, _role);
        EXECUTE format('REVOKE %I FROM %I', _role, _restore_drill_audit_writer_role);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _optional_calendar_role) THEN
        EXECUTE format(
            'REVOKE %I FROM %I',
            _restore_drill_executor_role,
            _optional_calendar_role
        );
        EXECUTE format(
            'REVOKE %I FROM %I',
            _optional_calendar_role,
            _restore_drill_executor_role
        );
        EXECUTE format(
            'REVOKE %I FROM %I',
            _restore_drill_executor_owner_role,
            _optional_calendar_role
        );
        EXECUTE format(
            'REVOKE %I FROM %I',
            _optional_calendar_role,
            _restore_drill_executor_owner_role
        );
        EXECUTE format(
            'REVOKE %I FROM %I',
            _restore_drill_audit_writer_role,
            _optional_calendar_role
        );
        EXECUTE format(
            'REVOKE %I FROM %I',
            _optional_calendar_role,
            _restore_drill_audit_writer_role
        );
    END IF;

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
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I', _restore_drill_executor_schema, _restore_drill_audit_writer_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA %I FROM %I', _restore_drill_executor_schema, _restore_drill_audit_writer_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA %I FROM %I', _restore_drill_executor_schema, _restore_drill_audit_writer_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA %I FROM %I', _restore_drill_executor_schema, _restore_drill_audit_writer_role);
    FOREACH _role IN ARRAY _all_runtime_roles LOOP
        EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I', _restore_drill_executor_schema, _role);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _optional_calendar_role) THEN
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SCHEMA %I FROM %I',
            _restore_drill_executor_schema,
            _optional_calendar_role
        );
    END IF;

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

-- ── Restore-drill interface bootstrap boundary ──────────────────────────────
--
-- Alembic normally runs as the same NOCREATEDB login used by dashboard-api.
-- That shared login must never stage arbitrary objects in the protected schema:
-- ownership bypasses EXECUTE ACLs, and an ownership finalizer cannot safely
-- infer whether a compatible-looking relation was created by an attacker.  The
-- bootstrap owner instead exposes one fixed no-argument SECURITY DEFINER
-- installer. core_196 invokes it inside its migration transaction, so it
-- creates the exact ledger and functions under the bootstrap owner before the
-- finalizer moves them to the isolated NOLOGIN owner. The schema and both
-- canonical signatures are validated before CREATE OR REPLACE can preserve an
-- attacker-controlled owner. A safe retry can arrive through a different
-- superuser after the trusted schema already exists; its admin objects must be
-- created as that established schema owner rather than the retry runner.

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
    v_schema_owner OID;
    v_schema_owner_name NAME;
    v_schema_owner_is_superuser BOOLEAN;
BEGIN
    -- The read-only preflight rejects this before all mutations. Keep the
    -- boundary-local check so this privileged installer remains fail-closed if
    -- future bootstrap staging changes its call order.
    IF current_user::name = v_migration_role THEN
        RAISE EXCEPTION
            'restore-drill admin bootstrap cannot run as the shared migration role';
    END IF;
    IF NOT COALESCE(
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
        false
    ) THEN
        RAISE EXCEPTION
            'restore-drill admin bootstrap requires a cluster superuser';
    END IF;
    SELECT admin_schema.nspowner, schema_owner.rolname, schema_owner.rolsuper
    INTO v_schema_owner, v_schema_owner_name, v_schema_owner_is_superuser
    FROM pg_namespace AS admin_schema
    JOIN pg_roles AS schema_owner ON schema_owner.oid = admin_schema.nspowner
    WHERE admin_schema.nspname = 'restore_drill_executor_admin';

    IF v_schema_owner IS NULL THEN
        EXECUTE format(
            'CREATE SCHEMA %I AUTHORIZATION %I',
            'restore_drill_executor_admin',
            current_user
        );
    ELSIF NOT COALESCE(v_schema_owner_is_superuser, false) THEN
        RAISE EXCEPTION
            'restore-drill admin schema is not owned by a trusted bootstrap superuser';
    ELSE
        -- A superuser retry may safely assume the previously verified bootstrap
        -- owner. This keeps every retained admin object aligned with the schema
        -- provenance that core_196 independently trusts.
        EXECUTE format('SET ROLE %I', v_schema_owner_name);
    END IF;
END;
$$;

REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor_admin FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
    v_bootstrap_owner OID;
    v_bootstrap_owner_is_superuser BOOLEAN;
BEGIN
    SELECT admin_schema.nspowner, bootstrap_owner.rolsuper
    INTO v_bootstrap_owner, v_bootstrap_owner_is_superuser
    FROM pg_namespace AS admin_schema
    JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
    WHERE admin_schema.nspname = 'restore_drill_executor_admin';
    IF NOT COALESCE(v_bootstrap_owner_is_superuser, false) THEN
        RAISE EXCEPTION
            'restore-drill admin schema is not owned by a trusted bootstrap superuser';
    END IF;
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor_admin FROM %I',
        v_migration_role
    );
    IF EXISTS (
        SELECT 1
        FROM pg_proc AS admin_function
        JOIN pg_namespace AS admin_schema
            ON admin_schema.oid = admin_function.pronamespace
        WHERE admin_schema.nspname = 'restore_drill_executor_admin'
          AND admin_function.proname IN ('finalize_interface', 'install_interface')
          AND admin_function.pronargs = 0
          AND admin_function.proowner <> v_bootstrap_owner
    ) THEN
        RAISE EXCEPTION
            'restore-drill admin interface function is not owned by the bootstrap role';
    END IF;
END;
$$;

DO $$
DECLARE
    v_bootstrap_owner OID;
    v_table_owner OID;
BEGIN
    SELECT admin_schema.nspowner
    INTO v_bootstrap_owner
    FROM pg_namespace AS admin_schema
    WHERE admin_schema.nspname = 'restore_drill_executor_admin';
    SELECT admin_table.relowner INTO v_table_owner
    FROM pg_class AS admin_table
    JOIN pg_namespace AS admin_schema ON admin_schema.oid = admin_table.relnamespace
    WHERE admin_schema.nspname = 'restore_drill_executor_admin'
      AND admin_table.relname = 'bootstrap_configuration'
      AND admin_table.relkind = 'r';
    IF v_table_owner IS NOT NULL AND v_table_owner <> v_bootstrap_owner THEN
        RAISE EXCEPTION
            'restore-drill bootstrap configuration is not owned by the bootstrap role';
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS restore_drill_executor_admin.bootstrap_configuration (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    migration_role NAME NOT NULL,
    bootstrap_role NAME
);
ALTER TABLE restore_drill_executor_admin.bootstrap_configuration
    ADD COLUMN IF NOT EXISTS bootstrap_role NAME;
UPDATE restore_drill_executor_admin.bootstrap_configuration
SET bootstrap_role = (
    SELECT bootstrap_owner.rolname::name
    FROM pg_namespace AS admin_schema
    JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
    WHERE admin_schema.nspname = 'restore_drill_executor_admin'
)
WHERE bootstrap_role IS NULL;
ALTER TABLE restore_drill_executor_admin.bootstrap_configuration
    ALTER COLUMN bootstrap_role SET NOT NULL;
REVOKE ALL PRIVILEGES ON TABLE restore_drill_executor_admin.bootstrap_configuration FROM PUBLIC;

INSERT INTO restore_drill_executor_admin.bootstrap_configuration (
    singleton,
    migration_role,
    bootstrap_role
)
VALUES (
    true,
    COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers')::name,
    (
        SELECT bootstrap_owner.rolname::name
        FROM pg_namespace AS admin_schema
        JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
        WHERE admin_schema.nspname = 'restore_drill_executor_admin'
    )
)
ON CONFLICT (singleton) DO UPDATE SET
    migration_role = EXCLUDED.migration_role,
    bootstrap_role = EXCLUDED.bootstrap_role;

-- A public.audit_log trigger runs as the effective user of the INSERT. Keep
-- that user intentionally unable to resolve or write the private authority
-- schema. The private result owner calls this fixed projection writer only
-- after its ledger insert, so a trigger failure rolls the enclosing result
-- transaction back instead of committing a partial truth claim.
CREATE OR REPLACE FUNCTION restore_drill_executor_admin.write_audit_projection(
    p_result TEXT
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $audit_projection$
DECLARE
    v_detail TEXT;
BEGIN
    IF p_result IS NULL OR p_result NOT IN ('pass', 'fail') THEN
        RAISE EXCEPTION 'p_result must be pass or fail';
    END IF;

    v_detail := 'restore drill diagnostic withheld';
    INSERT INTO public.audit_log (
        actor,
        action,
        target,
        result,
        error,
        metadata
    )
    VALUES (
        'restore_drill',
        'restore_drill_result',
        'restore_drill',
        p_result,
        CASE WHEN p_result = 'fail' THEN v_detail ELSE NULL END,
        jsonb_build_object(
            'detail', CASE WHEN p_result = 'fail' THEN v_detail ELSE NULL END
        )
    );
END;
$audit_projection$;

ALTER FUNCTION restore_drill_executor_admin.write_audit_projection(TEXT)
    OWNER TO restore_drill_executor_audit_writer;
REVOKE ALL PRIVILEGES ON FUNCTION
    restore_drill_executor_admin.write_audit_projection(TEXT) FROM PUBLIC;

CREATE OR REPLACE FUNCTION restore_drill_executor_admin.finalize_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_migration_role NAME;
    v_runtime_role NAME;
    v_optional_calendar_role NAME := 'butler_calendar_rw';
    v_bootstrap_owner OID;
    v_executor_owner OID;
    v_audit_writer_owner OID;
    v_audit_writer_function_owner OID;
    v_audit_writer_function_definer BOOLEAN;
    v_relation_owner OID;
    v_sequence_owner OID;
    v_is_due_owner OID;
    v_record_result_owner OID;
    v_latest_result_owner OID;
    v_has_user_trigger BOOLEAN;
    v_is_bootstrap_staged BOOLEAN := false;
    v_is_finalized BOOLEAN := false;
BEGIN
    SELECT migration_role
    INTO v_migration_role
    FROM restore_drill_executor_admin.bootstrap_configuration
    WHERE singleton;

    IF v_migration_role IS NULL THEN
        RAISE EXCEPTION 'restore-drill bootstrap configuration is missing';
    END IF;
    SELECT interface_function.proowner
    INTO v_bootstrap_owner
    FROM pg_proc AS interface_function
    WHERE interface_function.oid =
        'restore_drill_executor_admin.finalize_interface()'::regprocedure;
    SELECT oid
    INTO v_executor_owner
    FROM pg_roles
    WHERE rolname = 'restore_drill_executor_owner';
    SELECT oid
    INTO v_audit_writer_owner
    FROM pg_roles
    WHERE rolname = 'restore_drill_executor_audit_writer';
    SELECT interface_function.proowner, interface_function.prosecdef
    INTO v_audit_writer_function_owner, v_audit_writer_function_definer
    FROM pg_proc AS interface_function
    WHERE interface_function.oid =
        'restore_drill_executor_admin.write_audit_projection(text)'::regprocedure;

    IF v_bootstrap_owner IS NULL OR v_executor_owner IS NULL
       OR v_audit_writer_owner IS NULL
       OR v_audit_writer_function_owner <> v_audit_writer_owner
       OR NOT COALESCE(v_audit_writer_function_definer, false)
       OR to_regclass('restore_drill_executor.restore_drill_results') IS NULL
       OR to_regclass('restore_drill_executor.restore_drill_results_id_seq') IS NULL
       OR to_regprocedure('restore_drill_executor.is_due(integer)') IS NULL
       OR to_regprocedure('restore_drill_executor.record_result(text,text,text,integer)') IS NULL
       OR to_regprocedure('restore_drill_executor.latest_result()') IS NULL THEN
        RAISE EXCEPTION
            'restore-drill authority objects must be created by the fixed bootstrap installer';
    END IF;

    SELECT relation.relowner
    INTO v_relation_owner
    FROM pg_class AS relation
    WHERE relation.oid = 'restore_drill_executor.restore_drill_results'::regclass
      AND relation.relkind = 'r';
    SELECT sequence.relowner
    INTO v_sequence_owner
    FROM pg_class AS sequence
    WHERE sequence.oid = 'restore_drill_executor.restore_drill_results_id_seq'::regclass
      AND sequence.relkind = 'S';
    SELECT interface_function.proowner
    INTO v_is_due_owner
    FROM pg_proc AS interface_function
    WHERE interface_function.oid = 'restore_drill_executor.is_due(integer)'::regprocedure;
    SELECT interface_function.proowner
    INTO v_record_result_owner
    FROM pg_proc AS interface_function
    WHERE interface_function.oid =
        'restore_drill_executor.record_result(text,text,text,integer)'::regprocedure;
    SELECT interface_function.proowner
    INTO v_latest_result_owner
    FROM pg_proc AS interface_function
    WHERE interface_function.oid = 'restore_drill_executor.latest_result()'::regprocedure;
    SELECT EXISTS (
        SELECT 1
        FROM pg_trigger AS trigger_row
        WHERE trigger_row.tgrelid = 'restore_drill_executor.restore_drill_results'::regclass
          AND NOT trigger_row.tgisinternal
    )
    INTO v_has_user_trigger;

    v_is_bootstrap_staged := COALESCE(
        v_relation_owner = v_bootstrap_owner
        AND v_sequence_owner = v_bootstrap_owner
        AND v_is_due_owner = v_bootstrap_owner
        AND v_record_result_owner = v_bootstrap_owner
        AND v_latest_result_owner = v_bootstrap_owner
        AND NOT v_has_user_trigger,
        false
    );
    v_is_finalized := COALESCE(
        v_relation_owner = v_executor_owner
        AND v_sequence_owner = v_executor_owner
        AND v_is_due_owner = v_executor_owner
        AND v_record_result_owner = v_executor_owner
        AND v_latest_result_owner = v_executor_owner
        AND NOT v_has_user_trigger,
        false
    );

    -- Do not bless arbitrary compatible DDL.  Only exact objects made by this
    -- bootstrap owner in install_interface(), or an already-finalized clean
    -- interface, can reach the privilege handoff below.
    IF NOT v_is_bootstrap_staged AND NOT v_is_finalized THEN
        RAISE EXCEPTION 'restore-drill interface ownership is untrusted';
    END IF;

    -- The nested executor functions are created only by install_interface().
    -- Once their exact signatures and trusted ownership have been proved,
    -- privileged bootstrap reruns repair legacy search paths without accepting
    -- arbitrary caller-controlled objects.
    EXECUTE 'ALTER FUNCTION restore_drill_executor.is_due(integer) '
        || 'SET search_path = pg_catalog, public, pg_temp';
    EXECUTE 'ALTER FUNCTION restore_drill_executor.record_result(text, text, text, integer) '
        || 'SET search_path = pg_catalog, public, pg_temp';
    EXECUTE 'ALTER FUNCTION restore_drill_executor.latest_result() '
        || 'SET search_path = pg_catalog, pg_temp';

    IF v_is_bootstrap_staged THEN
        EXECUTE 'ALTER TABLE restore_drill_executor.restore_drill_results '
            || 'OWNER TO restore_drill_executor_owner';
        EXECUTE 'ALTER SEQUENCE restore_drill_executor.restore_drill_results_id_seq '
            || 'OWNER TO restore_drill_executor_owner';
        EXECUTE 'ALTER FUNCTION restore_drill_executor.is_due(integer) OWNER TO restore_drill_executor_owner';
        EXECUTE 'ALTER FUNCTION restore_drill_executor.record_result(text, text, text, integer) OWNER TO restore_drill_executor_owner';
        EXECUTE 'ALTER FUNCTION restore_drill_executor.latest_result() OWNER TO restore_drill_executor_owner';
    END IF;
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM restore_drill_executor';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA restore_drill_executor FROM restore_drill_executor';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA restore_drill_executor FROM restore_drill_executor';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA restore_drill_executor FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA restore_drill_executor FROM restore_drill_executor';
    -- The private result owner must not issue shared-audit DML itself. A
    -- mutable audit trigger therefore never runs with its ledger privileges.
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA public FROM restore_drill_executor_owner';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.audit_log FROM restore_drill_executor_owner';
    EXECUTE 'REVOKE ALL PRIVILEGES ON SEQUENCE public.audit_log_id_seq FROM restore_drill_executor_owner';
    -- The projection writer is a NOLOGIN definer with exactly the audit insert
    -- capability. It cannot create in public or resolve any protected object.
    EXECUTE 'REVOKE CREATE ON SCHEMA public FROM restore_drill_executor_audit_writer';
    EXECUTE 'GRANT USAGE ON SCHEMA public TO restore_drill_executor_audit_writer';
    EXECUTE 'GRANT INSERT ON TABLE public.audit_log TO restore_drill_executor_audit_writer';
    EXECUTE 'GRANT USAGE ON SEQUENCE public.audit_log_id_seq TO restore_drill_executor_audit_writer';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION restore_drill_executor_admin.write_audit_projection(text) FROM PUBLIC';
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION restore_drill_executor_admin.write_audit_projection(text) FROM %I',
        v_migration_role
    );
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION restore_drill_executor_admin.write_audit_projection(text) FROM restore_drill_executor';
    EXECUTE 'GRANT USAGE ON SCHEMA restore_drill_executor_admin TO restore_drill_executor_owner';
    EXECUTE 'GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.write_audit_projection(text) TO restore_drill_executor_owner';
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
                'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM %I',
                v_runtime_role
            );
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
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_optional_calendar_role) THEN
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM %I',
            v_optional_calendar_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE restore_drill_executor.restore_drill_results FROM %I',
            v_optional_calendar_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SEQUENCE restore_drill_executor.restore_drill_results_id_seq FROM %I',
            v_optional_calendar_role
        );
    END IF;
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
    EXECUTE format(
        'REVOKE EXECUTE ON FUNCTION restore_drill_executor_admin.install_interface() FROM %I',
        v_migration_role
    );
    EXECUTE format('REVOKE USAGE ON SCHEMA restore_drill_executor_admin FROM %I', v_migration_role);
END;
$$;

CREATE OR REPLACE FUNCTION restore_drill_executor_admin.install_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $installer$
BEGIN
    -- The installer has no caller-controlled object names or DDL input.  A
    -- pre-existing relation or canonical signature is always an untrusted
    -- state, rather than a shape to validate or repair.
    IF to_regclass('restore_drill_executor.restore_drill_results') IS NOT NULL
       OR to_regprocedure('restore_drill_executor.is_due(integer)') IS NOT NULL
       OR to_regprocedure('restore_drill_executor.record_result(text,text,text,integer)') IS NOT NULL
       OR to_regprocedure('restore_drill_executor.latest_result()') IS NOT NULL THEN
        RAISE EXCEPTION
            'restore-drill authority interface must be absent before fixed bootstrap installation';
    END IF;

    CREATE TABLE restore_drill_executor.restore_drill_results (
        id BIGSERIAL PRIMARY KEY,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        result TEXT NOT NULL CHECK (result IN ('pass', 'fail')),
        detail TEXT
    );

    CREATE FUNCTION restore_drill_executor.is_due(
        p_interval_seconds INTEGER
    )
    RETURNS BOOLEAN
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $is_due$
    DECLARE
        v_last_recorded_at TIMESTAMPTZ;
    BEGIN
        IF p_interval_seconds IS NULL OR p_interval_seconds <= 0 THEN
            RAISE EXCEPTION 'p_interval_seconds must be positive';
        END IF;

        SELECT max(recorded_at)
        INTO v_last_recorded_at
        FROM restore_drill_executor.restore_drill_results;

        RETURN v_last_recorded_at IS NULL
            OR v_last_recorded_at <= clock_timestamp()
                - make_interval(secs => p_interval_seconds);
    END;
    $is_due$;

    CREATE FUNCTION restore_drill_executor.record_result(
        p_backup_name TEXT,
        p_result TEXT,
        p_detail TEXT,
        p_table_count INTEGER
    )
    RETURNS BIGINT
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $record_result$
    DECLARE
        v_result_id BIGINT;
        v_detail TEXT;
    BEGIN
        IF p_result IS NULL OR p_result NOT IN ('pass', 'fail') THEN
            RAISE EXCEPTION 'p_result must be pass or fail';
        END IF;

        -- Keep the deployed four-argument ABI, but every caller-controlled
        -- compatibility input except p_result is inert at this final boundary.
        v_detail := 'restore drill diagnostic withheld';

        INSERT INTO restore_drill_executor.restore_drill_results (
            result,
            detail
        )
        VALUES (
            p_result,
            CASE WHEN p_result = 'fail' THEN v_detail ELSE NULL END
        )
        RETURNING id INTO v_result_id;

        -- Public audit is fixed telemetry, never a result authority. Its
        -- purpose-bound definer has no private-ledger privileges, so a hostile
        -- public trigger can at most fail this transaction (safe availability
        -- denial); it cannot manufacture or modify authoritative results.
        PERFORM restore_drill_executor_admin.write_audit_projection(p_result);

        RETURN v_result_id;
    END;
    $record_result$;

    CREATE FUNCTION restore_drill_executor.latest_result()
    RETURNS TABLE (
        checked_at TIMESTAMPTZ,
        result TEXT,
        detail TEXT
    )
    LANGUAGE sql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $latest_result$
        SELECT recorded_at, result, detail
        FROM restore_drill_executor.restore_drill_results
        ORDER BY recorded_at DESC, id DESC
        LIMIT 1
    $latest_result$;

    PERFORM restore_drill_executor_admin.finalize_interface();
END;
$installer$;

REVOKE ALL PRIVILEGES ON FUNCTION restore_drill_executor_admin.finalize_interface() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION restore_drill_executor_admin.install_interface() FROM PUBLIC;

DO $$
DECLARE
    _migration_user TEXT := COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers');
BEGIN
    -- Repair legacy grants before inspecting any interface object.  Shared
    -- users receive no protected-schema CREATE and no finalizer execution.
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor FROM %I', _migration_user);
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA restore_drill_executor_admin FROM %I', _migration_user);
    EXECUTE format(
        'REVOKE EXECUTE ON FUNCTION restore_drill_executor_admin.finalize_interface() FROM %I',
        _migration_user
    );
    EXECUTE format(
        'REVOKE EXECUTE ON FUNCTION restore_drill_executor_admin.install_interface() FROM %I',
        _migration_user
    );
END;
$$;

-- Keep the legacy-grant revocation in its own committed statement.  If a
-- poisoned authority object makes the next finalization fail, that failure
-- must not roll this repair back and leave a shared caller able to retry it.
DO $$
DECLARE
    _migration_user TEXT := COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers');
BEGIN
    IF to_regclass('restore_drill_executor.restore_drill_results') IS NOT NULL
       OR to_regprocedure('restore_drill_executor.is_due(integer)') IS NOT NULL
       OR to_regprocedure('restore_drill_executor.record_result(text,text,text,integer)') IS NOT NULL
       OR to_regprocedure('restore_drill_executor.latest_result()') IS NOT NULL THEN
        PERFORM restore_drill_executor_admin.finalize_interface();
    ELSE
        EXECUTE format('GRANT USAGE ON SCHEMA restore_drill_executor TO %I', _migration_user);
        EXECUTE format('GRANT USAGE ON SCHEMA restore_drill_executor_admin TO %I', _migration_user);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION restore_drill_executor_admin.install_interface() TO %I',
            _migration_user
        );
    END IF;
END;
$$;

-- ── Canonical DND generation guard bootstrap boundary ──────────────────────
--
-- ``public.user_context`` is an existing shared-awareness table, but DND now
-- carries a safety-critical generation/replay boundary.  The ordinary Alembic
-- login must never create or take ownership of that boundary.  This
-- cluster-superuser bootstrap exposes fixed no-argument installer, finalizer,
-- and restricted rollback operations; core_197 can only catalog-validate and
-- invoke them. The installer rejects partial or familiar-looking authority
-- objects rather than adopting them, while rollback accepts only an unused
-- generation boundary before restoring the pre-guard handoff.

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
    v_schema_owner NAME;
    v_schema_owner_is_superuser BOOLEAN;
BEGIN
    IF current_user::name = v_migration_role THEN
        RAISE EXCEPTION
            'DND generation admin bootstrap cannot run as the shared migration role';
    END IF;
    IF v_migration_role IN ('butler_general_rw', 'butler_switchboard_rw') THEN
        RAISE EXCEPTION
            'DND generation bootstrap requires a migration role distinct from canonical DND writers';
    END IF;
    IF NOT COALESCE(
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
        false
    ) THEN
        RAISE EXCEPTION
            'DND generation admin bootstrap requires a cluster superuser';
    END IF;

    SELECT owner_role.rolname, owner_role.rolsuper
    INTO v_schema_owner, v_schema_owner_is_superuser
    FROM pg_namespace AS admin_schema
    JOIN pg_roles AS owner_role ON owner_role.oid = admin_schema.nspowner
    WHERE admin_schema.nspname = 'dnd_generation_admin';

    IF v_schema_owner IS NULL THEN
        EXECUTE format('CREATE SCHEMA %I AUTHORIZATION %I', 'dnd_generation_admin', current_user);
    ELSIF NOT COALESCE(v_schema_owner_is_superuser, false) THEN
        RAISE EXCEPTION
            'DND generation admin schema is not owned by a trusted bootstrap superuser';
    ELSE
        -- A retry can be run by another superuser.  Keep all retained admin
        -- objects owned by the already-proven schema owner.
        EXECUTE format('SET ROLE %I', v_schema_owner);
    END IF;
END;
$$;

REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_admin FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
    v_bootstrap_owner OID;
    v_bootstrap_owner_is_superuser BOOLEAN;
BEGIN
    SELECT admin_schema.nspowner, bootstrap_owner.rolsuper
    INTO v_bootstrap_owner, v_bootstrap_owner_is_superuser
    FROM pg_namespace AS admin_schema
    JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
    WHERE admin_schema.nspname = 'dnd_generation_admin';
    IF NOT COALESCE(v_bootstrap_owner_is_superuser, false) THEN
        RAISE EXCEPTION
            'DND generation admin schema is not owned by a trusted bootstrap superuser';
    END IF;
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_admin FROM %I', v_migration_role);
    IF EXISTS (
        SELECT 1
        FROM pg_proc AS admin_function
        JOIN pg_namespace AS admin_schema
            ON admin_schema.oid = admin_function.pronamespace
        WHERE admin_schema.nspname = 'dnd_generation_admin'
          AND admin_function.proname IN (
              'finalize_interface',
              'install_interface',
              'rollback_interface',
              'upgrade_producers_v2',
              'deactivate_producers_v2'
          )
          AND admin_function.pronargs = 0
          AND admin_function.proowner <> v_bootstrap_owner
    ) THEN
        RAISE EXCEPTION
            'DND generation admin interface function is not owned by the bootstrap role';
    END IF;
END;
$$;

DO $$
DECLARE
    v_owner OID;
    v_valid BOOLEAN;
BEGIN
    SELECT oid INTO v_owner FROM pg_roles WHERE rolname = 'dnd_generation_owner';
    IF v_owner IS NULL THEN
        CREATE ROLE dnd_generation_owner
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS;
    ELSE
        SELECT NOT rolcanlogin
               AND NOT rolinherit
               AND NOT rolsuper
               AND NOT rolcreaterole
               AND NOT rolcreatedb
               AND NOT rolreplication
               AND NOT rolbypassrls
               AND NOT EXISTS (
                    SELECT 1
                    FROM pg_auth_members
                    WHERE roleid = v_owner OR member = v_owner
               )
        INTO v_valid
        FROM pg_roles
        WHERE oid = v_owner;
        IF NOT COALESCE(v_valid, false) THEN
            RAISE EXCEPTION
                'DND generation owner role is untrusted or has runtime membership';
        END IF;
    END IF;
END;
$$;

DO $$
DECLARE
    v_bootstrap_owner OID;
    v_existing_owner OID;
BEGIN
    SELECT nspowner INTO v_bootstrap_owner
    FROM pg_namespace
    WHERE nspname = 'dnd_generation_admin';
    SELECT relowner INTO v_existing_owner
    FROM pg_class AS relation
    JOIN pg_namespace AS admin_schema ON admin_schema.oid = relation.relnamespace
    WHERE admin_schema.nspname = 'dnd_generation_admin'
      AND relation.relname = 'bootstrap_configuration'
      AND relation.relkind = 'r';
    IF v_existing_owner IS NOT NULL AND v_existing_owner <> v_bootstrap_owner THEN
        RAISE EXCEPTION
            'DND generation bootstrap configuration is not owned by the bootstrap role';
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS dnd_generation_admin.bootstrap_configuration (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    migration_role NAME NOT NULL,
    bootstrap_role NAME NOT NULL
);
REVOKE ALL PRIVILEGES ON TABLE dnd_generation_admin.bootstrap_configuration FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
BEGIN
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON TABLE dnd_generation_admin.bootstrap_configuration FROM %I',
        v_migration_role
    );
END;
$$;

INSERT INTO dnd_generation_admin.bootstrap_configuration (
    singleton,
    migration_role,
    bootstrap_role
)
VALUES (
    true,
    COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers')::name,
    (
        SELECT bootstrap_owner.rolname::name
        FROM pg_namespace AS admin_schema
        JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
        WHERE admin_schema.nspname = 'dnd_generation_admin'
    )
)
ON CONFLICT (singleton) DO UPDATE SET
    migration_role = EXCLUDED.migration_role,
    bootstrap_role = EXCLUDED.bootstrap_role;

CREATE OR REPLACE FUNCTION dnd_generation_admin.finalize_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $dnd_finalizer$
DECLARE
    v_migration_role NAME;
    v_bootstrap_owner OID;
    v_bootstrap_owner_is_superuser BOOLEAN;
    v_admin_schema_owner OID;
    v_admin_configuration_owner OID;
    v_dnd_owner OID;
    v_general_runtime_role OID;
    v_switchboard_runtime_role OID;
    v_user_context_owner OID;
    v_guard_owner OID;
    v_audit_owner OID;
    v_private_schema_owner OID;
    v_gateway_owner OID;
    v_canonical_json_owner OID;
    v_private_mutation_owner OID;
    v_runtime_role NAME;
    v_is_bootstrap_staged BOOLEAN := false;
    v_is_finalized BOOLEAN := false;
BEGIN
    SELECT migration_role INTO v_migration_role
    FROM dnd_generation_admin.bootstrap_configuration
    WHERE singleton;
    SELECT interface_function.proowner INTO v_bootstrap_owner
    FROM pg_proc AS interface_function
    WHERE interface_function.oid = 'dnd_generation_admin.finalize_interface()'::regprocedure;
    SELECT admin_schema.nspowner, bootstrap_owner.rolsuper
    INTO v_admin_schema_owner, v_bootstrap_owner_is_superuser
    FROM pg_namespace AS admin_schema
    JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
    WHERE admin_schema.nspname = 'dnd_generation_admin';
    SELECT configuration.relowner INTO v_admin_configuration_owner
    FROM pg_class AS configuration
    JOIN pg_namespace AS admin_schema ON admin_schema.oid = configuration.relnamespace
    WHERE admin_schema.nspname = 'dnd_generation_admin'
      AND configuration.relname = 'bootstrap_configuration'
      AND configuration.relkind = 'r';
    SELECT oid INTO v_dnd_owner
    FROM pg_roles
    WHERE rolname = 'dnd_generation_owner';
    SELECT oid INTO v_general_runtime_role
    FROM pg_roles
    WHERE rolname = 'butler_general_rw';
    SELECT oid INTO v_switchboard_runtime_role
    FROM pg_roles
    WHERE rolname = 'butler_switchboard_rw';

    IF v_migration_role IS NULL OR v_bootstrap_owner IS NULL OR v_dnd_owner IS NULL
       OR v_general_runtime_role IS NULL OR v_switchboard_runtime_role IS NULL
       OR v_admin_schema_owner IS DISTINCT FROM v_bootstrap_owner
       OR v_admin_configuration_owner IS DISTINCT FROM v_bootstrap_owner
       OR NOT COALESCE(v_bootstrap_owner_is_superuser, false)
       OR to_regclass('public.user_context') IS NULL
       OR to_regclass('public.dnd_generation_guard') IS NULL
       OR to_regclass('public.dnd_generation_mutations') IS NULL
       OR to_regnamespace('dnd_generation_private') IS NULL
       OR to_regprocedure(
            'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NULL
       OR to_regprocedure(
            'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NULL
       OR to_regprocedure(
            'dnd_generation_private.canonical_json(jsonb)'
       ) IS NULL THEN
        RAISE EXCEPTION
            'DND authority objects must be created by the fixed bootstrap installer';
    END IF;

    -- A direct bootstrap-finalizer retry must observe one stable shared-table
    -- catalog while it validates policy/ownership and repairs final ACLs.
    LOCK TABLE public.user_context IN ACCESS EXCLUSIVE MODE;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_roles
        WHERE oid = v_dnd_owner
          AND NOT rolcanlogin
          AND NOT rolinherit
          AND NOT rolsuper
          AND NOT rolcreaterole
          AND NOT rolcreatedb
          AND NOT rolreplication
          AND NOT rolbypassrls
    ) OR EXISTS (
        SELECT 1
        FROM pg_auth_members
        WHERE roleid = v_dnd_owner OR member = v_dnd_owner
    ) THEN
        RAISE EXCEPTION 'DND generation owner role is untrusted or has memberships';
    END IF;

    SELECT relation.relowner INTO v_user_context_owner
    FROM pg_class AS relation
    WHERE relation.oid = 'public.user_context'::regclass;
    SELECT relation.relowner INTO v_guard_owner
    FROM pg_class AS relation
    WHERE relation.oid = 'public.dnd_generation_guard'::regclass;
    SELECT relation.relowner INTO v_audit_owner
    FROM pg_class AS relation
    WHERE relation.oid = 'public.dnd_generation_mutations'::regclass;
    SELECT nspowner INTO v_private_schema_owner
    FROM pg_namespace WHERE nspname = 'dnd_generation_private';
    SELECT proowner INTO v_gateway_owner
    FROM pg_proc
    WHERE oid = 'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure;
    SELECT proowner INTO v_canonical_json_owner
    FROM pg_proc
    WHERE oid = 'dnd_generation_private.canonical_json(jsonb)'::regprocedure;
    SELECT proowner INTO v_private_mutation_owner
    FROM pg_proc
    WHERE oid = 'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure;

    v_is_bootstrap_staged := COALESCE(
        v_user_context_owner = v_bootstrap_owner
        AND v_guard_owner = v_bootstrap_owner
        AND v_audit_owner = v_bootstrap_owner
        AND v_private_schema_owner = v_bootstrap_owner
        AND v_gateway_owner = v_bootstrap_owner
        AND v_canonical_json_owner = v_bootstrap_owner
        AND v_private_mutation_owner = v_bootstrap_owner,
        false
    );
    v_is_finalized := COALESCE(
        v_user_context_owner = v_dnd_owner
        AND v_guard_owner = v_dnd_owner
        AND v_audit_owner = v_dnd_owner
        AND v_private_schema_owner = v_dnd_owner
        AND v_gateway_owner = v_dnd_owner
        AND v_canonical_json_owner = v_dnd_owner
        AND v_private_mutation_owner = v_dnd_owner,
        false
    );
    IF NOT v_is_bootstrap_staged AND NOT v_is_finalized THEN
        RAISE EXCEPTION 'DND generation interface ownership is untrusted';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_class AS relation
        WHERE relation.oid = 'public.user_context'::regclass
          AND relation.relrowsecurity
          AND relation.relforcerowsecurity
    )
       OR NOT EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polrelid = 'public.user_context'::regclass
              AND polname = 'dnd_user_context_select' AND polcmd = 'r'
              AND polpermissive
              AND polroles = ARRAY[0]::oid[]
              AND pg_get_expr(polqual, polrelid) = 'true'
       )
       OR NOT EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polrelid = 'public.user_context'::regclass
              AND polname = 'dnd_user_context_insert' AND polcmd = 'a'
              AND polpermissive
              AND polroles = ARRAY[0]::oid[]
              AND lower(COALESCE(pg_get_expr(polwithcheck, polrelid), ''))
                    LIKE '%signal_type%'
              AND lower(COALESCE(pg_get_expr(polwithcheck, polrelid), ''))
                    LIKE '%dnd_generation_owner%'
       )
       OR NOT EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polrelid = 'public.user_context'::regclass
              AND polname = 'dnd_user_context_update' AND polcmd = 'w'
              AND polpermissive
              AND polroles = ARRAY[0]::oid[]
              AND lower(COALESCE(pg_get_expr(polqual, polrelid), ''))
                    LIKE '%signal_type%'
              AND lower(COALESCE(pg_get_expr(polwithcheck, polrelid), ''))
                    LIKE '%dnd_generation_owner%'
       )
       OR NOT EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polrelid = 'public.user_context'::regclass
              AND polname = 'dnd_user_context_delete' AND polcmd = 'd'
              AND polpermissive
              AND polroles = ARRAY[0]::oid[]
              AND lower(COALESCE(pg_get_expr(polqual, polrelid), ''))
                    LIKE '%dnd_generation_owner%'
       )
       OR EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polrelid = 'public.user_context'::regclass
              AND polname NOT IN (
                  'dnd_user_context_select',
                  'dnd_user_context_insert',
                  'dnd_user_context_update',
                  'dnd_user_context_delete'
              )
       ) THEN
        RAISE EXCEPTION 'DND user_context RLS catalog proof is incomplete';
    END IF;

    -- Pin function lookup before handing ownership to the NOLOGIN role.  The
    -- invoker gateway proves current_user; the private definer must re-check
    -- the active SET ROLE because current_user becomes its owner there.
    IF EXISTS (
        SELECT 1
        FROM pg_proc
        WHERE oid = 'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure
          AND prosecdef
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc
        WHERE oid = 'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure
          AND NOT prosecdef
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc
        WHERE oid = 'dnd_generation_private.canonical_json(jsonb)'::regprocedure
          AND prosecdef
    ) THEN
        RAISE EXCEPTION 'DND authority function security attributes are untrusted';
    END IF;
    EXECUTE 'ALTER FUNCTION public.context_dnd_mutate(uuid, text, text, timestamptz, text, real, jsonb) '
        || 'SET search_path = pg_catalog, public, dnd_generation_private, pg_temp';
    EXECUTE 'ALTER FUNCTION dnd_generation_private.canonical_json(jsonb) '
        || 'SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb) '
        || 'SET search_path = pg_catalog, public, pg_temp';

    IF v_is_bootstrap_staged THEN
        EXECUTE 'ALTER TABLE public.user_context OWNER TO dnd_generation_owner';
        EXECUTE 'ALTER TABLE public.dnd_generation_guard OWNER TO dnd_generation_owner';
        EXECUTE 'ALTER TABLE public.dnd_generation_mutations OWNER TO dnd_generation_owner';
        EXECUTE 'ALTER SCHEMA dnd_generation_private OWNER TO dnd_generation_owner';
        EXECUTE 'ALTER FUNCTION public.context_dnd_mutate(uuid, text, text, timestamptz, text, real, jsonb) OWNER TO dnd_generation_owner';
        EXECUTE 'ALTER FUNCTION dnd_generation_private.canonical_json(jsonb) OWNER TO dnd_generation_owner';
        EXECUTE 'ALTER FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb) OWNER TO dnd_generation_owner';
    END IF;

    EXECUTE 'REVOKE CREATE ON SCHEMA public FROM dnd_generation_owner';
    EXECUTE 'GRANT USAGE ON SCHEMA public TO dnd_generation_owner';
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_private FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA dnd_generation_private FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA dnd_generation_private FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_admin FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE dnd_generation_admin.bootstrap_configuration FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.dnd_generation_guard FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.dnd_generation_mutations FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.context_dnd_mutate(uuid, text, text, timestamptz, text, real, jsonb) FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_private.canonical_json(jsonb) FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb) FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.finalize_interface() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_interface() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.user_context FROM PUBLIC';

    -- Keep the existing development/non-DND fallback usable by the shared
    -- login. FORCE RLS still rejects every direct DND or DND-crossing write.
    EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE ON TABLE public.user_context TO %I',
        v_migration_role
    );
    EXECUTE format('REVOKE DELETE ON TABLE public.user_context FROM %I', v_migration_role);
    EXECUTE format(
        'REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.user_context FROM %I',
        v_migration_role
    );

    -- The shared login may retain ordinary non-DND table access through its
    -- runtime role.  It retains no DND authority objects; forced RLS denies
    -- direct DND/crossing DML even when that legacy non-DND grant exists.
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_admin FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_private FROM %I', v_migration_role);
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON TABLE dnd_generation_admin.bootstrap_configuration FROM %I',
        v_migration_role
    );
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.dnd_generation_guard FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.dnd_generation_mutations FROM %I', v_migration_role);
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION public.context_dnd_mutate(uuid, text, text, timestamptz, text, real, jsonb) FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_private.canonical_json(jsonb) FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb) FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.finalize_interface() FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_interface() FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM %I',
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
            EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_admin FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_private FROM %I', v_runtime_role);
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON TABLE dnd_generation_admin.bootstrap_configuration FROM %I',
                v_runtime_role
            );
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.dnd_generation_guard FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.dnd_generation_mutations FROM %I', v_runtime_role);
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION public.context_dnd_mutate(uuid, text, text, timestamptz, text, real, jsonb) FROM %I',
                v_runtime_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_private.canonical_json(jsonb) FROM %I',
                v_runtime_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb) FROM %I',
                v_runtime_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.finalize_interface() FROM %I',
                v_runtime_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_interface() FROM %I',
                v_runtime_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM %I',
                v_runtime_role
            );
            -- Keep the existing public context read/non-DND write matrix.
            EXECUTE format('GRANT SELECT, INSERT, UPDATE ON TABLE public.user_context TO %I', v_runtime_role);
            EXECUTE format('REVOKE DELETE ON TABLE public.user_context FROM %I', v_runtime_role);
            EXECUTE format(
                'REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLE public.user_context FROM %I',
                v_runtime_role
            );
            EXECUTE format('GRANT SELECT ON TABLE public.dnd_generation_guard TO %I', v_runtime_role);
        END IF;
    END LOOP;

    -- PostgreSQL checks the private function EXECUTE ACL using the invoker's
    -- current role even when it is called from a SECURITY INVOKER gateway. The
    -- private definer therefore independently validates active SET ROLE and
    -- writer identity; no PUBLIC or noncanonical role receives this privilege.
    FOREACH v_runtime_role IN ARRAY ARRAY['butler_general_rw', 'butler_switchboard_rw']::name[] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime_role) THEN
            RAISE EXCEPTION 'DND canonical runtime role % is missing', v_runtime_role;
        END IF;
        EXECUTE format('GRANT USAGE ON SCHEMA dnd_generation_private TO %I', v_runtime_role);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.context_dnd_mutate(uuid, text, text, timestamptz, text, real, jsonb) TO %I',
            v_runtime_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb) TO %I',
            v_runtime_role
        );
    END LOOP;

    -- The gateway and private definer are deliberately the only narrow
    -- canonical writer interface. Verify their exact ACL shape after each
    -- finalization so an unlisted runtime/group grant cannot survive a rerun.
    IF NOT has_function_privilege(
        v_general_runtime_role,
        'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure,
        'EXECUTE'
    ) OR NOT has_function_privilege(
        v_switchboard_runtime_role,
        'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure,
        'EXECUTE'
    ) OR NOT has_function_privilege(
        v_general_runtime_role,
        'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure,
        'EXECUTE'
    ) OR NOT has_function_privilege(
        v_switchboard_runtime_role,
        'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure,
        'EXECUTE'
    ) OR NOT has_schema_privilege(
        v_general_runtime_role,
        'dnd_generation_private'::regnamespace,
        'USAGE'
    ) OR NOT has_schema_privilege(
        v_switchboard_runtime_role,
        'dnd_generation_private'::regnamespace,
        'USAGE'
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS context_relation,
             LATERAL aclexplode(
                 COALESCE(context_relation.relacl, acldefault('r', context_relation.relowner))
             ) AS acl
        WHERE context_relation.oid = 'public.user_context'::regclass
          AND acl.privilege_type IN ('DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER')
          AND acl.grantee <> v_dnd_owner
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS guard_relation,
             LATERAL aclexplode(
                 COALESCE(guard_relation.relacl, acldefault('r', guard_relation.relowner))
             ) AS acl
        WHERE guard_relation.oid = 'public.dnd_generation_guard'::regclass
          AND acl.privilege_type <> 'SELECT'
          AND acl.grantee <> v_dnd_owner
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc AS interface_function,
             LATERAL aclexplode(
                 COALESCE(
                     interface_function.proacl,
                     acldefault('f', interface_function.proowner)
                 )
             ) AS acl
        WHERE interface_function.oid =
                  'dnd_generation_private.canonical_json(jsonb)'::regprocedure
          AND acl.privilege_type = 'EXECUTE'
          AND acl.grantee <> v_dnd_owner
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc AS interface_function,
             LATERAL aclexplode(
                 COALESCE(
                     interface_function.proacl,
                     acldefault('f', interface_function.proowner)
                 )
             ) AS acl
        WHERE interface_function.oid =
                  'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure
          AND acl.privilege_type = 'EXECUTE'
          AND acl.grantee NOT IN (
              v_dnd_owner, v_general_runtime_role, v_switchboard_runtime_role
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc AS interface_function,
             LATERAL aclexplode(
                 COALESCE(
                     interface_function.proacl,
                     acldefault('f', interface_function.proowner)
                 )
             ) AS acl
        WHERE interface_function.oid =
                  'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'::regprocedure
          AND acl.privilege_type = 'EXECUTE'
          AND acl.grantee NOT IN (
              v_dnd_owner, v_general_runtime_role, v_switchboard_runtime_role
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_namespace AS private_schema,
             LATERAL aclexplode(
                 COALESCE(private_schema.nspacl, acldefault('n', private_schema.nspowner))
             ) AS acl
        WHERE private_schema.nspname = 'dnd_generation_private'
          AND acl.grantee NOT IN (
              v_dnd_owner, v_general_runtime_role, v_switchboard_runtime_role
          )
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS audit_relation,
             LATERAL aclexplode(
                 COALESCE(audit_relation.relacl, acldefault('r', audit_relation.relowner))
             ) AS acl
        WHERE audit_relation.oid = 'public.dnd_generation_mutations'::regclass
          AND acl.grantee <> v_dnd_owner
    ) OR EXISTS (
        SELECT 1
        FROM pg_namespace AS admin_schema,
             LATERAL aclexplode(
                 COALESCE(admin_schema.nspacl, acldefault('n', admin_schema.nspowner))
             ) AS acl
        WHERE admin_schema.nspname = 'dnd_generation_admin'
          AND acl.grantee <> v_bootstrap_owner
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS configuration,
             LATERAL aclexplode(
                 COALESCE(configuration.relacl, acldefault('r', configuration.relowner))
             ) AS acl
        WHERE configuration.oid = 'dnd_generation_admin.bootstrap_configuration'::regclass
          AND acl.grantee <> v_bootstrap_owner
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc AS admin_function,
             LATERAL aclexplode(
                 COALESCE(admin_function.proacl, acldefault('f', admin_function.proowner))
             ) AS acl
        WHERE admin_function.oid IN (
            'dnd_generation_admin.install_interface()'::regprocedure,
            'dnd_generation_admin.finalize_interface()'::regprocedure,
            'dnd_generation_admin.rollback_interface()'::regprocedure
        )
          AND acl.privilege_type = 'EXECUTE'
          AND acl.grantee <> v_bootstrap_owner
    ) THEN
        RAISE EXCEPTION 'DND authority ACL finalization is incomplete';
    END IF;

    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE dnd_generation_owner IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE dnd_generation_owner IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE dnd_generation_owner IN SCHEMA dnd_generation_private REVOKE ALL ON TABLES FROM PUBLIC';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE dnd_generation_owner IN SCHEMA dnd_generation_private REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';
END;
$dnd_finalizer$;

CREATE OR REPLACE FUNCTION dnd_generation_admin.install_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $dnd_installer$
DECLARE
    v_migration_role NAME;
    v_bootstrap_owner NAME;
BEGIN
    SELECT migration_role, bootstrap_role
    INTO v_migration_role, v_bootstrap_owner
    FROM dnd_generation_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL OR v_bootstrap_owner IS NULL THEN
        RAISE EXCEPTION 'DND generation bootstrap configuration is missing';
    END IF;

    -- ``user_context`` is the legacy shared table this installer hardens.  All
    -- new authority objects must be absent; an arbitrary compatible object is
    -- never repaired or accepted as bootstrap provenance.
    IF to_regclass('public.user_context') IS NULL THEN
        RAISE EXCEPTION 'DND generation requires the canonical public.user_context table';
    END IF;
    -- Preserve this lock through the installer/finalizer handoff. Without it,
    -- the former shared table owner could race a policy/trigger/shape change
    -- between the preflight and final ownership transfer.
    LOCK TABLE public.user_context IN ACCESS EXCLUSIVE MODE;
    -- The bounded rollback returns exactly this known ordinary posture. Do not
    -- harden a table whose owner/RLS state would make that reversal ambiguous.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class AS relation
        JOIN pg_roles AS migration_role ON migration_role.oid = relation.relowner
        WHERE relation.oid = 'public.user_context'::regclass
          AND migration_role.rolname = v_migration_role
    ) OR EXISTS (
        SELECT 1
        FROM pg_class AS relation
        WHERE relation.oid = 'public.user_context'::regclass
          AND (relation.relrowsecurity OR relation.relforcerowsecurity)
    ) THEN
        RAISE EXCEPTION
            'DND generation requires the recorded pre-guard ownership and ordinary RLS posture';
    END IF;
    IF to_regclass('public.dnd_generation_guard') IS NOT NULL
       OR to_regclass('public.dnd_generation_mutations') IS NOT NULL
       OR to_regnamespace('dnd_generation_private') IS NOT NULL
       OR to_regprocedure(
            'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NOT NULL
       OR to_regprocedure(
            'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NOT NULL
       -- RLS permissive policies compose with OR. Do not permit an
       -- attacker-created broad policy to survive beside the DND policies we
       -- are about to install: the pre-DND table must have no user policies at
       -- all, not merely no policy with a familiar DND name.
       OR EXISTS (
            SELECT 1 FROM pg_policy
            WHERE polrelid = 'public.user_context'::regclass
       )
       OR EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgrelid = 'public.user_context'::regclass
              AND NOT tgisinternal
       ) THEN
        RAISE EXCEPTION
            'DND authority interface must be absent before fixed bootstrap installation';
    END IF;
    -- This is an ownership handoff of a pre-existing shared table. Verify the
    -- entire known core shape rather than adopting a compatible-looking table
    -- with attacker-added columns, altered types, or a missing upsert key.
    IF (SELECT count(*)
        FROM pg_attribute AS attribute
        WHERE attribute.attrelid = 'public.user_context'::regclass
          AND attribute.attnum > 0
          AND NOT attribute.attisdropped) <> 9
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'id'
              AND attribute.atttypid = 'uuid'::regtype
              AND attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'signal_type'
              AND attribute.atttypid = 'text'::regtype
              AND attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'value'
              AND attribute.atttypid = 'text'::regtype
              AND NOT attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'set_by_butler'
              AND attribute.atttypid = 'text'::regtype
              AND attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'set_at'
              AND attribute.atttypid = 'timestamptz'::regtype
              AND attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'expires_at'
              AND attribute.atttypid = 'timestamptz'::regtype
              AND attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'confidence'
              AND attribute.atttypid = 'real'::regtype
              AND attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'metadata'
              AND attribute.atttypid = 'jsonb'::regtype
              AND NOT attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_attribute AS attribute
            WHERE attribute.attrelid = 'public.user_context'::regclass
              AND attribute.attname = 'superseded_at'
              AND attribute.atttypid = 'timestamptz'::regtype
              AND NOT attribute.attnotnull
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_constraint AS constraint_row
            WHERE constraint_row.conrelid = 'public.user_context'::regclass
              AND constraint_row.contype = 'p'
              AND constraint_row.conkey = ARRAY[
                  (SELECT attribute.attnum
                   FROM pg_attribute AS attribute
                   WHERE attribute.attrelid = 'public.user_context'::regclass
                     AND attribute.attname = 'id')
              ]::smallint[]
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_constraint AS constraint_row
            WHERE constraint_row.conrelid = 'public.user_context'::regclass
              AND constraint_row.contype = 'u'
              AND constraint_row.conkey = ARRAY[
                  (SELECT attribute.attnum
                   FROM pg_attribute AS attribute
                   WHERE attribute.attrelid = 'public.user_context'::regclass
                     AND attribute.attname = 'signal_type'),
                  (SELECT attribute.attnum
                   FROM pg_attribute AS attribute
                   WHERE attribute.attrelid = 'public.user_context'::regclass
                     AND attribute.attname = 'set_by_butler')
              ]::smallint[]
       )
       OR (SELECT count(*)
           FROM pg_constraint AS constraint_row
           WHERE constraint_row.conrelid = 'public.user_context'::regclass) <> 3
       OR NOT EXISTS (
            SELECT 1
            FROM pg_constraint AS constraint_row
            WHERE constraint_row.conrelid = 'public.user_context'::regclass
              AND constraint_row.conname = 'user_context_confidence_check'
              AND constraint_row.contype = 'c'
       )
       OR EXISTS (
            SELECT 1
            FROM pg_depend AS dependency
            JOIN pg_proc AS referenced_function ON referenced_function.oid = dependency.refobjid
            JOIN pg_namespace AS function_schema
                ON function_schema.oid = referenced_function.pronamespace
            JOIN pg_constraint AS constraint_row ON constraint_row.oid = dependency.objid
            WHERE dependency.classid = 'pg_constraint'::regclass
              AND dependency.refclassid = 'pg_proc'::regclass
              AND constraint_row.conrelid = 'public.user_context'::regclass
              AND function_schema.nspname <> 'pg_catalog'
       )
       OR EXISTS (
            SELECT 1
            FROM pg_depend AS dependency
            JOIN pg_operator AS referenced_operator ON referenced_operator.oid = dependency.refobjid
            JOIN pg_namespace AS operator_schema
                ON operator_schema.oid = referenced_operator.oprnamespace
            JOIN pg_constraint AS constraint_row ON constraint_row.oid = dependency.objid
            WHERE dependency.classid = 'pg_constraint'::regclass
              AND dependency.refclassid = 'pg_operator'::regclass
              AND constraint_row.conrelid = 'public.user_context'::regclass
              AND operator_schema.nspname <> 'pg_catalog'
       )
       OR (SELECT count(*)
           FROM pg_index AS index_row
           WHERE index_row.indrelid = 'public.user_context'::regclass) <> 3
       OR EXISTS (
            SELECT 1
            FROM pg_index AS index_row
            WHERE index_row.indrelid = 'public.user_context'::regclass
              AND index_row.indexprs IS NOT NULL
       )
       OR NOT EXISTS (
            SELECT 1
            FROM pg_index AS index_row
            JOIN pg_class AS index_relation ON index_relation.oid = index_row.indexrelid
            WHERE index_row.indrelid = 'public.user_context'::regclass
              AND index_relation.relname = 'idx_user_context_active_signals'
              AND NOT index_row.indisprimary
              AND NOT index_row.indisunique
              -- pg_index.indkey is an int2vector, not a smallint[] like
              -- pg_constraint.conkey. Compare its single key directly while
              -- proving no included or additional index attributes exist.
              AND index_row.indnkeyatts = 1
              AND index_row.indnatts = 1
              AND index_row.indkey[0] = (
                  SELECT attribute.attnum
                  FROM pg_attribute AS attribute
                  WHERE attribute.attrelid = 'public.user_context'::regclass
                    AND attribute.attname = 'signal_type'
              )
              AND pg_get_expr(index_row.indpred, index_row.indrelid)
                    = '(superseded_at IS NULL)'
       )
       OR EXISTS (
            SELECT 1
            FROM pg_rewrite AS rewrite_rule
            WHERE rewrite_rule.ev_class = 'public.user_context'::regclass
              AND rewrite_rule.rulename <> '_RETURN'
       ) THEN
        RAISE EXCEPTION 'DND generation requires the canonical user_context shape';
    END IF;

    EXECUTE format('ALTER TABLE public.user_context OWNER TO %I', v_bootstrap_owner);
    CREATE SCHEMA dnd_generation_private AUTHORIZATION dnd_generation_owner;
    -- Create it under the bootstrap owner first so finalizer provenance is
    -- explicit; the final ownership handoff happens only after full catalog
    -- and ACL proof.
    EXECUTE format('ALTER SCHEMA dnd_generation_private OWNER TO %I', v_bootstrap_owner);

    CREATE TABLE public.dnd_generation_guard (
        guard_id SMALLINT PRIMARY KEY DEFAULT 1 CHECK (guard_id = 1),
        generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    );
    INSERT INTO public.dnd_generation_guard (guard_id, generation)
    VALUES (1, 0);

    CREATE TABLE public.dnd_generation_mutations (
        mutation_id UUID PRIMARY KEY,
        generation BIGINT NOT NULL CHECK (generation >= 0),
        writer TEXT NOT NULL CHECK (writer IN ('general', 'switchboard')),
        operation TEXT NOT NULL CHECK (operation IN ('set', 'clear')),
        correlation TEXT NOT NULL CHECK (
            correlation ~ '^dnd-action:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        ),
        requested_expires_at TIMESTAMPTZ,
        effective_expires_at TIMESTAMPTZ,
        semantic_fingerprint_version SMALLINT NOT NULL,
        semantic_fingerprint TEXT NOT NULL,
        committed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        CHECK (
            (operation = 'set' AND effective_expires_at IS NOT NULL)
            OR (operation = 'clear' AND requested_expires_at IS NULL AND effective_expires_at IS NULL)
        )
    );
    CREATE UNIQUE INDEX dnd_generation_mutations_generation_key
        ON public.dnd_generation_mutations (generation);

    ALTER TABLE public.user_context ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.user_context FORCE ROW LEVEL SECURITY;
    CREATE POLICY dnd_user_context_select ON public.user_context
        FOR SELECT TO PUBLIC USING (true);
    CREATE POLICY dnd_user_context_insert ON public.user_context
        FOR INSERT TO PUBLIC
        WITH CHECK (signal_type <> 'dnd' OR current_user = 'dnd_generation_owner');
    CREATE POLICY dnd_user_context_update ON public.user_context
        FOR UPDATE TO PUBLIC
        USING (signal_type <> 'dnd' OR current_user = 'dnd_generation_owner')
        WITH CHECK (signal_type <> 'dnd' OR current_user = 'dnd_generation_owner');
    CREATE POLICY dnd_user_context_delete ON public.user_context
        FOR DELETE TO PUBLIC
        USING (signal_type <> 'dnd' OR current_user = 'dnd_generation_owner');

    CREATE FUNCTION dnd_generation_private.canonical_json(p_document JSONB)
    RETURNS TEXT
    LANGUAGE plpgsql
    IMMUTABLE
    SECURITY INVOKER
    SET search_path = pg_catalog, pg_temp
    AS $dnd_canonical_json$
    DECLARE
        v_kind TEXT;
        v_rendered TEXT;
    BEGIN
        IF p_document IS NULL THEN
            RETURN 'null';
        END IF;

        v_kind := jsonb_typeof(p_document);
        IF v_kind = 'object' THEN
            -- JSONB gives deterministic structural semantics, while this
            -- explicit rendering also normalizes Unicode object keys. A pair
            -- of distinct source keys that normalizes to the same NFC key is
            -- ambiguous for replay identity and must fail closed.
            IF EXISTS (
                SELECT 1
                FROM (
                    SELECT "normalize"(entry.key, 'NFC') AS normalized_key
                    FROM jsonb_each(p_document) AS entry
                ) AS normalized_keys
                GROUP BY normalized_key
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'DND metadata has duplicate NFC-normalized keys';
            END IF;

            SELECT '{' || string_agg(
                to_jsonb("normalize"(entry.key, 'NFC'))::text
                || ':' || dnd_generation_private.canonical_json(entry.value),
                ',' ORDER BY convert_to("normalize"(entry.key, 'NFC'), 'UTF8')
            ) || '}'
            INTO v_rendered
            FROM jsonb_each(p_document) AS entry;
            RETURN COALESCE(v_rendered, '{}');
        ELSIF v_kind = 'array' THEN
            SELECT '[' || string_agg(
                dnd_generation_private.canonical_json(entry.value),
                ',' ORDER BY entry.ordinality
            ) || ']'
            INTO v_rendered
            FROM jsonb_array_elements(p_document) WITH ORDINALITY AS entry(value, ordinality);
            RETURN COALESCE(v_rendered, '[]');
        ELSIF v_kind = 'string' THEN
            RETURN to_jsonb("normalize"(p_document #>> '{}', 'NFC'))::text;
        ELSIF v_kind = 'number' THEN
            -- JSONB rejects non-JSON numeric input, but keep the canonical
            -- form explicit: numeric display scale must not make 1, 1.0, and
            -- 1.00 distinct replay identities. PostgreSQL numeric NaN is not
            -- a valid JSON number and is rejected defensively if encountered.
            IF (p_document #>> '{}')::numeric = 'NaN'::numeric THEN
                RAISE EXCEPTION 'DND metadata numeric value is not finite';
            END IF;
            RETURN trim_scale((p_document #>> '{}')::numeric)::text;
        END IF;

        -- JSONB renders scalar booleans and null deterministically.
        RETURN p_document::text;
    END;
    $dnd_canonical_json$;

    CREATE FUNCTION dnd_generation_private.mutate(
        p_mutation_id UUID,
        p_writer TEXT,
        p_operation TEXT,
        p_requested_expires_at TIMESTAMPTZ,
        p_value TEXT,
        p_confidence REAL,
        p_metadata JSONB
    )
    RETURNS TABLE (
        mutation_id UUID,
        generation BIGINT,
        writer TEXT,
        operation TEXT,
        correlation TEXT,
        requested_expires_at TIMESTAMPTZ,
        effective_expires_at TIMESTAMPTZ,
        committed_at TIMESTAMPTZ
    )
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $dnd_private$
    DECLARE
        v_active_role TEXT := NULLIF(current_setting('role', true), 'none');
        v_effective_writer TEXT;
        v_now TIMESTAMPTZ;
        v_effective_expires_at TIMESTAMPTZ;
        v_guard_generation BIGINT;
        v_existing public.dnd_generation_mutations%ROWTYPE;
        v_has_existing BOOLEAN := false;
        v_correlation TEXT;
        v_requested_expiry_canonical TEXT;
        v_effective_expiry_canonical TEXT;
        v_confidence_canonical TEXT;
        v_value_normalized TEXT;
        v_value_digest TEXT;
        v_metadata_digest TEXT;
        v_fingerprint TEXT;
    BEGIN
        IF v_active_role = 'butler_general_rw' THEN
            v_effective_writer := 'general';
        ELSIF v_active_role = 'butler_switchboard_rw' THEN
            v_effective_writer := 'switchboard';
        ELSE
            RAISE EXCEPTION 'DND mutation requires an active canonical runtime role';
        END IF;
        IF p_writer IS DISTINCT FROM v_effective_writer THEN
            RAISE EXCEPTION 'DND writer does not match the active runtime role';
        END IF;
        IF p_mutation_id IS NULL THEN
            RAISE EXCEPTION 'DND mutation requires stable mutation_id';
        END IF;
        v_correlation := 'dnd-action:' || p_mutation_id::text;
        IF p_operation NOT IN ('set', 'clear') THEN
            RAISE EXCEPTION 'DND operation must be set or clear';
        END IF;
        IF p_operation = 'clear'
           AND (p_requested_expires_at IS NOT NULL OR p_value IS NOT NULL
                OR p_confidence IS NOT NULL OR p_metadata IS NOT NULL) THEN
            RAISE EXCEPTION 'DND clear cannot carry set payload fields';
        END IF;
        IF p_operation = 'set'
           AND (p_confidence IS NULL OR p_confidence < 0.0 OR p_confidence > 1.0) THEN
            RAISE EXCEPTION 'DND confidence must be in [0, 1]';
        END IF;

        SELECT guard.generation INTO v_guard_generation
        FROM public.dnd_generation_guard AS guard
        WHERE guard.guard_id = 1
        FOR UPDATE;
        IF v_guard_generation IS NULL OR v_guard_generation < 0 THEN
            RAISE EXCEPTION 'DND generation guard is missing or invalid';
        END IF;

        SELECT * INTO v_existing
        FROM public.dnd_generation_mutations AS receipt
        WHERE receipt.mutation_id = p_mutation_id;
        v_has_existing := FOUND;

        IF v_has_existing THEN
            IF v_existing.semantic_fingerprint_version <> 1
               OR v_existing.semantic_fingerprint IS NULL
               OR (v_existing.operation = 'set' AND v_existing.effective_expires_at IS NULL)
               OR (v_existing.operation = 'clear'
                   AND (v_existing.requested_expires_at IS NOT NULL
                        OR v_existing.effective_expires_at IS NOT NULL)) THEN
                RAISE EXCEPTION 'replay_identity_unprovable';
            END IF;
            v_effective_expires_at := v_existing.effective_expires_at;
        ELSE
            v_now := clock_timestamp();
            IF p_operation = 'set' THEN
                v_effective_expires_at := LEAST(
                    COALESCE(p_requested_expires_at, v_now + interval '2 hours'),
                    v_now + interval '24 hours'
                );
            ELSE
                v_effective_expires_at := NULL;
            END IF;
        END IF;

        v_requested_expiry_canonical := CASE
            WHEN p_requested_expires_at IS NULL THEN NULL
            ELSE to_char(
                p_requested_expires_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        END;
        v_effective_expiry_canonical := CASE
            WHEN v_effective_expires_at IS NULL THEN NULL
            ELSE to_char(
                v_effective_expires_at AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
            )
        END;
        v_confidence_canonical := CASE
            WHEN p_confidence IS NULL THEN NULL
            ELSE encode(float4send(p_confidence), 'hex')
        END;
        v_value_normalized := CASE
            WHEN p_value IS NULL THEN NULL
            ELSE "normalize"(p_value, 'NFC')
        END;
        v_value_digest := CASE
            WHEN v_value_normalized IS NULL THEN encode(
                digest(convert_to('dnd-value:null', 'UTF8'), 'sha256'), 'hex'
            )
            ELSE encode(
                digest(
                    convert_to('dnd-value:string:' || v_value_normalized, 'UTF8'),
                    'sha256'
                ),
                'hex'
            )
        END;
        v_metadata_digest := encode(
            digest(
                convert_to(
                    CASE
                        WHEN p_metadata IS NULL THEN 'dnd-metadata:absent'
                        ELSE 'dnd-metadata:json:'
                            || dnd_generation_private.canonical_json(p_metadata)
                    END,
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        );
        v_fingerprint := encode(
            digest(
                convert_to(
                    dnd_generation_private.canonical_json(
                        jsonb_build_object(
                            'protocol', 'context.dnd.mutate.v1',
                            'signal_type', 'dnd',
                            'writer', v_effective_writer,
                            'set_by_butler', v_effective_writer,
                            'operation', p_operation,
                            'correlation', v_correlation,
                            'requested_expires_at', v_requested_expiry_canonical,
                            'effective_expires_at', v_effective_expiry_canonical,
                            'confidence_float4', v_confidence_canonical,
                            'value_digest', v_value_digest,
                            'metadata_digest', v_metadata_digest
                        )
                    ),
                    'UTF8'
                ),
                'sha256'
            ),
            'hex'
        );

        IF v_has_existing THEN
            IF v_existing.writer IS DISTINCT FROM v_effective_writer
               OR v_existing.operation IS DISTINCT FROM p_operation
               OR v_existing.correlation IS DISTINCT FROM v_correlation
               OR v_existing.semantic_fingerprint IS DISTINCT FROM v_fingerprint THEN
                RAISE EXCEPTION 'idempotency_conflict';
            END IF;
            RETURN QUERY
            SELECT v_existing.mutation_id, v_existing.generation, v_existing.writer,
                   v_existing.operation, v_existing.correlation,
                   v_existing.requested_expires_at, v_existing.effective_expires_at,
                   v_existing.committed_at;
            RETURN;
        END IF;

        IF v_guard_generation = 9223372036854775807 THEN
            RAISE EXCEPTION 'DND generation is exhausted';
        END IF;
        IF v_now IS NULL THEN
            v_now := clock_timestamp();
        END IF;

        IF p_operation = 'set' THEN
            INSERT INTO public.user_context (
                id, signal_type, value, set_by_butler, set_at, expires_at, confidence,
                metadata, superseded_at
            )
            VALUES (
                gen_random_uuid(), 'dnd', p_value, v_effective_writer, v_now, v_effective_expires_at,
                p_confidence, p_metadata, NULL
            )
            ON CONFLICT (signal_type, set_by_butler) DO UPDATE
                SET value = EXCLUDED.value,
                    set_at = EXCLUDED.set_at,
                    expires_at = EXCLUDED.expires_at,
                    confidence = EXCLUDED.confidence,
                    metadata = EXCLUDED.metadata,
                    superseded_at = NULL;
        ELSE
            UPDATE public.user_context
            SET superseded_at = v_now
            WHERE signal_type = 'dnd'
              AND set_by_butler = v_effective_writer
              AND superseded_at IS NULL;
        END IF;

        UPDATE public.dnd_generation_guard AS guard
        SET generation = guard.generation + 1,
            updated_at = v_now
        WHERE guard.guard_id = 1
        RETURNING guard.generation INTO v_guard_generation;

        INSERT INTO public.dnd_generation_mutations (
            mutation_id, generation, writer, operation, correlation,
            requested_expires_at, effective_expires_at,
            semantic_fingerprint_version, semantic_fingerprint, committed_at
        )
        VALUES (
            p_mutation_id, v_guard_generation, v_effective_writer, p_operation,
            v_correlation, p_requested_expires_at, v_effective_expires_at,
            1, v_fingerprint, v_now
        );

        RETURN QUERY
        SELECT p_mutation_id, v_guard_generation, v_effective_writer, p_operation,
               v_correlation, p_requested_expires_at, v_effective_expires_at,
               v_now;
    END;
    $dnd_private$;

    CREATE FUNCTION public.context_dnd_mutate(
        p_mutation_id UUID,
        p_writer TEXT,
        p_operation TEXT,
        p_requested_expires_at TIMESTAMPTZ,
        p_value TEXT,
        p_confidence REAL,
        p_metadata JSONB
    )
    RETURNS TABLE (
        mutation_id UUID,
        generation BIGINT,
        writer TEXT,
        operation TEXT,
        correlation TEXT,
        requested_expires_at TIMESTAMPTZ,
        effective_expires_at TIMESTAMPTZ,
        committed_at TIMESTAMPTZ
    )
    LANGUAGE plpgsql
    SECURITY INVOKER
    SET search_path = pg_catalog, public, dnd_generation_private, pg_temp
    AS $dnd_gateway$
    DECLARE
        v_effective_writer TEXT;
    BEGIN
        IF current_user = 'butler_general_rw' THEN
            v_effective_writer := 'general';
        ELSIF current_user = 'butler_switchboard_rw' THEN
            v_effective_writer := 'switchboard';
        ELSE
            RAISE EXCEPTION 'DND gateway requires an active canonical runtime role';
        END IF;
        IF p_writer IS DISTINCT FROM v_effective_writer THEN
            RAISE EXCEPTION 'DND writer does not match the gateway active role';
        END IF;
        RETURN QUERY
        SELECT * FROM dnd_generation_private.mutate(
            p_mutation_id, p_writer, p_operation,
            p_requested_expires_at, p_value, p_confidence, p_metadata
        );
    END;
    $dnd_gateway$;

    PERFORM dnd_generation_admin.finalize_interface();
END;
$dnd_installer$;

CREATE OR REPLACE FUNCTION dnd_generation_admin.rollback_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $dnd_rollback$
DECLARE
    v_migration_role NAME;
    v_generation BIGINT;
    v_session_is_superuser BOOLEAN;
BEGIN
    SELECT rolsuper
    INTO v_session_is_superuser
    FROM pg_roles
    WHERE rolname = session_user;
    IF NOT COALESCE(v_session_is_superuser, false) THEN
        RAISE EXCEPTION 'core_197 downgrade requires the managed privileged bootstrap owner';
    END IF;

    IF to_regclass('public.user_context') IS NULL
       OR to_regclass('public.dnd_generation_guard') IS NULL
       OR to_regclass('public.dnd_generation_mutations') IS NULL
       OR to_regnamespace('dnd_generation_private') IS NULL
       OR to_regprocedure(
            'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NULL
       OR to_regprocedure(
            'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NULL
       OR to_regprocedure('dnd_generation_private.canonical_json(jsonb)') IS NULL THEN
        RAISE EXCEPTION 'core_197 rollback requires the complete trusted DND interface';
    END IF;

    -- A durable replay receipt or any advanced generation proves that a
    -- consumer may depend on this boundary.  That state needs an explicitly
    -- planned, audited recovery migration rather than destructive reseeding.
    IF EXISTS (SELECT 1 FROM public.dnd_generation_mutations) THEN
        RAISE EXCEPTION
            'core_197 rollback refuses durable DND replay receipts; use a planned audited rollback';
    END IF;
    SELECT generation
    INTO v_generation
    FROM public.dnd_generation_guard
    WHERE guard_id = 1;
    IF NOT FOUND OR v_generation IS NULL OR v_generation <> 0 THEN
        RAISE EXCEPTION
            'core_197 rollback refuses a nonzero DND generation; use a planned audited rollback';
    END IF;

    -- This only accepts the bootstrap-produced shape.  It cannot adopt an
    -- attacker-shaped authority boundary before reopening the ordinary path.
    PERFORM dnd_generation_admin.finalize_interface();
    LOCK TABLE public.user_context IN ACCESS EXCLUSIVE MODE;

    SELECT generation
    INTO v_generation
    FROM public.dnd_generation_guard
    WHERE guard_id = 1
    FOR UPDATE;
    IF NOT FOUND OR v_generation IS NULL OR v_generation <> 0 THEN
        RAISE EXCEPTION
            'core_197 rollback refuses a nonzero DND generation; use a planned audited rollback';
    END IF;
    IF EXISTS (SELECT 1 FROM public.dnd_generation_mutations) THEN
        RAISE EXCEPTION
            'core_197 rollback refuses durable DND replay receipts; use a planned audited rollback';
    END IF;

    SELECT migration_role
    INTO v_migration_role
    FROM dnd_generation_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_migration_role) THEN
        RAISE EXCEPTION 'core_197 rollback has no trusted migration-role handoff';
    END IF;

    DROP FUNCTION public.context_dnd_mutate(uuid, text, text, timestamptz, text, real, jsonb);
    DROP FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb);
    DROP FUNCTION dnd_generation_private.canonical_json(jsonb);
    DROP SCHEMA dnd_generation_private;
    DROP TABLE public.dnd_generation_mutations;
    DROP TABLE public.dnd_generation_guard;

    ALTER TABLE public.user_context NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE public.user_context DISABLE ROW LEVEL SECURITY;
    DROP POLICY dnd_user_context_delete ON public.user_context;
    DROP POLICY dnd_user_context_update ON public.user_context;
    DROP POLICY dnd_user_context_insert ON public.user_context;
    DROP POLICY dnd_user_context_select ON public.user_context;
    EXECUTE format('ALTER TABLE public.user_context OWNER TO %I', v_migration_role);

    -- Re-upgrade is still possible, but only through the same fixed bootstrap
    -- installer.  The ordinary migration role never receives rollback access.
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM %I', v_migration_role);
    EXECUTE format('GRANT USAGE ON SCHEMA dnd_generation_admin TO %I', v_migration_role);
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION dnd_generation_admin.install_interface() TO %I',
        v_migration_role
    );
END;
$dnd_rollback$;

REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.finalize_interface() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_interface() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
BEGIN
    -- Keep the migration login unable to call a finalizer or self-install a
    -- named object except through the narrow one-time installer handoff.
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA dnd_generation_admin FROM %I', v_migration_role);
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.finalize_interface() FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_interface() FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM %I',
        v_migration_role
    );

    IF to_regclass('public.dnd_generation_guard') IS NOT NULL
       OR to_regclass('public.dnd_generation_mutations') IS NOT NULL
       OR to_regnamespace('dnd_generation_private') IS NOT NULL
       OR to_regprocedure(
            'public.context_dnd_mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NOT NULL
       OR to_regprocedure(
            'dnd_generation_private.mutate(uuid,text,text,timestamptz,text,real,jsonb)'
       ) IS NOT NULL THEN
        PERFORM dnd_generation_admin.finalize_interface();
    ELSE
        EXECUTE format('GRANT USAGE ON SCHEMA dnd_generation_admin TO %I', v_migration_role);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION dnd_generation_admin.install_interface() TO %I',
            v_migration_role
        );
    END IF;
END;
$$;

RESET ROLE;

-- ── Runtime-attention outbox bootstrap boundary ────────────────────────────
--
-- Runtime-attention is intentionally inert at this point: this boundary only
-- represents server-derived, durable episodes.  The later Switchboard worker
-- owns claiming and delivery.  ``init-db`` gives legacy public tables broad
-- development grants above, so this finalizer runs *after* that baseline on
-- every bootstrap/rerun and repairs the exceptional function-only boundary.

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
    v_schema_owner NAME;
    v_schema_owner_is_superuser BOOLEAN;
    v_owner OID;
    v_owner_is_safe BOOLEAN;
BEGIN
    IF current_user::name = v_migration_role THEN
        RAISE EXCEPTION
            'runtime-attention bootstrap cannot run as the shared migration role';
    END IF;
    IF NOT COALESCE(
        (SELECT rolsuper FROM pg_roles WHERE rolname = current_user),
        false
    ) THEN
        RAISE EXCEPTION 'runtime-attention bootstrap requires a cluster superuser';
    END IF;

    SELECT owner_role.rolname, owner_role.rolsuper
    INTO v_schema_owner, v_schema_owner_is_superuser
    FROM pg_namespace AS admin_schema
    JOIN pg_roles AS owner_role ON owner_role.oid = admin_schema.nspowner
    WHERE admin_schema.nspname = 'runtime_attention_admin';
    IF v_schema_owner IS NULL THEN
        EXECUTE format('CREATE SCHEMA %I AUTHORIZATION %I', 'runtime_attention_admin', current_user);
    ELSIF NOT COALESCE(v_schema_owner_is_superuser, false) THEN
        RAISE EXCEPTION
            'runtime-attention admin schema is not owned by a trusted bootstrap superuser';
    ELSE
        -- A different superuser may perform a later rerun, but all retained
        -- bootstrap objects stay owned by the first proven bootstrap owner.
        EXECUTE format('SET ROLE %I', v_schema_owner);
    END IF;

    SELECT oid INTO v_owner
    FROM pg_roles
    WHERE rolname = 'runtime_attention_outbox_owner';
    IF v_owner IS NULL THEN
        CREATE ROLE runtime_attention_outbox_owner
            NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE NOCREATEDB NOREPLICATION NOBYPASSRLS;
    ELSE
        SELECT NOT rolcanlogin
               AND NOT rolinherit
               AND NOT rolsuper
               AND NOT rolcreaterole
               AND NOT rolcreatedb
               AND NOT rolreplication
               AND NOT rolbypassrls
               AND NOT EXISTS (
                    SELECT 1
                    FROM pg_auth_members
                    WHERE roleid = v_owner OR member = v_owner
               )
        INTO v_owner_is_safe
        FROM pg_roles
        WHERE oid = v_owner;
        IF NOT COALESCE(v_owner_is_safe, false) THEN
            RAISE EXCEPTION
                'runtime-attention outbox owner is untrusted or has runtime membership';
        END IF;
    END IF;
END;
$$;

REVOKE ALL PRIVILEGES ON SCHEMA runtime_attention_admin FROM PUBLIC;

DO $$
DECLARE
    v_bootstrap_owner OID;
    v_existing_owner OID;
BEGIN
    SELECT nspowner INTO v_bootstrap_owner
    FROM pg_namespace
    WHERE nspname = 'runtime_attention_admin';
    SELECT relation.relowner INTO v_existing_owner
    FROM pg_class AS relation
    JOIN pg_namespace AS admin_schema ON admin_schema.oid = relation.relnamespace
    WHERE admin_schema.nspname = 'runtime_attention_admin'
      AND relation.relname = 'bootstrap_configuration'
      AND relation.relkind = 'r';
    IF v_existing_owner IS NOT NULL AND v_existing_owner <> v_bootstrap_owner THEN
        RAISE EXCEPTION
            'runtime-attention bootstrap configuration is not owned by the bootstrap role';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_proc AS admin_function
        JOIN pg_namespace AS admin_schema ON admin_schema.oid = admin_function.pronamespace
        WHERE admin_schema.nspname = 'runtime_attention_admin'
          AND admin_function.proname IN (
              'finalize_interface',
              'install_interface',
              'rollback_interface'
          )
          AND admin_function.pronargs = 0
          AND admin_function.proowner <> v_bootstrap_owner
    ) THEN
        RAISE EXCEPTION
            'runtime-attention admin interface function is not owned by the bootstrap role';
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS runtime_attention_admin.bootstrap_configuration (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
    migration_role NAME NOT NULL,
    bootstrap_role NAME NOT NULL,
    interface_version INTEGER NOT NULL DEFAULT 1 CHECK (interface_version IN (1, 2)),
    producers_enabled BOOLEAN NOT NULL DEFAULT false,
    producer_activated_at TIMESTAMPTZ
);
ALTER TABLE runtime_attention_admin.bootstrap_configuration
    ADD COLUMN IF NOT EXISTS interface_version INTEGER NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS producers_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS producer_activated_at TIMESTAMPTZ;
REVOKE ALL PRIVILEGES ON TABLE runtime_attention_admin.bootstrap_configuration FROM PUBLIC;

INSERT INTO runtime_attention_admin.bootstrap_configuration (
    singleton,
    migration_role,
    bootstrap_role
)
VALUES (
    true,
    COALESCE(NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers')::name,
    (
        SELECT bootstrap_owner.rolname::name
        FROM pg_namespace AS admin_schema
        JOIN pg_roles AS bootstrap_owner ON bootstrap_owner.oid = admin_schema.nspowner
        WHERE admin_schema.nspname = 'runtime_attention_admin'
    )
)
ON CONFLICT (singleton) DO UPDATE SET
    migration_role = EXCLUDED.migration_role,
    bootstrap_role = EXCLUDED.bootstrap_role;

CREATE OR REPLACE FUNCTION runtime_attention_admin.finalize_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_finalizer$
DECLARE
    v_migration_role NAME;
    v_bootstrap_role NAME;
    v_runtime_role NAME;
    v_acl_role NAME;
    v_policy_name NAME;
    v_trigger_name NAME;
    v_outbox_owner OID;
    v_bootstrap_owner OID;
    v_interface_version INTEGER;
BEGIN
    SELECT migration_role, bootstrap_role, interface_version
    INTO v_migration_role, v_bootstrap_role, v_interface_version
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    SELECT oid INTO v_outbox_owner
    FROM pg_roles
    WHERE rolname = 'runtime_attention_outbox_owner';
    SELECT nspowner INTO v_bootstrap_owner
    FROM pg_namespace
    WHERE nspname = 'runtime_attention_admin';
    IF v_migration_role IS NULL OR v_bootstrap_role IS NULL OR v_outbox_owner IS NULL
       OR v_bootstrap_owner IS NULL
       OR NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = v_bootstrap_role), false) THEN
        RAISE EXCEPTION 'runtime-attention bootstrap configuration is untrusted';
    END IF;
    IF to_regclass('public.runtime_attention_outbox') IS NULL
       OR to_regclass('public.runtime_attention_delivery_lease') IS NULL
       OR to_regprocedure('public.append_runtime_attention_model_breaker(bigint)') IS NULL
       OR to_regprocedure('public.append_runtime_attention_fleet_halt()') IS NULL
       OR to_regprocedure('public.runtime_attention_active_switchboard_role()') IS NULL
       OR to_regprocedure('public.runtime_attention_outbox_guard()') IS NULL
       OR to_regprocedure('public.runtime_attention_delivery_lease_guard()') IS NULL THEN
        RAISE EXCEPTION 'runtime-attention interface is incomplete';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_class AS relation
        WHERE relation.oid IN (
            'public.runtime_attention_outbox'::regclass,
            'public.runtime_attention_delivery_lease'::regclass
        )
          AND relation.relowner NOT IN (v_bootstrap_owner, v_outbox_owner)
    ) OR EXISTS (
        SELECT 1
        FROM pg_proc AS interface_function
        WHERE interface_function.oid IN (
            'public.append_runtime_attention_model_breaker(bigint)'::regprocedure,
            'public.append_runtime_attention_fleet_halt()'::regprocedure,
            'public.runtime_attention_active_switchboard_role()'::regprocedure,
            'public.runtime_attention_outbox_guard()'::regprocedure,
            'public.runtime_attention_delivery_lease_guard()'::regprocedure
        )
          AND interface_function.proowner NOT IN (v_bootstrap_owner, v_outbox_owner)
    ) THEN
        RAISE EXCEPTION 'runtime-attention interface ownership is untrusted';
    END IF;

    -- The dedicated owner is deliberately a no-login, membership-free role;
    -- no shared migration/runtime login owns a SECURITY DEFINER function.
    EXECUTE 'ALTER TABLE public.runtime_attention_outbox OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER TABLE public.runtime_attention_delivery_lease OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_model_breaker(bigint) OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_fleet_halt() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_active_switchboard_role() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_outbox_guard() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_delivery_lease_guard() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_model_breaker(bigint) SET search_path = pg_catalog, public, pg_temp';
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_fleet_halt() SET search_path = pg_catalog, public, pg_temp';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_active_switchboard_role() SET search_path = pg_catalog, public, pg_temp';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_outbox_guard() SET search_path = pg_catalog, public, pg_temp';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_delivery_lease_guard() SET search_path = pg_catalog, public, pg_temp';

    EXECUTE 'ALTER TABLE public.runtime_attention_outbox ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE public.runtime_attention_outbox FORCE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE public.runtime_attention_delivery_lease ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE public.runtime_attention_delivery_lease FORCE ROW LEVEL SECURITY';

    -- RLS policies compose permissively.  Remove every non-system policy
    -- before restoring the two fixed policies so an ad-hoc broad policy
    -- cannot survive an init-db rerun beside the active-role gate.
    FOR v_policy_name IN
        SELECT policy.polname::name
        FROM pg_policy AS policy
        WHERE policy.polrelid = 'public.runtime_attention_outbox'::regclass
    LOOP
        EXECUTE format(
            'DROP POLICY %I ON public.runtime_attention_outbox', v_policy_name
        );
    END LOOP;
    EXECUTE 'CREATE POLICY runtime_attention_outbox_owner ON public.runtime_attention_outbox '
        || 'FOR ALL TO runtime_attention_outbox_owner USING (true) WITH CHECK (true)';
    EXECUTE 'CREATE POLICY runtime_attention_outbox_switchboard ON public.runtime_attention_outbox '
        || 'FOR ALL TO butler_switchboard_rw '
        || 'USING (public.runtime_attention_active_switchboard_role()) '
        || 'WITH CHECK (public.runtime_attention_active_switchboard_role())';

    FOR v_policy_name IN
        SELECT policy.polname::name
        FROM pg_policy AS policy
        WHERE policy.polrelid = 'public.runtime_attention_delivery_lease'::regclass
    LOOP
        EXECUTE format(
            'DROP POLICY %I ON public.runtime_attention_delivery_lease', v_policy_name
        );
    END LOOP;
    EXECUTE 'CREATE POLICY runtime_attention_delivery_lease_owner ON public.runtime_attention_delivery_lease '
        || 'FOR ALL TO runtime_attention_outbox_owner USING (true) WITH CHECK (true)';
    EXECUTE 'CREATE POLICY runtime_attention_delivery_lease_switchboard ON public.runtime_attention_delivery_lease '
        || 'FOR ALL TO butler_switchboard_rw '
        || 'USING (public.runtime_attention_active_switchboard_role()) '
        || 'WITH CHECK (public.runtime_attention_active_switchboard_role())';

    -- The trigger guards are as authority-bearing as the RLS policies.  Keep
    -- the relations to exactly the bootstrap-defined guards on every rerun.
    FOR v_trigger_name IN
        SELECT trigger_row.tgname::name
        FROM pg_trigger AS trigger_row
        WHERE trigger_row.tgrelid = 'public.runtime_attention_outbox'::regclass
          AND NOT trigger_row.tgisinternal
    LOOP
        EXECUTE format(
            'DROP TRIGGER %I ON public.runtime_attention_outbox', v_trigger_name
        );
    END LOOP;
    FOR v_trigger_name IN
        SELECT trigger_row.tgname::name
        FROM pg_trigger AS trigger_row
        WHERE trigger_row.tgrelid = 'public.runtime_attention_delivery_lease'::regclass
          AND NOT trigger_row.tgisinternal
    LOOP
        EXECUTE format(
            'DROP TRIGGER %I ON public.runtime_attention_delivery_lease', v_trigger_name
        );
    END LOOP;
    EXECUTE 'CREATE TRIGGER runtime_attention_outbox_guard_trigger '
        || 'BEFORE UPDATE ON public.runtime_attention_outbox '
        || 'FOR EACH ROW EXECUTE FUNCTION public.runtime_attention_outbox_guard()';
    EXECUTE 'CREATE TRIGGER runtime_attention_delivery_lease_guard_trigger '
        || 'BEFORE INSERT OR UPDATE ON public.runtime_attention_delivery_lease '
        || 'FOR EACH ROW EXECUTE FUNCTION public.runtime_attention_delivery_lease_guard()';

    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA public FROM runtime_attention_outbox_owner';
    EXECUTE 'GRANT USAGE ON SCHEMA public TO runtime_attention_outbox_owner';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.model_catalog FROM runtime_attention_outbox_owner';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.model_dispatch_attempts FROM runtime_attention_outbox_owner';
    EXECUTE 'GRANT SELECT ON TABLE public.model_catalog TO runtime_attention_outbox_owner';
    EXECUTE 'GRANT SELECT ON TABLE public.model_dispatch_attempts TO runtime_attention_outbox_owner';

    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_outbox FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_delivery_lease FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_model_breaker(bigint) FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_fleet_halt() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_active_switchboard_role() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_outbox_guard() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_delivery_lease_guard() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON SCHEMA runtime_attention_admin FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE runtime_attention_admin.bootstrap_configuration FROM PUBLIC';

    -- Remove stale direct grants to unlisted roles as well as the known
    -- init-db grants below.  A later bootstrap must not preserve an ad-hoc
    -- principal merely because it was absent from the historical role list.
    FOR v_acl_role IN
        SELECT DISTINCT role_row.rolname::name
        FROM pg_proc AS interface_function
        CROSS JOIN LATERAL aclexplode(
            COALESCE(interface_function.proacl, acldefault('f', interface_function.proowner))
        ) AS acl
        JOIN pg_roles AS role_row ON role_row.oid = acl.grantee
        WHERE interface_function.oid IN (
            'public.append_runtime_attention_model_breaker(bigint)'::regprocedure,
            'public.append_runtime_attention_fleet_halt()'::regprocedure
        )
          AND acl.privilege_type = 'EXECUTE'
          AND role_row.rolname <> ALL (ARRAY[
              'runtime_attention_outbox_owner',
              'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
              'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
              'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
              'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
          ]::name[])
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_model_breaker(bigint) FROM %I',
            v_acl_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_fleet_halt() FROM %I',
            v_acl_role
        );
    END LOOP;
    FOR v_acl_role IN
        SELECT DISTINCT role_row.rolname::name
        FROM pg_class AS relation
        CROSS JOIN LATERAL aclexplode(
            COALESCE(relation.relacl, acldefault('r', relation.relowner))
        ) AS acl
        JOIN pg_roles AS role_row ON role_row.oid = acl.grantee
        WHERE relation.oid IN (
            'public.runtime_attention_outbox'::regclass,
            'public.runtime_attention_delivery_lease'::regclass
        )
          AND role_row.rolname <> ALL (ARRAY[
              'runtime_attention_outbox_owner', 'butler_switchboard_rw'
          ]::name[])
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_outbox FROM %I', v_acl_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_delivery_lease FROM %I',
            v_acl_role
        );
    END LOOP;

    -- Revoke the broad init-db grants from every reachable shared identity,
    -- then grant the two intentionally disjoint interfaces back.  The SET
    -- ROLE gates in the functions/policies make inherited membership alone
    -- insufficient proof of a producer or Switchboard identity.
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
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_outbox FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_delivery_lease FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_model_breaker(bigint) FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_fleet_halt() FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_active_switchboard_role() FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_outbox_guard() FROM %I', v_runtime_role);
            EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_delivery_lease_guard() FROM %I', v_runtime_role);
        END IF;
    END LOOP;
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_outbox FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_delivery_lease FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_model_breaker(bigint) FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_fleet_halt() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_active_switchboard_role() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_outbox_guard() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_delivery_lease_guard() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA runtime_attention_admin FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE runtime_attention_admin.bootstrap_configuration FROM %I', v_migration_role);
    -- The migration login is handed the installer only while the boundary is
    -- wholly absent.  Once installed, it keeps no access to the admin control
    -- functions; later core-schema runs prove the finalized catalog and no-op.
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.finalize_interface() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.install_interface() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.rollback_interface() FROM %I', v_migration_role);

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
        'butler_travel_rw'
    ]::name[] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime_role) THEN
            RAISE EXCEPTION 'runtime-attention producer role % is missing', v_runtime_role;
        END IF;
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.append_runtime_attention_model_breaker(bigint) TO %I',
            v_runtime_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.append_runtime_attention_fleet_halt() TO %I',
            v_runtime_role
        );
    END LOOP;

    EXECUTE 'GRANT SELECT, UPDATE (lifecycle_state, claim_token, claim_epoch, delivery_lease_epoch, claimed_by_instance, claimed_at, claim_expires_at, next_attempt_at, delivered_at) '
        || 'ON TABLE public.runtime_attention_outbox TO butler_switchboard_rw';
    EXECUTE 'GRANT SELECT, INSERT, UPDATE ON TABLE public.runtime_attention_delivery_lease TO butler_switchboard_rw';
    EXECUTE 'GRANT EXECUTE ON FUNCTION public.runtime_attention_active_switchboard_role() TO butler_switchboard_rw';

    -- Only the dedicated definer can make an immutable source snapshot; it is
    -- not a generic payload sink.  Reassert the post-finalization shape on
    -- every init-db rerun so broad legacy grants cannot survive bootstrap.
    IF EXISTS (
        SELECT 1
        FROM aclexplode(
            COALESCE(
                (SELECT relacl FROM pg_class WHERE oid = 'public.runtime_attention_outbox'::regclass),
                acldefault('r', v_outbox_owner)
            )
        ) AS acl
        WHERE acl.grantee = 0
          AND acl.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'DELETE')
    ) THEN
        RAISE EXCEPTION 'runtime-attention outbox PUBLIC DML ACL repair failed';
    END IF;
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE runtime_attention_outbox_owner '
        || 'IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE runtime_attention_outbox_owner '
        || 'IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC';

    IF v_interface_version = 1 THEN
        -- core_198 installs the inert v1 boundary.  Preserve exactly one
        -- short-lived path for the immediately following core_199 migration;
        -- upgrade_producers_v2 revokes this grant before it returns.
        EXECUTE format('GRANT USAGE ON SCHEMA runtime_attention_admin TO %I', v_migration_role);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION runtime_attention_admin.upgrade_producers_v2() TO %I',
            v_migration_role
        );
    ELSIF v_interface_version = 2 THEN
        EXECUTE 'ALTER TABLE public.runtime_attention_producer_control '
            || 'OWNER TO runtime_attention_outbox_owner';
        EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE '
            || 'public.runtime_attention_producer_control FROM PUBLIC';
        FOREACH v_runtime_role IN ARRAY ARRAY[
            'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
            'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
            'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw',
            'connector_writer'
        ]::name[] LOOP
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = v_runtime_role) THEN
                EXECUTE format(
                    'REVOKE ALL PRIVILEGES ON TABLE '
                    || 'public.runtime_attention_producer_control FROM %I',
                    v_runtime_role
                );
            END IF;
        END LOOP;
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE '
            || 'public.runtime_attention_producer_control FROM %I',
            v_migration_role
        );
        EXECUTE 'ALTER FUNCTION public.runtime_attention_legacy_producer_fence() '
            || 'OWNER TO runtime_attention_outbox_owner';
        EXECUTE 'ALTER FUNCTION public.runtime_attention_legacy_producer_fence() '
            || 'SET search_path = pg_catalog, public, pg_temp';
        EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION '
            || 'public.runtime_attention_legacy_producer_fence() FROM PUBLIC';
        EXECUTE 'GRANT INSERT ON TABLE public.audit_log TO runtime_attention_outbox_owner';
        EXECUTE 'GRANT USAGE ON SEQUENCE public.audit_log_id_seq '
            || 'TO runtime_attention_outbox_owner';
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION '
            || 'runtime_attention_admin.upgrade_producers_v2() FROM %I',
            v_migration_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON SCHEMA runtime_attention_admin FROM %I',
            v_migration_role
        );
    ELSE
        RAISE EXCEPTION 'unsupported runtime-attention interface version %', v_interface_version;
    END IF;
END;
$runtime_attention_finalizer$;

CREATE OR REPLACE FUNCTION runtime_attention_admin.install_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_installer$
DECLARE
    v_migration_role NAME;
    v_bootstrap_role NAME;
BEGIN
    SELECT migration_role, bootstrap_role
    INTO v_migration_role, v_bootstrap_role
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL OR v_bootstrap_role IS NULL THEN
        RAISE EXCEPTION 'runtime-attention bootstrap configuration is missing';
    END IF;
    IF to_regclass('public.runtime_attention_outbox') IS NOT NULL
       OR to_regclass('public.runtime_attention_delivery_lease') IS NOT NULL
       OR to_regprocedure('public.append_runtime_attention_model_breaker(bigint)') IS NOT NULL
       OR to_regprocedure('public.append_runtime_attention_fleet_halt()') IS NOT NULL
       OR to_regprocedure('public.runtime_attention_active_switchboard_role()') IS NOT NULL
       OR to_regprocedure('public.runtime_attention_outbox_guard()') IS NOT NULL
       OR to_regprocedure('public.runtime_attention_delivery_lease_guard()') IS NOT NULL THEN
        -- The core migration proves a completed final catalog before it can
        -- no-op.  The installer itself accepts only total absence, never a
        -- compatible-looking or partial boundary supplied by another owner.
        RAISE EXCEPTION 'runtime-attention interface must be absent before fixed bootstrap installation';
    END IF;
    IF to_regclass('public.model_catalog') IS NULL
       OR to_regclass('public.model_dispatch_attempts') IS NULL THEN
        RAISE EXCEPTION 'runtime-attention requires canonical model catalog and dispatch attempts';
    END IF;
    IF to_regclass('public.idx_model_dispatch_attempts_catalog_ts_id') IS NOT NULL
       OR to_regclass('public.idx_model_dispatch_attempts_outcome_ts_id') IS NOT NULL THEN
        -- These names are rollback-owned only after this installer creates
        -- them.  Never adopt an operator-created exact match that the bounded
        -- rollback would later destroy.
        RAISE EXCEPTION
            'runtime-attention reserved deterministic index already exists before installation';
    END IF;

    CREATE TABLE public.runtime_attention_outbox (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source TEXT NOT NULL CHECK (source IN ('model_breaker', 'fleet_halt')),
        triggering_attempt_id BIGINT,
        fleet_halt_month DATE,
        source_snapshot JSONB NOT NULL CHECK (jsonb_typeof(source_snapshot) = 'object'),
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        lifecycle_state TEXT NOT NULL DEFAULT 'pending'
            CHECK (lifecycle_state IN ('pending', 'sending', 'sent', 'failed', 'uncertain')),
        claim_token UUID,
        claim_epoch BIGINT NOT NULL DEFAULT 0 CHECK (claim_epoch >= 0),
        delivery_lease_epoch BIGINT,
        claimed_by_instance TEXT,
        claimed_at TIMESTAMPTZ,
        claim_expires_at TIMESTAMPTZ,
        next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        delivered_at TIMESTAMPTZ,
        -- Dormant worker-stage evidence: only fixed, non-secret codes can
        -- ever be persisted.  Core_198 does not grant these fields to any
        -- runtime role or activate a producer/worker that writes them.
        delivery_error_class TEXT,
        delivery_error_detail TEXT,
        -- Optional scalar linkage only.  Do not add a switchboard-schema FK:
        -- core-only databases must install this boundary before that schema.
        notification_ref UUID,
        retention_until TIMESTAMPTZ NOT NULL DEFAULT (now() + interval '90 days'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        manual_reissue_of UUID,
        CONSTRAINT ck_runtime_attention_outbox_source_edge CHECK (
            (manual_reissue_of IS NULL AND source = 'model_breaker'
                AND triggering_attempt_id IS NOT NULL AND fleet_halt_month IS NULL)
            OR (manual_reissue_of IS NULL AND source = 'fleet_halt'
                AND triggering_attempt_id IS NULL AND fleet_halt_month IS NOT NULL)
            OR (manual_reissue_of IS NOT NULL
                AND triggering_attempt_id IS NULL AND fleet_halt_month IS NULL)
        ),
        -- Safe episode fields are an explicit fixed projection.  In
        -- particular, no failure_reason, error_message, provider response, or
        -- caller-supplied free-form text can enter this durable surface.
        CONSTRAINT ck_runtime_attention_outbox_snapshot_allowlist CHECK (
            (manual_reissue_of IS NULL AND source = 'model_breaker'
                AND source_snapshot ?& ARRAY[
                    'catalog_entry_id', 'alias', 'model_id',
                    'triggering_attempt_id', 'consecutive_failures'
                ]
                AND source_snapshot - ARRAY[
                    'catalog_entry_id', 'alias', 'model_id',
                    'triggering_attempt_id', 'consecutive_failures'
                ] = '{}'::jsonb)
            OR (manual_reissue_of IS NULL AND source = 'fleet_halt'
                AND source_snapshot ?& ARRAY['month', 'denied_count', 'first_denied_at']
                AND source_snapshot - ARRAY['month', 'denied_count', 'first_denied_at']
                    = '{}'::jsonb)
            OR (manual_reissue_of IS NOT NULL
                AND source_snapshot ? 'reissue_of'
                AND source_snapshot - ARRAY['reissue_of'] = '{}'::jsonb)
        ),
        CONSTRAINT ck_runtime_attention_outbox_payload_allowlist CHECK (
            (manual_reissue_of IS NULL AND source = 'model_breaker'
                AND payload ?& ARRAY['classification', 'consecutive_failures', 'door']
                AND payload - ARRAY['classification', 'consecutive_failures', 'door'] = '{}'::jsonb)
            OR (manual_reissue_of IS NULL AND source = 'fleet_halt'
                AND payload ?& ARRAY['classification', 'door']
                AND payload - ARRAY['classification', 'door'] = '{}'::jsonb)
            OR (manual_reissue_of IS NOT NULL
                AND payload ? 'classification'
                AND payload - ARRAY['classification'] = '{}'::jsonb)
        ),
        CONSTRAINT ck_runtime_attention_outbox_retention CHECK (retention_until >= created_at),
        CONSTRAINT ck_runtime_attention_outbox_claim_shape CHECK (
            (lifecycle_state = 'pending' AND claim_token IS NULL
                AND delivery_lease_epoch IS NULL AND claimed_by_instance IS NULL
                AND claimed_at IS NULL AND claim_expires_at IS NULL)
            OR (lifecycle_state IN ('sending', 'sent', 'failed', 'uncertain')
                AND claim_token IS NOT NULL AND delivery_lease_epoch > 0
                AND claimed_by_instance IS NOT NULL AND claimed_at IS NOT NULL
                AND claim_expires_at IS NOT NULL)
        ),
        CONSTRAINT ck_runtime_attention_outbox_claim_expiry CHECK (
            claim_expires_at IS NULL
                OR (claimed_at IS NOT NULL AND claim_expires_at > claimed_at)
        ),
        CONSTRAINT ck_runtime_attention_outbox_delivery_shape CHECK (
            (lifecycle_state = 'sent' AND delivered_at IS NOT NULL)
            OR (lifecycle_state <> 'sent' AND delivered_at IS NULL)
        ),
        CONSTRAINT ck_runtime_attention_outbox_delivery_evidence CHECK (
            (
                (
                    delivery_error_class IS NULL
                    AND delivery_error_detail IS NULL
                )
                OR (
                    lifecycle_state IN ('failed', 'uncertain')
                    AND delivery_error_class IS NOT NULL
                    AND delivery_error_detail IS NOT NULL
                    AND (delivery_error_class, delivery_error_detail) IN (
                        ('pre_transport', 'recipient_unavailable'),
                        ('pre_transport', 'policy_denied'),
                        ('transport_rejected', 'provider_rejected'),
                        ('transport_uncertain', 'transport_timeout'),
                        ('transport_uncertain', 'transport_connection_lost'),
                        ('transport_uncertain', 'worker_recovery')
                    )
                )
            )
            AND (
                notification_ref IS NULL
                OR lifecycle_state IN ('sent', 'failed', 'uncertain')
            )
        )
    );
    CREATE TABLE public.runtime_attention_delivery_lease (
        lease_name TEXT PRIMARY KEY DEFAULT 'runtime_attention_delivery'
            CHECK (lease_name = 'runtime_attention_delivery'),
        lease_token UUID,
        lease_epoch BIGINT NOT NULL DEFAULT 0 CHECK (lease_epoch >= 0),
        holder_instance TEXT,
        acquired_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT ck_runtime_attention_delivery_lease_shape CHECK (
            (lease_token IS NULL AND holder_instance IS NULL AND acquired_at IS NULL AND expires_at IS NULL)
            OR (lease_token IS NOT NULL AND holder_instance IS NOT NULL
                AND acquired_at IS NOT NULL AND expires_at > acquired_at)
        )
    );
    CREATE UNIQUE INDEX ux_runtime_attention_outbox_model_breaker_attempt
        ON public.runtime_attention_outbox (triggering_attempt_id)
        WHERE source = 'model_breaker' AND triggering_attempt_id IS NOT NULL;
    CREATE UNIQUE INDEX ux_runtime_attention_outbox_fleet_halt_month
        ON public.runtime_attention_outbox (fleet_halt_month)
        WHERE source = 'fleet_halt' AND fleet_halt_month IS NOT NULL;
    CREATE UNIQUE INDEX ux_runtime_attention_outbox_manual_reissue
        ON public.runtime_attention_outbox (manual_reissue_of)
        WHERE manual_reissue_of IS NOT NULL;
    CREATE INDEX idx_runtime_attention_outbox_pending_order
        ON public.runtime_attention_outbox (next_attempt_at ASC, created_at ASC, id ASC)
        WHERE lifecycle_state = 'pending';
    CREATE INDEX idx_runtime_attention_outbox_retention
        ON public.runtime_attention_outbox (retention_until ASC, id ASC);
    CREATE INDEX idx_model_dispatch_attempts_catalog_ts_id
        ON public.model_dispatch_attempts (catalog_entry_id, ts DESC, id DESC);
    CREATE INDEX idx_model_dispatch_attempts_outcome_ts_id
        ON public.model_dispatch_attempts (outcome, ts DESC, id DESC);

    CREATE FUNCTION public.runtime_attention_active_switchboard_role()
    RETURNS boolean
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_switchboard_role$
    BEGIN
        IF current_setting('role', true) IS DISTINCT FROM 'butler_switchboard_rw' THEN
            RAISE EXCEPTION 'runtime-attention direct access requires SET ROLE butler_switchboard_rw'
                USING ERRCODE = '42501';
        END IF;
        RETURN true;
    END;
    $runtime_attention_switchboard_role$;

    CREATE FUNCTION public.runtime_attention_outbox_guard()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_guard$
    BEGIN
        IF TG_OP = 'UPDATE' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.source IS DISTINCT FROM OLD.source
               OR NEW.triggering_attempt_id IS DISTINCT FROM OLD.triggering_attempt_id
               OR NEW.fleet_halt_month IS DISTINCT FROM OLD.fleet_halt_month
               OR NEW.source_snapshot IS DISTINCT FROM OLD.source_snapshot
               OR NEW.payload IS DISTINCT FROM OLD.payload
               OR NEW.manual_reissue_of IS DISTINCT FROM OLD.manual_reissue_of
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.retention_until IS DISTINCT FROM OLD.retention_until THEN
                RAISE EXCEPTION 'runtime-attention source snapshots and retention are immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.claim_epoch < OLD.claim_epoch THEN
                RAISE EXCEPTION 'runtime-attention claim epoch may not move backwards'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.lifecycle_state = 'pending' THEN
                IF NEW.lifecycle_state = 'pending' THEN
                    IF NEW.claim_epoch IS DISTINCT FROM OLD.claim_epoch THEN
                        RAISE EXCEPTION
                            'runtime-attention claim epoch may only advance with a fresh sending claim'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.lifecycle_state = 'sending' THEN
                    IF NEW.claim_token IS NULL OR NEW.delivery_lease_epoch IS NULL
                       OR NEW.delivery_lease_epoch <= 0 OR NEW.claimed_by_instance IS NULL
                       OR NEW.claimed_at IS NULL OR NEW.claim_expires_at IS NULL
                       OR NEW.claim_epoch <> OLD.claim_epoch + 1 THEN
                        RAISE EXCEPTION
                            'runtime-attention sending transition requires a fresh fenced claim'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    RAISE EXCEPTION
                        'runtime-attention terminal transition requires a fenced sending claim'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.lifecycle_state = 'sending' THEN
                IF NEW.lifecycle_state = 'pending' THEN
                    IF NEW.claim_epoch IS DISTINCT FROM OLD.claim_epoch
                       OR NEW.claim_token IS NOT NULL
                       OR NEW.delivery_lease_epoch IS NOT NULL
                       OR NEW.claimed_by_instance IS NOT NULL
                       OR NEW.claimed_at IS NOT NULL
                       OR NEW.claim_expires_at IS NOT NULL THEN
                        RAISE EXCEPTION
                            'runtime-attention retry must clear, but not replace, its fenced claim'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF NEW.lifecycle_state IN ('sending', 'sent', 'failed', 'uncertain') THEN
                    IF NEW.claim_token IS DISTINCT FROM OLD.claim_token
                       OR NEW.claim_epoch IS DISTINCT FROM OLD.claim_epoch
                       OR NEW.delivery_lease_epoch IS DISTINCT FROM OLD.delivery_lease_epoch
                       OR NEW.claimed_by_instance IS DISTINCT FROM OLD.claimed_by_instance
                       OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                       OR (
                           NEW.lifecycle_state <> 'sending'
                           AND NEW.claim_expires_at IS DISTINCT FROM OLD.claim_expires_at
                       ) THEN
                        RAISE EXCEPTION
                            'runtime-attention fenced claim identity is immutable while sending'
                            USING ERRCODE = '23514';
                    END IF;
                END IF;
            ELSE
                IF NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state THEN
                    RAISE EXCEPTION 'runtime-attention terminal lifecycle state is immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.claim_token IS DISTINCT FROM OLD.claim_token
                   OR NEW.claim_epoch IS DISTINCT FROM OLD.claim_epoch
                   OR NEW.delivery_lease_epoch IS DISTINCT FROM OLD.delivery_lease_epoch
                   OR NEW.claimed_by_instance IS DISTINCT FROM OLD.claimed_by_instance
                   OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
                   OR NEW.claim_expires_at IS DISTINCT FROM OLD.claim_expires_at
                   OR NEW.delivered_at IS DISTINCT FROM OLD.delivered_at
                   OR NEW.delivery_error_class IS DISTINCT FROM OLD.delivery_error_class
                   OR NEW.delivery_error_detail IS DISTINCT FROM OLD.delivery_error_detail
                   OR NEW.notification_ref IS DISTINCT FROM OLD.notification_ref THEN
                    RAISE EXCEPTION 'runtime-attention terminal fence is immutable'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            NEW.updated_at := now();
        END IF;
        RETURN NEW;
    END;
    $runtime_attention_guard$;

    CREATE FUNCTION public.runtime_attention_delivery_lease_guard()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_lease_guard$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.lease_token IS NULL AND NEW.lease_epoch <> 0 THEN
                RAISE EXCEPTION
                    'runtime-attention idle delivery lease must begin at epoch zero'
                    USING ERRCODE = '23514';
            ELSIF NEW.lease_token IS NOT NULL AND NEW.lease_epoch <> 1 THEN
                RAISE EXCEPTION
                    'runtime-attention lease acquisition must advance from epoch zero to one'
                    USING ERRCODE = '23514';
            END IF;
        ELSIF TG_OP = 'UPDATE' THEN
            IF NEW.lease_name IS DISTINCT FROM OLD.lease_name THEN
                RAISE EXCEPTION 'runtime-attention delivery lease name is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.lease_epoch < OLD.lease_epoch THEN
                RAISE EXCEPTION 'runtime-attention lease epoch may not move backwards'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.lease_token IS NULL THEN
                IF NEW.lease_token IS NULL AND NEW.lease_epoch <> OLD.lease_epoch THEN
                    RAISE EXCEPTION
                        'runtime-attention idle delivery lease epoch may not advance'
                        USING ERRCODE = '23514';
                ELSIF NEW.lease_token IS NOT NULL
                   AND NEW.lease_epoch <> OLD.lease_epoch + 1 THEN
                    RAISE EXCEPTION
                        'runtime-attention lease acquisition must advance exactly one epoch'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.lease_token IS NULL THEN
                IF NEW.lease_epoch <> OLD.lease_epoch THEN
                    RAISE EXCEPTION
                        'runtime-attention delivery lease release must preserve its fence epoch'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.lease_token IS DISTINCT FROM OLD.lease_token THEN
                IF NEW.lease_epoch <> OLD.lease_epoch + 1 THEN
                    RAISE EXCEPTION
                        'runtime-attention lease acquisition must advance exactly one epoch'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.lease_epoch IS DISTINCT FROM OLD.lease_epoch
               OR NEW.holder_instance IS DISTINCT FROM OLD.holder_instance
               OR NEW.acquired_at IS DISTINCT FROM OLD.acquired_at THEN
                RAISE EXCEPTION
                    'runtime-attention active delivery lease identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            NEW.updated_at := now();
        END IF;
        RETURN NEW;
    END;
    $runtime_attention_lease_guard$;

    CREATE FUNCTION public.append_runtime_attention_model_breaker(p_triggering_attempt_id BIGINT)
    RETURNS UUID
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_model_breaker$
    DECLARE
        v_catalog_entry_id UUID;
        v_alias TEXT;
        v_model_id TEXT;
        v_trigger_ts TIMESTAMPTZ;
        v_count INTEGER;
        v_all_failures BOOLEAN;
        v_latest_ts TIMESTAMPTZ;
        v_before_count INTEGER;
        v_before_all_failures BOOLEAN;
        v_before_latest_ts TIMESTAMPTZ;
        v_episode_id UUID;
    BEGIN
        IF COALESCE(current_setting('role', true), '') <> ALL (ARRAY[
            'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
            'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
            'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
        ]) THEN
            RAISE EXCEPTION 'runtime-attention producer requires an active canonical SET ROLE'
                USING ERRCODE = '42501';
        END IF;

        SELECT attempt.catalog_entry_id, catalog.alias, catalog.model_id, attempt.ts
        INTO v_catalog_entry_id, v_alias, v_model_id, v_trigger_ts
        FROM public.model_dispatch_attempts AS attempt
        JOIN public.model_catalog AS catalog ON catalog.id = attempt.catalog_entry_id
        WHERE attempt.id = p_triggering_attempt_id
          AND attempt.outcome = 'runtime_failure';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime-attention model-breaker trigger must be a catalog-backed runtime failure'
                USING ERRCODE = '23514';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(v_catalog_entry_id::text, 0));

        -- Derive the transition at this exact (ts, id) edge, rather than
        -- requiring it to remain the latest row at call time.  That makes
        -- concurrent half-open failures deterministic: the lowest ordered
        -- newly-open edge records once; later same-timestamp rows observe it
        -- as already open and cannot manufacture another episode.
        SELECT count(*)::integer, bool_and(outcome = 'runtime_failure'), max(ts)
        INTO v_count, v_all_failures, v_latest_ts
        FROM (
            SELECT outcome, ts
            FROM public.model_dispatch_attempts
            WHERE catalog_entry_id = v_catalog_entry_id
              AND outcome IN ('runtime_failure', 'success')
              AND (ts, id) <= (v_trigger_ts, p_triggering_attempt_id)
            ORDER BY ts DESC, id DESC
            LIMIT 5
        ) AS recent;
        IF v_count < 5 OR NOT COALESCE(v_all_failures, false)
           OR now() - v_latest_ts >= interval '15 minutes' THEN
            RAISE EXCEPTION 'runtime-attention model-breaker trigger is not an open breaker edge'
                USING ERRCODE = '23514';
        END IF;

        SELECT count(*)::integer, bool_and(outcome = 'runtime_failure'), max(ts)
        INTO v_before_count, v_before_all_failures, v_before_latest_ts
        FROM (
            SELECT outcome, ts
            FROM public.model_dispatch_attempts
            WHERE catalog_entry_id = v_catalog_entry_id
              AND outcome IN ('runtime_failure', 'success')
              AND (ts, id) < (v_trigger_ts, p_triggering_attempt_id)
            ORDER BY ts DESC, id DESC
            LIMIT 5
        ) AS preceding;
        IF v_before_count >= 5 AND COALESCE(v_before_all_failures, false)
           AND now() - v_before_latest_ts < interval '15 minutes' THEN
            RAISE EXCEPTION 'runtime-attention model-breaker edge was already open'
                USING ERRCODE = '23514';
        END IF;

        INSERT INTO public.runtime_attention_outbox (
            source, triggering_attempt_id, source_snapshot, payload
        )
        VALUES (
            'model_breaker',
            p_triggering_attempt_id,
            jsonb_build_object(
                'catalog_entry_id', v_catalog_entry_id::text,
                'alias', v_alias,
                'model_id', v_model_id,
                'triggering_attempt_id', p_triggering_attempt_id,
                'consecutive_failures', 5
            ),
            jsonb_build_object(
                'classification', 'model_breaker_open',
                'consecutive_failures', 5,
                'door', '/settings/models?highlight=' || v_catalog_entry_id::text
            )
        )
        ON CONFLICT (triggering_attempt_id)
            WHERE source = 'model_breaker' AND triggering_attempt_id IS NOT NULL
            DO NOTHING
        RETURNING id INTO v_episode_id;
        IF v_episode_id IS NULL THEN
            SELECT id INTO v_episode_id
            FROM public.runtime_attention_outbox
            WHERE source = 'model_breaker'
              AND triggering_attempt_id = p_triggering_attempt_id;
        END IF;
        RETURN v_episode_id;
    END;
    $runtime_attention_model_breaker$;

    CREATE FUNCTION public.append_runtime_attention_fleet_halt()
    RETURNS UUID
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_fleet_halt$
    DECLARE
        v_month DATE := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
        v_denied_count INTEGER;
        v_first_denied_at TIMESTAMPTZ;
        v_episode_id UUID;
    BEGIN
        IF COALESCE(current_setting('role', true), '') <> ALL (ARRAY[
            'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
            'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
            'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
        ]) THEN
            RAISE EXCEPTION 'runtime-attention producer requires an active canonical SET ROLE'
                USING ERRCODE = '42501';
        END IF;
        SELECT count(*)::integer, min(ts)
        INTO v_denied_count, v_first_denied_at
        FROM public.model_dispatch_attempts
        WHERE outcome = 'quota_skip'
          AND left(COALESCE(failure_reason, ''), length('Monthly spend ceiling reached'))
                = 'Monthly spend ceiling reached'
          AND date_trunc('month', ts AT TIME ZONE 'UTC')::date = v_month;
        IF v_denied_count < 1 THEN
            RAISE EXCEPTION 'runtime-attention fleet-halt trigger lacks current-month ceiling evidence'
                USING ERRCODE = '23514';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('runtime_attention_fleet_halt:' || v_month::text, 0));
        INSERT INTO public.runtime_attention_outbox (
            source, fleet_halt_month, source_snapshot, payload
        )
        VALUES (
            'fleet_halt',
            v_month,
            jsonb_build_object(
                'month', v_month::text,
                'denied_count', v_denied_count,
                'first_denied_at', v_first_denied_at
            ),
            jsonb_build_object(
                'classification', 'monthly_spend_ceiling',
                'door', '/spend?outcome=quota_skip'
            )
        )
        ON CONFLICT (fleet_halt_month)
            WHERE source = 'fleet_halt' AND fleet_halt_month IS NOT NULL
            DO NOTHING
        RETURNING id INTO v_episode_id;
        IF v_episode_id IS NULL THEN
            SELECT id INTO v_episode_id
            FROM public.runtime_attention_outbox
            WHERE source = 'fleet_halt' AND fleet_halt_month = v_month;
        END IF;
        RETURN v_episode_id;
    END;
    $runtime_attention_fleet_halt$;

    PERFORM runtime_attention_admin.finalize_interface();
END;
$runtime_attention_installer$;

CREATE OR REPLACE FUNCTION runtime_attention_admin.upgrade_producers_v2()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_upgrade_v2$
DECLARE
    v_migration_role NAME;
    v_bootstrap_role NAME;
    v_interface_version INTEGER;
BEGIN
    SELECT migration_role, bootstrap_role, interface_version
    INTO v_migration_role, v_bootstrap_role, v_interface_version
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL OR v_bootstrap_role IS NULL
       OR v_interface_version NOT IN (1, 2) THEN
        RAISE EXCEPTION 'runtime-attention v2 upgrade requires a finalized supported interface';
    END IF;
    IF current_user <> v_bootstrap_role AND session_user <> v_migration_role THEN
        RAISE EXCEPTION 'runtime-attention v2 upgrade requires its configured migration role';
    END IF;
    IF to_regclass('public.runtime_attention_outbox') IS NULL
       OR to_regprocedure('public.append_runtime_attention_model_breaker(bigint)') IS NULL
       OR to_regprocedure('public.append_runtime_attention_fleet_halt()') IS NULL THEN
        RAISE EXCEPTION 'runtime-attention v2 upgrade requires the complete finalized v1 interface';
    END IF;
    IF v_interface_version = 1
       AND to_regclass('public.runtime_attention_producer_control') IS NOT NULL THEN
        RAISE EXCEPTION 'runtime-attention v2 reserved producer control already exists';
    END IF;

    CREATE TABLE IF NOT EXISTS public.runtime_attention_producer_control (
        singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
        interface_version INTEGER NOT NULL CHECK (interface_version = 2),
        producers_enabled BOOLEAN NOT NULL,
        producer_activated_at TIMESTAMPTZ NOT NULL
    );
    INSERT INTO public.runtime_attention_producer_control (
        singleton, interface_version, producers_enabled, producer_activated_at
    )
    VALUES (true, 2, true, clock_timestamp())
    ON CONFLICT (singleton) DO UPDATE SET
        interface_version = 2,
        producers_enabled = true,
        producer_activated_at = EXCLUDED.producer_activated_at;

    CREATE OR REPLACE FUNCTION public.append_runtime_attention_model_breaker(
        p_triggering_attempt_id BIGINT
    )
    RETURNS UUID
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_model_breaker_v2$
    DECLARE
        v_catalog_entry_id UUID;
        v_alias TEXT;
        v_model_id TEXT;
        v_trigger_ts TIMESTAMPTZ;
        v_count INTEGER;
        v_all_failures BOOLEAN;
        v_latest_ts TIMESTAMPTZ;
        v_before_count INTEGER;
        v_before_all_failures BOOLEAN;
        v_before_latest_ts TIMESTAMPTZ;
        v_episode_id UUID;
        v_enabled BOOLEAN;
    BEGIN
        IF COALESCE(current_setting('role', true), '') <> ALL (ARRAY[
            'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
            'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
            'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
        ]) THEN
            RAISE EXCEPTION 'runtime-attention producer requires an active canonical SET ROLE'
                USING ERRCODE = '42501';
        END IF;
        SELECT producers_enabled INTO v_enabled
        FROM public.runtime_attention_producer_control
        WHERE singleton;
        IF NOT COALESCE(v_enabled, false) THEN
            RETURN NULL;
        END IF;

        SELECT attempt.catalog_entry_id, catalog.alias, catalog.model_id, attempt.ts
        INTO v_catalog_entry_id, v_alias, v_model_id, v_trigger_ts
        FROM public.model_dispatch_attempts AS attempt
        JOIN public.model_catalog AS catalog ON catalog.id = attempt.catalog_entry_id
        WHERE attempt.id = p_triggering_attempt_id
          AND attempt.outcome = 'runtime_failure';
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime-attention model-breaker trigger must be a catalog-backed runtime failure'
                USING ERRCODE = '23514';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(v_catalog_entry_id::text, 0));

        SELECT count(*)::integer, bool_and(outcome = 'runtime_failure'), max(ts)
        INTO v_count, v_all_failures, v_latest_ts
        FROM (
            SELECT outcome, ts
            FROM public.model_dispatch_attempts
            WHERE catalog_entry_id = v_catalog_entry_id
              AND outcome IN ('runtime_failure', 'success')
              AND (ts, id) <= (v_trigger_ts, p_triggering_attempt_id)
            ORDER BY ts DESC, id DESC
            LIMIT 5
        ) AS recent;
        IF v_count < 5 OR NOT COALESCE(v_all_failures, false)
           OR clock_timestamp() - v_latest_ts >= interval '15 minutes' THEN
            RAISE EXCEPTION 'runtime-attention model-breaker trigger is not an open breaker edge'
                USING ERRCODE = '23514';
        END IF;

        SELECT count(*)::integer, bool_and(outcome = 'runtime_failure'), max(ts)
        INTO v_before_count, v_before_all_failures, v_before_latest_ts
        FROM (
            SELECT outcome, ts
            FROM public.model_dispatch_attempts
            WHERE catalog_entry_id = v_catalog_entry_id
              AND outcome IN ('runtime_failure', 'success')
              AND (ts, id) < (v_trigger_ts, p_triggering_attempt_id)
            ORDER BY ts DESC, id DESC
            LIMIT 5
        ) AS preceding;
        IF v_before_count >= 5 AND COALESCE(v_before_all_failures, false)
           AND clock_timestamp() - v_before_latest_ts < interval '15 minutes' THEN
            RAISE EXCEPTION 'runtime-attention model-breaker edge was already open'
                USING ERRCODE = '23514';
        END IF;

        INSERT INTO public.runtime_attention_outbox (
            source, triggering_attempt_id, source_snapshot, payload
        )
        VALUES (
            'model_breaker',
            p_triggering_attempt_id,
            jsonb_build_object(
                'catalog_entry_id', v_catalog_entry_id::text,
                'alias', v_alias,
                'model_id', v_model_id,
                'triggering_attempt_id', p_triggering_attempt_id,
                'consecutive_failures', 5
            ),
            jsonb_build_object(
                'classification', 'model_breaker_open',
                'consecutive_failures', 5,
                'door', '/settings/models?highlight=' || v_catalog_entry_id::text
            )
        )
        ON CONFLICT (triggering_attempt_id)
            WHERE source = 'model_breaker' AND triggering_attempt_id IS NOT NULL
            DO NOTHING
        RETURNING id INTO v_episode_id;
        IF v_episode_id IS NULL THEN
            SELECT id INTO v_episode_id
            FROM public.runtime_attention_outbox
            WHERE source = 'model_breaker'
              AND triggering_attempt_id = p_triggering_attempt_id;
        END IF;
        RETURN v_episode_id;
    END;
    $runtime_attention_model_breaker_v2$;

    CREATE OR REPLACE FUNCTION public.append_runtime_attention_fleet_halt()
    RETURNS UUID
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_fleet_halt_v2$
    DECLARE
        v_month DATE := date_trunc('month', clock_timestamp() AT TIME ZONE 'UTC')::date;
        v_denied_count INTEGER;
        v_first_denied_at TIMESTAMPTZ;
        v_episode_id UUID;
        v_enabled BOOLEAN;
        v_activated_at TIMESTAMPTZ;
    BEGIN
        IF COALESCE(current_setting('role', true), '') <> ALL (ARRAY[
            'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
            'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
            'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
        ]) THEN
            RAISE EXCEPTION 'runtime-attention producer requires an active canonical SET ROLE'
                USING ERRCODE = '42501';
        END IF;
        SELECT producers_enabled, producer_activated_at
        INTO v_enabled, v_activated_at
        FROM public.runtime_attention_producer_control
        WHERE singleton;
        IF NOT COALESCE(v_enabled, false) THEN
            RETURN NULL;
        END IF;

        SELECT count(*)::integer, min(ts)
        INTO v_denied_count, v_first_denied_at
        FROM public.model_dispatch_attempts
        WHERE outcome = 'quota_skip'
          AND left(COALESCE(failure_reason, ''), length('Monthly spend ceiling reached'))
                = 'Monthly spend ceiling reached'
          AND date_trunc('month', ts AT TIME ZONE 'UTC')::date = v_month;
        IF v_denied_count < 1 THEN
            RAISE EXCEPTION 'runtime-attention fleet-halt trigger lacks current-month ceiling evidence'
                USING ERRCODE = '23514';
        END IF;
        -- A month already breached before activation remains dashboard-only;
        -- later denials in that same window must not manufacture a rollout page.
        IF v_activated_at IS NULL OR v_first_denied_at < v_activated_at THEN
            RETURN NULL;
        END IF;
        PERFORM pg_advisory_xact_lock(
            hashtextextended('runtime_attention_fleet_halt:' || v_month::text, 0)
        );
        INSERT INTO public.runtime_attention_outbox (
            source, fleet_halt_month, source_snapshot, payload
        )
        VALUES (
            'fleet_halt',
            v_month,
            jsonb_build_object(
                'month', v_month::text,
                'denied_count', v_denied_count,
                'first_denied_at', v_first_denied_at
            ),
            jsonb_build_object(
                'classification', 'monthly_spend_ceiling',
                'door', '/spend?openDrawer=fleet-halt'
            )
        )
        ON CONFLICT (fleet_halt_month)
            WHERE source = 'fleet_halt' AND fleet_halt_month IS NOT NULL
            DO NOTHING
        RETURNING id INTO v_episode_id;
        IF v_episode_id IS NULL THEN
            SELECT id INTO v_episode_id
            FROM public.runtime_attention_outbox
            WHERE source = 'fleet_halt' AND fleet_halt_month = v_month;
        END IF;
        RETURN v_episode_id;
    END;
    $runtime_attention_fleet_halt_v2$;

    CREATE OR REPLACE FUNCTION public.runtime_attention_legacy_producer_fence()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, public, pg_temp
    AS $runtime_attention_legacy_fence_v2$
    DECLARE
        v_active_role TEXT := COALESCE(current_setting('role', true), '');
    BEGIN
        IF v_active_role = ANY (ARRAY[
            'butler_chronicler_rw', 'butler_education_rw', 'butler_finance_rw',
            'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw',
            'butler_relationship_rw', 'butler_switchboard_rw', 'butler_travel_rw'
        ]) AND COALESCE(
            current_setting('butlers.runtime_attention_producer_abi', true), ''
        ) <> '2' THEN
            IF NEW.outcome = 'runtime_failure' THEN
                INSERT INTO public.audit_log (actor, action, target, note)
                VALUES (
                    'runtime_attention_cutover_fence',
                    'model_breaker_open_notified',
                    'model_breaker:' || NEW.catalog_entry_id::text,
                    'blocked_old_binary'
                );
            ELSIF NEW.outcome = 'quota_skip'
                  AND left(
                      COALESCE(NEW.failure_reason, ''),
                      length('Monthly spend ceiling reached')
                  ) = 'Monthly spend ceiling reached' THEN
                INSERT INTO public.audit_log (actor, action, target, note)
                VALUES (
                    'runtime_attention_cutover_fence',
                    'ceiling_halt_notified',
                    'ceiling_halt',
                    to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM')
                );
            END IF;
        END IF;
        RETURN NEW;
    END;
    $runtime_attention_legacy_fence_v2$;

    DROP TRIGGER IF EXISTS runtime_attention_legacy_producer_fence_trigger
        ON public.model_dispatch_attempts;
    CREATE TRIGGER runtime_attention_legacy_producer_fence_trigger
        BEFORE INSERT ON public.model_dispatch_attempts
        FOR EACH ROW EXECUTE FUNCTION public.runtime_attention_legacy_producer_fence();

    UPDATE runtime_attention_admin.bootstrap_configuration
    SET interface_version = 2,
        producers_enabled = true,
        producer_activated_at = clock_timestamp()
    WHERE singleton;
    PERFORM runtime_attention_admin.finalize_interface();
END;
$runtime_attention_upgrade_v2$;

CREATE OR REPLACE FUNCTION runtime_attention_admin.deactivate_producers_v2()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_deactivate_v2$
DECLARE
    v_migration_role NAME;
    v_interface_version INTEGER;
BEGIN
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false) THEN
        RAISE EXCEPTION 'core_199 downgrade requires the managed privileged bootstrap owner';
    END IF;
    SELECT migration_role, interface_version
    INTO v_migration_role, v_interface_version
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL OR v_interface_version <> 2 THEN
        RAISE EXCEPTION 'core_199 downgrade requires the complete v2 producer interface';
    END IF;
    UPDATE runtime_attention_admin.bootstrap_configuration
    SET producers_enabled = false
    WHERE singleton;
    UPDATE public.runtime_attention_producer_control
    SET producers_enabled = false
    WHERE singleton;
    -- Keep the legacy fence and v2 functions in place.  A later re-upgrade
    -- receives only this one-shot versioned activation path.
    EXECUTE format('GRANT USAGE ON SCHEMA runtime_attention_admin TO %I', v_migration_role);
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION runtime_attention_admin.upgrade_producers_v2() TO %I',
        v_migration_role
    );
END;
$runtime_attention_deactivate_v2$;

CREATE OR REPLACE FUNCTION runtime_attention_admin.rollback_interface()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_rollback$
DECLARE
    v_migration_role NAME;
    v_session_is_superuser BOOLEAN;
BEGIN
    SELECT rolsuper INTO v_session_is_superuser
    FROM pg_roles
    WHERE rolname = session_user;
    IF NOT COALESCE(v_session_is_superuser, false) THEN
        RAISE EXCEPTION 'core_198 downgrade requires the managed privileged bootstrap owner';
    END IF;
    SELECT migration_role INTO v_migration_role
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL
       OR to_regclass('public.runtime_attention_outbox') IS NULL
       OR to_regclass('public.runtime_attention_delivery_lease') IS NULL THEN
        RAISE EXCEPTION 'core_198 rollback requires the complete trusted outbox interface';
    END IF;
    IF EXISTS (SELECT 1 FROM public.runtime_attention_outbox)
       OR EXISTS (SELECT 1 FROM public.runtime_attention_delivery_lease) THEN
        RAISE EXCEPTION
            'core_198 rollback refuses durable runtime-attention evidence; use forward remediation';
    END IF;
    -- The representation has no active consumer in core_198.  An empty
    -- relation is therefore the only consumer-disabled reversible state.
    LOCK TABLE public.runtime_attention_outbox, public.runtime_attention_delivery_lease IN ACCESS EXCLUSIVE MODE;
    IF EXISTS (SELECT 1 FROM public.runtime_attention_outbox)
       OR EXISTS (SELECT 1 FROM public.runtime_attention_delivery_lease) THEN
        RAISE EXCEPTION
            'core_198 rollback refuses durable runtime-attention evidence; use forward remediation';
    END IF;
    DROP TABLE public.runtime_attention_delivery_lease;
    DROP TABLE public.runtime_attention_outbox;
    DROP FUNCTION public.append_runtime_attention_model_breaker(bigint);
    DROP FUNCTION public.append_runtime_attention_fleet_halt();
    DROP FUNCTION public.runtime_attention_active_switchboard_role();
    DROP FUNCTION public.runtime_attention_outbox_guard();
    DROP FUNCTION public.runtime_attention_delivery_lease_guard();
    DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_catalog_ts_id;
    DROP INDEX IF EXISTS public.idx_model_dispatch_attempts_outcome_ts_id;
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA runtime_attention_admin FROM %I', v_migration_role);
    EXECUTE format('GRANT USAGE ON SCHEMA runtime_attention_admin TO %I', v_migration_role);
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION runtime_attention_admin.install_interface() TO %I',
        v_migration_role
    );
END;
$runtime_attention_rollback$;

REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.finalize_interface() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.install_interface() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.rollback_interface() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.upgrade_producers_v2() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.deactivate_producers_v2() FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''),
        'butlers'
    )::name;
BEGIN
    EXECUTE format('REVOKE ALL PRIVILEGES ON SCHEMA runtime_attention_admin FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON TABLE runtime_attention_admin.bootstrap_configuration FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.finalize_interface() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.install_interface() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.rollback_interface() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.upgrade_producers_v2() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.deactivate_producers_v2() FROM %I', v_migration_role);

    IF to_regclass('public.runtime_attention_outbox') IS NOT NULL
       OR to_regclass('public.runtime_attention_delivery_lease') IS NOT NULL
       OR to_regprocedure('public.append_runtime_attention_model_breaker(bigint)') IS NOT NULL
       OR to_regprocedure('public.append_runtime_attention_fleet_halt()') IS NOT NULL
       OR to_regprocedure('public.runtime_attention_active_switchboard_role()') IS NOT NULL
       OR to_regprocedure('public.runtime_attention_outbox_guard()') IS NOT NULL
       OR to_regprocedure('public.runtime_attention_delivery_lease_guard()') IS NOT NULL THEN
        PERFORM runtime_attention_admin.finalize_interface();
    ELSE
        EXECUTE format('GRANT USAGE ON SCHEMA runtime_attention_admin TO %I', v_migration_role);
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION runtime_attention_admin.install_interface() TO %I',
            v_migration_role
        );
    END IF;
END;
$$;

RESET ROLE;
