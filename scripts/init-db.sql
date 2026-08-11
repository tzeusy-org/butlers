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

-- ── Read-only bootstrap preflight ───────────────────────────────────────────

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

RESET ROLE;
