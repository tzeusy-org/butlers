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
        'concierge',
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
        'concierge',
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
        'butler_concierge_rw',
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
        'butler_concierge_rw',
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
    --   {schema}.calendar_events + {schema}.calendar_sources — required
    --                                      companions for the calendar join
    --   {schema}.calendar_event_entities — optional participant resolution
    --   public.google_accounts          — optional calendar-owner resolution
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
    -- init-db runs before Alembic's specialist core migrations on a fresh
    -- install. Its guarded calendar grants therefore cover existing/legacy
    -- tables and reruns; core_207 is the post-calendar convergence point for
    -- fresh installs and must retain the same explicit allowlist.
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
        -- calendar_events + calendar_sources (required join companions for
        -- CalendarCompletedAdapter's completed-instance projection).
        FOREACH _table IN ARRAY ARRAY[
            'calendar_events',
            'calendar_sources',
            'calendar_event_entities'
        ] LOOP
            IF EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = _schema AND table_name = _table
            ) THEN
                EXECUTE format(
                    'GRANT SELECT ON TABLE %I.%I TO butler_chronicler_rw',
                    _schema,
                    _table
                );
            END IF;
        END LOOP;
        -- public.google_accounts (optional calendar-owner resolution).  Keep
        -- this explicit alongside the calendar surface grants so a bootstrap
        -- does not depend on the broad shared-public role block above.
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'google_accounts'
        ) THEN
            EXECUTE 'GRANT SELECT ON TABLE public.google_accounts TO butler_chronicler_rw';
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

    -- Cost claims are append/lifecycle evidence. Generic public-table bootstrap
    -- grants must not make their current assertion, resolution, or audit rows
    -- deletable. Forced RLS remains the authority fence; these revokes keep the
    -- catalog privileges equally narrow after every bootstrap replay.
    IF to_regclass('public.cost_claims') IS NOT NULL THEN
        FOREACH _role IN ARRAY _all_runtime_roles || ARRAY[_connector_role] LOOP
            EXECUTE format(
                'REVOKE DELETE ON TABLE public.cost_claims, '
                'public.cost_claim_resolutions, public.cost_claim_events FROM %I',
                _role
            );
        END LOOP;
    END IF;

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
        || 'SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION restore_drill_executor.record_result(text, text, text, integer) '
        || 'SET search_path = pg_catalog, pg_temp';
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
        'butler_concierge_rw',
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
    SET search_path = pg_catalog, pg_temp
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
    SET search_path = pg_catalog, pg_temp
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
              'install_private_mutation',
              'rollback_interface'
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

-- Single source of truth for dnd_generation_private.mutate's body.
--
-- mutate is created once by install_interface, which never re-runs on an
-- installed database, so finalize_interface adopts this definition on every
-- init-db rerun (the bu-jxelx runtime-attention precedent).  A fresh bootstrap
-- and an already-installed database therefore cannot drift apart, and the
-- definer's pinned search_path always meets a body that needs nothing outside
-- pg_catalog: the built-in sha256(bytea) replaces pgcrypto's digest(), which
-- lives in public (bu-mzm3su.1).  encode(sha256(x), 'hex') is byte-identical
-- to encode(digest(x, 'sha256'), 'hex'), so stored replay fingerprints stay
-- valid.  CREATE OR REPLACE preserves the function's OID, owner, and ACL.
CREATE OR REPLACE FUNCTION dnd_generation_admin.install_private_mutation()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $dnd_install_private_mutation$
BEGIN
    CREATE OR REPLACE FUNCTION dnd_generation_private.mutate(
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
    SET search_path = pg_catalog, pg_temp
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
                sha256(convert_to('dnd-value:null', 'UTF8')), 'hex'
            )
            ELSE encode(
                sha256(
                    convert_to('dnd-value:string:' || v_value_normalized, 'UTF8')
                ),
                'hex'
            )
        END;
        v_metadata_digest := encode(
            sha256(
                convert_to(
                    CASE
                        WHEN p_metadata IS NULL THEN 'dnd-metadata:absent'
                        ELSE 'dnd-metadata:json:'
                            || dnd_generation_private.canonical_json(p_metadata)
                    END,
                    'UTF8'
                )
            ),
            'hex'
        );
        v_fingerprint := encode(
            sha256(
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
                )
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
END;
$dnd_install_private_mutation$;

REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_private_mutation() FROM PUBLIC;

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
    -- Adopt the current private body first: an installed database may still
    -- carry the pgcrypto digest() body, which cannot resolve once public is
    -- off the definer's search_path.
    PERFORM dnd_generation_admin.install_private_mutation();
    EXECUTE 'ALTER FUNCTION dnd_generation_private.mutate(uuid, text, text, timestamptz, text, real, jsonb) '
        || 'SET search_path = pg_catalog, pg_temp';

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
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_private_mutation() FROM PUBLIC';
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
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_private_mutation() FROM %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.rollback_interface() FROM %I',
        v_migration_role
    );

    FOREACH v_runtime_role IN ARRAY ARRAY[
        'butler_chronicler_rw',
        'butler_concierge_rw',
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
                'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_private_mutation() FROM %I',
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
            'dnd_generation_admin.install_private_mutation()'::regprocedure,
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

    -- The private mutation body is defined once, in
    -- dnd_generation_admin.install_private_mutation, which finalize_interface
    -- re-adopts on every init-db rerun.
    PERFORM dnd_generation_admin.install_private_mutation();

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
        'REVOKE ALL PRIVILEGES ON FUNCTION dnd_generation_admin.install_private_mutation() FROM %I',
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

-- Operator observation/reissue is installed only through this privileged,
-- versioned upgrader.  The shared migration/dashboard login receives no raw
-- outbox privilege: it can call the fixed content-blind projections and the
-- one state-checked successor operation only.
CREATE OR REPLACE FUNCTION public.runtime_attention_upgrade_operator_v3()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_operator_v3$
DECLARE
    v_migration_role NAME;
BEGIN
    SELECT migration_role INTO v_migration_role
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL
       OR to_regclass('public.runtime_attention_outbox') IS NULL
       OR to_regclass('public.runtime_attention_delivery_lease') IS NULL THEN
        RAISE EXCEPTION 'runtime-attention operator upgrade requires the finalized outbox';
    END IF;
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false)
       AND session_user <> v_migration_role THEN
        RAISE EXCEPTION 'runtime-attention operator upgrade requires its configured migration role';
    END IF;

    CREATE TABLE IF NOT EXISTS public.runtime_attention_operator_control (
        singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
        interface_version INTEGER NOT NULL CHECK (interface_version = 3),
        reissue_enabled BOOLEAN NOT NULL
    );
    INSERT INTO public.runtime_attention_operator_control (
        singleton, interface_version, reissue_enabled
    ) VALUES (true, 3, true)
    ON CONFLICT (singleton) DO UPDATE SET
        interface_version = 3,
        reissue_enabled = true;

    -- A manual successor is a distinct delivery episode, but its delivery
    -- message still needs the original bounded model-breaker context and
    -- review door.  Keep the immutable allowlist narrow while admitting that
    -- copied context plus the direct-parent lineage marker.
    ALTER TABLE public.runtime_attention_outbox
        DROP CONSTRAINT ck_runtime_attention_outbox_snapshot_allowlist,
        ADD CONSTRAINT ck_runtime_attention_outbox_snapshot_allowlist CHECK (
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
            OR (manual_reissue_of IS NOT NULL AND source = 'model_breaker'
                AND source_snapshot ?& ARRAY[
                    'catalog_entry_id', 'alias', 'model_id', 'triggering_attempt_id',
                    'consecutive_failures', 'reissue_of'
                ]
                AND source_snapshot - ARRAY[
                    'catalog_entry_id', 'alias', 'model_id', 'triggering_attempt_id',
                    'consecutive_failures', 'reissue_of'
                ] = '{}'::jsonb)
        ),
        DROP CONSTRAINT ck_runtime_attention_outbox_payload_allowlist,
        ADD CONSTRAINT ck_runtime_attention_outbox_payload_allowlist CHECK (
            (manual_reissue_of IS NULL AND source = 'model_breaker'
                AND payload ?& ARRAY['classification', 'consecutive_failures', 'door']
                AND payload - ARRAY['classification', 'consecutive_failures', 'door']
                    = '{}'::jsonb)
            OR (manual_reissue_of IS NULL AND source = 'fleet_halt'
                AND payload ?& ARRAY['classification', 'door']
                AND payload - ARRAY['classification', 'door'] = '{}'::jsonb)
            OR (manual_reissue_of IS NOT NULL AND source = 'model_breaker'
                AND payload ?& ARRAY['classification', 'consecutive_failures', 'door']
                AND payload - ARRAY['classification', 'consecutive_failures', 'door']
                    = '{}'::jsonb)
        );

    CREATE OR REPLACE FUNCTION public.observe_runtime_attention_models()
    RETURNS TABLE (
        catalog_entry_id UUID,
        episode_id UUID,
        lifecycle_state TEXT,
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        delivered_at TIMESTAMPTZ,
        delivery_error_class TEXT,
        delivery_error_detail TEXT,
        manual_reissue_of UUID,
        successor_id UUID
    )
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $observe_runtime_attention_models$
    BEGIN
        IF COALESCE(current_setting('role', true), 'none') <> 'none' THEN
            RAISE EXCEPTION 'runtime-attention operator observation forbids SET ROLE'
                USING ERRCODE = '42501';
        END IF;
        RETURN QUERY
        SELECT DISTINCT ON (resolved.catalog_entry_id)
            resolved.catalog_entry_id,
            resolved.episode_id,
            resolved.lifecycle_state,
            resolved.created_at,
            resolved.updated_at,
            resolved.delivered_at,
            resolved.delivery_error_class,
            resolved.delivery_error_detail,
            resolved.manual_reissue_of,
            child.id AS successor_id
        FROM (
            SELECT
                COALESCE(
                    NULLIF(episode.source_snapshot->>'catalog_entry_id', '')::uuid,
                    NULLIF(parent.source_snapshot->>'catalog_entry_id', '')::uuid
                ) AS catalog_entry_id,
                episode.id AS episode_id,
                episode.lifecycle_state,
                episode.created_at,
                episode.updated_at,
                episode.delivered_at,
                episode.delivery_error_class,
                episode.delivery_error_detail,
                episode.manual_reissue_of
            FROM public.runtime_attention_outbox AS episode
            LEFT JOIN public.runtime_attention_outbox AS parent
              ON parent.id = episode.manual_reissue_of
            WHERE episode.source = 'model_breaker'
        ) AS resolved
        LEFT JOIN public.runtime_attention_outbox AS child
          ON child.manual_reissue_of = resolved.episode_id
        WHERE resolved.catalog_entry_id IS NOT NULL
        ORDER BY resolved.catalog_entry_id, resolved.created_at DESC, resolved.episode_id DESC;
    END;
    $observe_runtime_attention_models$;

    CREATE OR REPLACE FUNCTION public.observe_runtime_attention_fleet_halt()
    RETURNS TABLE (
        episode_id UUID,
        lifecycle_state TEXT,
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        delivered_at TIMESTAMPTZ,
        delivery_error_class TEXT,
        delivery_error_detail TEXT
    )
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $observe_runtime_attention_fleet_halt$
    BEGIN
        IF COALESCE(current_setting('role', true), 'none') <> 'none' THEN
            RAISE EXCEPTION 'runtime-attention operator observation forbids SET ROLE'
                USING ERRCODE = '42501';
        END IF;
        RETURN QUERY
        SELECT episode.id, episode.lifecycle_state, episode.created_at,
               episode.updated_at, episode.delivered_at,
               episode.delivery_error_class, episode.delivery_error_detail
        FROM public.runtime_attention_outbox AS episode
        WHERE episode.source = 'fleet_halt'
          AND episode.fleet_halt_month = date_trunc('month', now() AT TIME ZONE 'UTC')::date
        ORDER BY episode.created_at DESC, episode.id DESC
        LIMIT 1;
    END;
    $observe_runtime_attention_fleet_halt$;

    CREATE OR REPLACE FUNCTION public.reissue_runtime_attention_episode(p_original_id UUID)
    RETURNS TABLE (
        original_episode_id UUID,
        successor_episode_id UUID,
        successor_state TEXT,
        created BOOLEAN
    )
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $reissue_runtime_attention_episode$
    DECLARE
        v_original public.runtime_attention_outbox%ROWTYPE;
        v_successor public.runtime_attention_outbox%ROWTYPE;
        v_created BOOLEAN := false;
        v_enabled BOOLEAN;
    BEGIN
        IF COALESCE(current_setting('role', true), 'none') <> 'none' THEN
            RAISE EXCEPTION 'runtime-attention operator reissue forbids SET ROLE'
                USING ERRCODE = '42501';
        END IF;
        SELECT reissue_enabled INTO v_enabled
        FROM public.runtime_attention_operator_control WHERE singleton;
        IF NOT COALESCE(v_enabled, false) THEN
            RAISE EXCEPTION 'runtime-attention manual reissue is disabled'
                USING ERRCODE = '55000';
        END IF;

        PERFORM pg_advisory_xact_lock(hashtextextended(p_original_id::text, 0));
        SELECT * INTO v_original
        FROM public.runtime_attention_outbox
        WHERE id = p_original_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime-attention episode not found' USING ERRCODE = 'P0002';
        END IF;
        SELECT * INTO v_successor
        FROM public.runtime_attention_outbox
        WHERE manual_reissue_of = p_original_id;
        IF FOUND THEN
            RETURN QUERY SELECT p_original_id, v_successor.id,
                                v_successor.lifecycle_state, false;
            RETURN;
        END IF;
        IF v_original.source <> 'model_breaker'
           OR v_original.manual_reissue_of IS NOT NULL
           OR v_original.lifecycle_state <> 'uncertain' THEN
            RAISE EXCEPTION 'runtime-attention episode is not eligible for reissue'
                USING ERRCODE = '55000';
        END IF;
        IF EXISTS (
            SELECT 1 FROM public.runtime_attention_delivery_lease
            WHERE lease_name = 'runtime_attention_delivery'
              AND lease_token IS NOT NULL
              AND expires_at > clock_timestamp()
        ) THEN
            RAISE EXCEPTION 'runtime-attention delivery recovery is still active'
                USING ERRCODE = '55000';
        END IF;

        INSERT INTO public.runtime_attention_outbox (
            source, source_snapshot, payload, manual_reissue_of
        ) VALUES (
            'model_breaker',
            v_original.source_snapshot
                || jsonb_build_object('reissue_of', p_original_id::text),
            v_original.payload,
            p_original_id
        )
        ON CONFLICT (manual_reissue_of) WHERE manual_reissue_of IS NOT NULL DO NOTHING
        RETURNING * INTO v_successor;
        v_created := FOUND;
        IF NOT v_created THEN
            SELECT * INTO v_successor FROM public.runtime_attention_outbox
            WHERE manual_reissue_of = p_original_id;
        END IF;
        RETURN QUERY SELECT p_original_id, v_successor.id,
                            v_successor.lifecycle_state, v_created;
    END;
    $reissue_runtime_attention_episode$;

    ALTER TABLE public.runtime_attention_operator_control
        OWNER TO runtime_attention_outbox_owner;
    ALTER FUNCTION public.observe_runtime_attention_models()
        OWNER TO runtime_attention_outbox_owner;
    ALTER FUNCTION public.observe_runtime_attention_fleet_halt()
        OWNER TO runtime_attention_outbox_owner;
    ALTER FUNCTION public.reissue_runtime_attention_episode(UUID)
        OWNER TO runtime_attention_outbox_owner;
    REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_operator_control FROM PUBLIC;
    REVOKE ALL PRIVILEGES ON FUNCTION public.observe_runtime_attention_models() FROM PUBLIC;
    REVOKE ALL PRIVILEGES ON FUNCTION public.observe_runtime_attention_fleet_halt() FROM PUBLIC;
    REVOKE ALL PRIVILEGES ON FUNCTION public.reissue_runtime_attention_episode(UUID) FROM PUBLIC;
    EXECUTE format(
        'GRANT SELECT ON TABLE public.runtime_attention_operator_control TO %I',
        v_migration_role
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION public.observe_runtime_attention_models() TO %I',
        v_migration_role
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION public.observe_runtime_attention_fleet_halt() TO %I',
        v_migration_role
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION public.reissue_runtime_attention_episode(UUID) TO %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_operator_v3() FROM %I',
        v_migration_role
    );
    -- The upgrader needs bootstrap authority while it creates and finalizes
    -- the v3 interface.  Once that one-shot work succeeds, fence both public
    -- entry points behind the dedicated no-login owner so a backup restore
    -- cannot leave either SECURITY DEFINER function running as the restoring
    -- login.  The finalizer reasserts this on later init-db reruns.
    ALTER FUNCTION public.runtime_attention_deactivate_operator_v3()
        OWNER TO runtime_attention_outbox_owner;
    ALTER FUNCTION public.runtime_attention_upgrade_operator_v3()
        OWNER TO runtime_attention_outbox_owner;
END;
$runtime_attention_operator_v3$;

CREATE OR REPLACE FUNCTION public.runtime_attention_deactivate_operator_v3()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_deactivate_operator_v3$
BEGIN
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false) THEN
        RAISE EXCEPTION 'runtime-attention operator rollback requires bootstrap superuser';
    END IF;
    UPDATE public.runtime_attention_operator_control SET reissue_enabled = false
    WHERE singleton;
END;
$runtime_attention_deactivate_operator_v3$;

REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_operator_v3() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_deactivate_operator_v3() FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers'
    )::name;
BEGIN
    IF to_regprocedure('public.reissue_runtime_attention_episode(uuid)') IS NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.runtime_attention_upgrade_operator_v3() TO %I',
            v_migration_role
        );
    ELSE
        EXECUTE format(
            'GRANT SELECT ON TABLE public.runtime_attention_operator_control TO %I',
            v_migration_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.observe_runtime_attention_models() TO %I',
            v_migration_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.observe_runtime_attention_fleet_halt() TO %I',
            v_migration_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.reissue_runtime_attention_episode(UUID) TO %I',
            v_migration_role
        );
    END IF;
END;
$$;

-- REQ-butler-control-plane-liveness-007: owner attention for the independent
-- fleet-control and QA-patrol-overdue conditions.  Like v3 this is a
-- privileged, versioned upgrader: the migration login only invokes it once,
-- and afterwards holds nothing but the content-blind condition projection.
-- Switchboard's role (the Dashboard observer's role view) is the only
-- producer; the existing fenced worker delivers the rows it appends.
CREATE OR REPLACE FUNCTION public.runtime_attention_upgrade_condition_v4()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_condition_v4$
DECLARE
    v_migration_role NAME;
BEGIN
    SELECT migration_role INTO v_migration_role
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL
       OR to_regclass('public.runtime_attention_outbox') IS NULL
       OR to_regclass('public.runtime_attention_delivery_lease') IS NULL
       OR to_regclass('public.runtime_attention_operator_control') IS NULL
       OR to_regclass('public.infra_conditions') IS NULL THEN
        RAISE EXCEPTION
            'runtime-attention condition upgrade requires the v3 outbox and condition ledger';
    END IF;
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false)
       AND session_user <> v_migration_role THEN
        RAISE EXCEPTION 'runtime-attention condition upgrade requires its configured migration role';
    END IF;

    CREATE TABLE IF NOT EXISTS public.runtime_attention_condition_control (
        singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
        interface_version INTEGER NOT NULL CHECK (interface_version = 4),
        producer_enabled BOOLEAN NOT NULL
    );
    INSERT INTO public.runtime_attention_condition_control (
        singleton, interface_version, producer_enabled
    ) VALUES (true, 4, true)
    ON CONFLICT (singleton) DO UPDATE SET
        interface_version = 4,
        producer_enabled = true;

    -- The condition-side emission marker.  Outbox rows age out after their
    -- retention window; this row does not, so a still-active condition keeps
    -- its emitted episode identity and last verified delivery category and is
    -- never mistaken for an unpaged episode.  No FK: the outbox row it names
    -- may be gone.
    CREATE TABLE IF NOT EXISTS public.runtime_attention_condition_episodes (
        condition_id UUID PRIMARY KEY,
        condition_kind TEXT NOT NULL
            CHECK (condition_kind IN ('fleet_control', 'qa_patrol_overdue')),
        episode_id UUID NOT NULL UNIQUE,
        last_delivery_state TEXT NOT NULL
            CHECK (last_delivery_state IN ('pending', 'sending', 'sent', 'failed', 'uncertain')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    ALTER TABLE public.runtime_attention_outbox
        DROP CONSTRAINT IF EXISTS runtime_attention_outbox_source_check,
        DROP CONSTRAINT IF EXISTS ck_runtime_attention_outbox_source,
        ADD CONSTRAINT ck_runtime_attention_outbox_source CHECK (
            source IN ('model_breaker', 'fleet_halt', 'control_plane_condition')
        ),
        DROP CONSTRAINT ck_runtime_attention_outbox_source_edge,
        ADD CONSTRAINT ck_runtime_attention_outbox_source_edge CHECK (
            (manual_reissue_of IS NULL AND source = 'model_breaker'
                AND triggering_attempt_id IS NOT NULL AND fleet_halt_month IS NULL)
            OR (manual_reissue_of IS NULL AND source = 'fleet_halt'
                AND triggering_attempt_id IS NULL AND fleet_halt_month IS NOT NULL)
            OR (manual_reissue_of IS NULL AND source = 'control_plane_condition'
                AND triggering_attempt_id IS NULL AND fleet_halt_month IS NULL)
            OR (manual_reissue_of IS NOT NULL
                AND triggering_attempt_id IS NULL AND fleet_halt_month IS NULL)
        ),
        -- v3's three branches verbatim, plus one fixed condition projection:
        -- the ledger row id, its fixed kind, and when it was first detected.
        -- No summary, daemon name, endpoint, or condition metadata.
        DROP CONSTRAINT ck_runtime_attention_outbox_snapshot_allowlist,
        ADD CONSTRAINT ck_runtime_attention_outbox_snapshot_allowlist CHECK (
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
            OR (manual_reissue_of IS NOT NULL AND source = 'model_breaker'
                AND source_snapshot ?& ARRAY[
                    'catalog_entry_id', 'alias', 'model_id', 'triggering_attempt_id',
                    'consecutive_failures', 'reissue_of'
                ]
                AND source_snapshot - ARRAY[
                    'catalog_entry_id', 'alias', 'model_id', 'triggering_attempt_id',
                    'consecutive_failures', 'reissue_of'
                ] = '{}'::jsonb)
            OR (manual_reissue_of IS NULL AND source = 'control_plane_condition'
                AND source_snapshot ?& ARRAY['condition_id', 'condition_kind', 'first_detected_at']
                AND source_snapshot - ARRAY['condition_id', 'condition_kind', 'first_detected_at']
                    = '{}'::jsonb
                AND source_snapshot->>'condition_kind' IN ('fleet_control', 'qa_patrol_overdue'))
        ),
        DROP CONSTRAINT ck_runtime_attention_outbox_payload_allowlist,
        ADD CONSTRAINT ck_runtime_attention_outbox_payload_allowlist CHECK (
            (manual_reissue_of IS NULL AND source = 'model_breaker'
                AND payload ?& ARRAY['classification', 'consecutive_failures', 'door']
                AND payload - ARRAY['classification', 'consecutive_failures', 'door']
                    = '{}'::jsonb)
            OR (manual_reissue_of IS NULL AND source = 'fleet_halt'
                AND payload ?& ARRAY['classification', 'door']
                AND payload - ARRAY['classification', 'door'] = '{}'::jsonb)
            OR (manual_reissue_of IS NOT NULL AND source = 'model_breaker'
                AND payload ?& ARRAY['classification', 'consecutive_failures', 'door']
                AND payload - ARRAY['classification', 'consecutive_failures', 'door']
                    = '{}'::jsonb)
            OR (manual_reissue_of IS NULL AND source = 'control_plane_condition'
                AND payload ?& ARRAY['classification', 'door']
                AND payload - ARRAY['classification', 'door'] = '{}'::jsonb
                AND (payload->>'classification', payload->>'door') IN (
                    ('fleet_control_unhealthy', '/system'),
                    ('qa_patrol_overdue', '/system')
                ))
        );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_runtime_attention_outbox_control_plane_condition
        ON public.runtime_attention_outbox ((source_snapshot->>'condition_id'))
        WHERE source = 'control_plane_condition' AND manual_reissue_of IS NULL;

    -- Both v4 definers resolve names with pg_catalog and pg_temp only, and
    -- every relation below is schema-qualified.  The migration login can
    -- CREATE in public, so a public search_path would let it plant a better
    -- overload (e.g. public.hashtextextended(text, integer)) that then runs as
    -- runtime_attention_outbox_owner.
    CREATE OR REPLACE FUNCTION public.append_runtime_attention_condition(p_condition_id UUID)
    RETURNS UUID
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $append_runtime_attention_condition$
    DECLARE
        -- The producer-owned initial attention grace.  The observer cycles
        -- every half TTL, so an append lands well inside the ten-minute bound
        -- while a single transient failing cycle cannot page the owner.
        v_attention_grace CONSTANT INTERVAL := interval '5 minutes';
        v_enabled BOOLEAN;
        v_marker public.runtime_attention_condition_episodes%ROWTYPE;
        v_condition RECORD;
        v_kind TEXT;
        v_episode_id UUID;
        v_state TEXT;
    BEGIN
        IF current_setting('role', true) IS DISTINCT FROM 'butler_switchboard_rw' THEN
            RAISE EXCEPTION 'runtime-attention condition producer requires SET ROLE butler_switchboard_rw'
                USING ERRCODE = '42501';
        END IF;
        SELECT producer_enabled INTO v_enabled
        FROM public.runtime_attention_condition_control WHERE singleton;
        IF NOT COALESCE(v_enabled, false) THEN
            RETURN NULL;
        END IF;

        PERFORM pg_advisory_xact_lock(
            hashtextextended('runtime_attention_condition:' || p_condition_id::text, 0::bigint)
        );
        -- Emitted once, forever.  Only refresh the retained delivery category
        -- while the outbox row still exists; never mint a successor.
        SELECT * INTO v_marker
        FROM public.runtime_attention_condition_episodes
        WHERE condition_id = p_condition_id;
        IF FOUND THEN
            SELECT lifecycle_state INTO v_state
            FROM public.runtime_attention_outbox WHERE id = v_marker.episode_id;
            IF v_state IS NOT NULL AND v_state IS DISTINCT FROM v_marker.last_delivery_state THEN
                UPDATE public.runtime_attention_condition_episodes
                SET last_delivery_state = v_state, updated_at = now()
                WHERE condition_id = p_condition_id;
            END IF;
            RETURN v_marker.episode_id;
        END IF;

        SELECT source, fingerprint, state, first_detected_at INTO v_condition
        FROM public.infra_conditions WHERE id = p_condition_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'runtime-attention condition not found' USING ERRCODE = 'P0002';
        END IF;
        -- Fixed server-side identities (butlers.core.fleet_conditions).  The
        -- stopped-by-policy QA identity is an intentional hold, not a page.
        v_kind := CASE
            WHEN v_condition.source = 'control_plane_fleet'
             AND v_condition.fingerprint
                 = '72d8afdcf22a218132518788e87ecc64113b4d45b07616a628b840f985f9e799'
                THEN 'fleet_control'
            WHEN v_condition.source = 'qa_patrol_assurance'
             AND v_condition.fingerprint
                 = 'd3273e18ff648f040725c226cbde2317c3a20a06b2f7355186bc7e43979fbff2'
                THEN 'qa_patrol_overdue'
        END;
        IF v_kind IS NULL THEN
            RAISE EXCEPTION 'runtime-attention condition is not an owner-attention identity'
                USING ERRCODE = '22023';
        END IF;
        IF v_condition.state NOT IN ('open', 'aging')
           OR v_condition.first_detected_at > clock_timestamp() - v_attention_grace THEN
            RETURN NULL;
        END IF;

        INSERT INTO public.runtime_attention_outbox (source, source_snapshot, payload)
        VALUES (
            'control_plane_condition',
            jsonb_build_object(
                'condition_id', p_condition_id::text,
                'condition_kind', v_kind,
                'first_detected_at', v_condition.first_detected_at
            ),
            jsonb_build_object(
                'classification',
                CASE v_kind
                    WHEN 'fleet_control' THEN 'fleet_control_unhealthy'
                    ELSE 'qa_patrol_overdue'
                END,
                'door', '/system'
            )
        )
        ON CONFLICT ((source_snapshot->>'condition_id'))
            WHERE source = 'control_plane_condition' AND manual_reissue_of IS NULL
            DO NOTHING
        RETURNING id, lifecycle_state INTO v_episode_id, v_state;
        IF v_episode_id IS NULL THEN
            SELECT id, lifecycle_state INTO v_episode_id, v_state
            FROM public.runtime_attention_outbox
            WHERE source = 'control_plane_condition'
              AND manual_reissue_of IS NULL
              AND source_snapshot->>'condition_id' = p_condition_id::text;
        END IF;
        INSERT INTO public.runtime_attention_condition_episodes (
            condition_id, condition_kind, episode_id, last_delivery_state
        ) VALUES (p_condition_id, v_kind, v_episode_id, v_state)
        ON CONFLICT (condition_id) DO NOTHING;
        RETURN v_episode_id;
    END;
    $append_runtime_attention_condition$;

    CREATE OR REPLACE FUNCTION public.observe_runtime_attention_conditions()
    RETURNS TABLE (
        condition_id UUID,
        condition_kind TEXT,
        episode_id UUID,
        lifecycle_state TEXT,
        outbox_retained BOOLEAN,
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        delivered_at TIMESTAMPTZ,
        delivery_error_class TEXT,
        delivery_error_detail TEXT,
        delivery_worker_live BOOLEAN
    )
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $observe_runtime_attention_conditions$
    BEGIN
        IF COALESCE(current_setting('role', true), 'none') <> 'none' THEN
            RAISE EXCEPTION 'runtime-attention operator observation forbids SET ROLE'
                USING ERRCODE = '42501';
        END IF;
        RETURN QUERY
        SELECT marker.condition_id,
               marker.condition_kind,
               marker.episode_id,
               COALESCE(episode.lifecycle_state, marker.last_delivery_state),
               episode.id IS NOT NULL,
               marker.created_at,
               COALESCE(episode.updated_at, marker.updated_at),
               episode.delivered_at,
               episode.delivery_error_class,
               episode.delivery_error_detail,
               EXISTS (
                   SELECT 1 FROM public.runtime_attention_delivery_lease AS lease
                   WHERE lease.lease_name = 'runtime_attention_delivery'
                     AND lease.lease_token IS NOT NULL
                     AND lease.expires_at > clock_timestamp()
               )
        FROM public.runtime_attention_condition_episodes AS marker
        LEFT JOIN public.runtime_attention_outbox AS episode ON episode.id = marker.episode_id
        ORDER BY marker.created_at DESC, marker.condition_id;
    END;
    $observe_runtime_attention_conditions$;

    GRANT SELECT ON TABLE public.infra_conditions TO runtime_attention_outbox_owner;
    ALTER TABLE public.runtime_attention_condition_control
        OWNER TO runtime_attention_outbox_owner;
    ALTER TABLE public.runtime_attention_condition_episodes
        OWNER TO runtime_attention_outbox_owner;
    ALTER TABLE public.runtime_attention_condition_episodes ENABLE ROW LEVEL SECURITY;
    ALTER TABLE public.runtime_attention_condition_episodes FORCE ROW LEVEL SECURITY;
    DROP POLICY IF EXISTS runtime_attention_condition_episodes_owner
        ON public.runtime_attention_condition_episodes;
    CREATE POLICY runtime_attention_condition_episodes_owner
        ON public.runtime_attention_condition_episodes
        FOR ALL TO runtime_attention_outbox_owner USING (true) WITH CHECK (true);
    ALTER FUNCTION public.append_runtime_attention_condition(UUID)
        OWNER TO runtime_attention_outbox_owner;
    ALTER FUNCTION public.observe_runtime_attention_conditions()
        OWNER TO runtime_attention_outbox_owner;
    REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_condition_control FROM PUBLIC;
    REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_condition_episodes FROM PUBLIC;
    REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_condition(UUID) FROM PUBLIC;
    REVOKE ALL PRIVILEGES ON FUNCTION public.observe_runtime_attention_conditions() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.append_runtime_attention_condition(UUID)
        TO butler_switchboard_rw;
    EXECUTE format(
        'GRANT SELECT ON TABLE public.runtime_attention_condition_control TO %I',
        v_migration_role
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION public.observe_runtime_attention_conditions() TO %I',
        v_migration_role
    );
    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_v4() FROM %I',
        v_migration_role
    );
    -- Fence both entry points behind the no-login owner once the one-shot
    -- DDL has run, exactly as v3 does; the finalizer reasserts it on reruns.
    ALTER FUNCTION public.runtime_attention_deactivate_condition_v4()
        OWNER TO runtime_attention_outbox_owner;
    ALTER FUNCTION public.runtime_attention_upgrade_condition_v4()
        OWNER TO runtime_attention_outbox_owner;
END;
$runtime_attention_condition_v4$;

-- Rollback stops new appends only.  Emitted rows, markers, and their delivery
-- truth stay; a re-enable cannot re-page a condition that already has a marker.
CREATE OR REPLACE FUNCTION public.runtime_attention_deactivate_condition_v4()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_deactivate_condition_v4$
BEGIN
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false) THEN
        RAISE EXCEPTION 'runtime-attention condition rollback requires bootstrap superuser';
    END IF;
    UPDATE public.runtime_attention_condition_control SET producer_enabled = false
    WHERE singleton;
END;
$runtime_attention_deactivate_condition_v4$;

REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_v4() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_deactivate_condition_v4() FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers'
    )::name;
BEGIN
    IF to_regprocedure('public.append_runtime_attention_condition(uuid)') IS NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.runtime_attention_upgrade_condition_v4() TO %I',
            v_migration_role
        );
    ELSE
        EXECUTE format(
            'GRANT SELECT ON TABLE public.runtime_attention_condition_control TO %I',
            v_migration_role
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.observe_runtime_attention_conditions() TO %I',
            v_migration_role
        );
    END IF;
END;
$$;

-- bu-giazn6: the condition marker's last_delivery_state must follow every
-- delivery transition, not only those the controller happens to observe while
-- a condition is still active, so it stays truthful after the outbox row is
-- gone.  One AFTER UPDATE trigger on the outbox covers every writer (the
-- Switchboard worker's claim, terminal, and fencing UPDATEs) in the same
-- transaction; the worker role itself gains no privilege on the marker.  The
-- v4 append's refresh branch stays as a no-op fallback: with the trigger
-- installed the marker already equals the outbox state, and after a rollback
-- it restores the v4 behaviour exactly.
CREATE OR REPLACE FUNCTION public.runtime_attention_upgrade_condition_marker_v5()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_condition_marker_v5$
DECLARE
    v_migration_role NAME;
BEGIN
    SELECT migration_role INTO v_migration_role
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    IF v_migration_role IS NULL
       OR to_regclass('public.runtime_attention_outbox') IS NULL
       OR to_regclass('public.runtime_attention_condition_episodes') IS NULL THEN
        RAISE EXCEPTION
            'runtime-attention condition marker upgrade requires the v4 condition marker';
    END IF;
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false)
       AND session_user <> v_migration_role THEN
        RAISE EXCEPTION
            'runtime-attention condition marker upgrade requires its configured migration role';
    END IF;

    -- Owner-run: only the no-login outbox owner (the marker's sole RLS
    -- principal) writes the marker.  The body schema-qualifies its relation.
    CREATE OR REPLACE FUNCTION public.runtime_attention_sync_condition_marker()
    RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $runtime_attention_sync_condition_marker$
    BEGIN
        UPDATE public.runtime_attention_condition_episodes
        SET last_delivery_state = NEW.lifecycle_state, updated_at = now()
        WHERE episode_id = NEW.id
          AND last_delivery_state IS DISTINCT FROM NEW.lifecycle_state;
        RETURN NULL;
    END;
    $runtime_attention_sync_condition_marker$;
    ALTER FUNCTION public.runtime_attention_sync_condition_marker()
        OWNER TO runtime_attention_outbox_owner;
    REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_sync_condition_marker()
        FROM PUBLIC;

    DROP TRIGGER IF EXISTS runtime_attention_condition_marker_sync_trigger
        ON public.runtime_attention_outbox;
    CREATE TRIGGER runtime_attention_condition_marker_sync_trigger
        AFTER UPDATE OF lifecycle_state ON public.runtime_attention_outbox
        FOR EACH ROW
        WHEN (NEW.source = 'control_plane_condition'
              AND NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state)
        EXECUTE FUNCTION public.runtime_attention_sync_condition_marker();

    -- Converge markers left stale before the trigger existed.  Idempotent.
    UPDATE public.runtime_attention_condition_episodes AS marker
    SET last_delivery_state = episode.lifecycle_state, updated_at = now()
    FROM public.runtime_attention_outbox AS episode
    WHERE episode.id = marker.episode_id
      AND marker.last_delivery_state IS DISTINCT FROM episode.lifecycle_state;

    EXECUTE format(
        'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_marker_v5() FROM %I',
        v_migration_role
    );
    ALTER FUNCTION public.runtime_attention_deactivate_condition_marker_v5()
        OWNER TO runtime_attention_outbox_owner;
    ALTER FUNCTION public.runtime_attention_upgrade_condition_marker_v5()
        OWNER TO runtime_attention_outbox_owner;
END;
$runtime_attention_condition_marker_v5$;

-- Rollback removes the trigger and its function and keeps every marker row
-- and its last recorded delivery category.  Unlike the v4 deactivator it is
-- not a definer: it runs with the calling bootstrap superuser's authority so it
-- can also hand the upgrader back to the bootstrap and re-offer it, making a
-- later re-upgrade a plain migration step.
CREATE OR REPLACE FUNCTION public.runtime_attention_deactivate_condition_marker_v5()
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_deactivate_condition_marker_v5$
DECLARE
    v_migration_role NAME;
    v_bootstrap_owner NAME;
BEGIN
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = current_user), false) THEN
        RAISE EXCEPTION 'runtime-attention condition marker rollback requires bootstrap superuser'
            USING ERRCODE = '42501';
    END IF;
    IF to_regclass('public.runtime_attention_outbox') IS NOT NULL THEN
        DROP TRIGGER IF EXISTS runtime_attention_condition_marker_sync_trigger
            ON public.runtime_attention_outbox;
    END IF;
    DROP FUNCTION IF EXISTS public.runtime_attention_sync_condition_marker();

    SELECT migration_role INTO v_migration_role
    FROM runtime_attention_admin.bootstrap_configuration
    WHERE singleton;
    SELECT pg_get_userbyid(nspowner) INTO v_bootstrap_owner
    FROM pg_namespace
    WHERE nspname = 'runtime_attention_admin';
    IF v_bootstrap_owner IS NOT NULL THEN
        EXECUTE format(
            'ALTER FUNCTION public.runtime_attention_upgrade_condition_marker_v5() OWNER TO %I',
            v_bootstrap_owner
        );
    END IF;
    IF v_migration_role IS NOT NULL THEN
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.runtime_attention_upgrade_condition_marker_v5() TO %I',
            v_migration_role
        );
    END IF;
END;
$runtime_attention_deactivate_condition_marker_v5$;

REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_marker_v5() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_deactivate_condition_marker_v5() FROM PUBLIC;

DO $$
DECLARE
    v_migration_role NAME := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers'
    )::name;
BEGIN
    -- Bootstrap-owned until used, as v4: a rollback removes the installed
    -- function, so hand the upgrader back to the bootstrap and re-offer it.
    IF to_regprocedure('public.runtime_attention_sync_condition_marker()') IS NULL THEN
        EXECUTE format(
            'ALTER FUNCTION public.runtime_attention_upgrade_condition_marker_v5() OWNER TO %I',
            current_user
        );
        EXECUTE format(
            'GRANT EXECUTE ON FUNCTION public.runtime_attention_upgrade_condition_marker_v5() TO %I',
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
              'rollback_interface',
              'upgrade_producers_v2',
              'deactivate_producers_v2',
              'install_legacy_debounce_marker',
              'install_fleet_halt_producer_v2',
              'finalize_condition_marker_v5'
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
    ADD COLUMN IF NOT EXISTS interface_version INTEGER NOT NULL DEFAULT 1
        CHECK (interface_version IN (1, 2)),
    ADD COLUMN IF NOT EXISTS producers_enabled BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS producer_activated_at TIMESTAMPTZ;
-- ADD COLUMN IF NOT EXISTS skips its entire subcommand once the column exists,
-- CHECK included, so databases already carried through the unconstrained ALTER
-- can only be converged by installing the constraint explicitly. PostgreSQL has
-- no ADD CONSTRAINT IF NOT EXISTS, hence the catalog guard.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint AS version_constraint
        JOIN pg_attribute AS constrained_column
          ON constrained_column.attrelid = version_constraint.conrelid
         AND constrained_column.attnum = ANY (version_constraint.conkey)
        WHERE version_constraint.conrelid
                  = 'runtime_attention_admin.bootstrap_configuration'::regclass
          AND version_constraint.contype = 'c'
          AND constrained_column.attname = 'interface_version'
    ) THEN
        ALTER TABLE runtime_attention_admin.bootstrap_configuration
            ADD CONSTRAINT bootstrap_configuration_interface_version_check
            CHECK (interface_version IN (1, 2));
    END IF;
END;
$$;
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

-- Single source of truth for the legacy debounce-marker planter's body.
--
-- The body cannot live only in upgrade_producers_v2: that upgrader is invoked
-- once, by core_199, and never re-runs on a database already at version 2, so a
-- literal edited there reaches fresh bootstraps only.  finalize_interface does
-- re-run -- scripts/init-db.sql calls it on every rerun of an installed
-- database -- so it adopts this definition too, and fresh and pre-existing
-- databases converge on one body by construction rather than by review.
--
-- CREATE OR REPLACE preserves the function's OID, owner, and ACL, so the
-- trigger stays bound and the finalizer's ownership work is not undone.
CREATE OR REPLACE FUNCTION runtime_attention_admin.install_legacy_debounce_marker()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_install_legacy_debounce_marker$
BEGIN
    -- Plants the debounce markers that make a pre-v2 runtime suppress its own
    -- runtime-attention sends.  Read the next paragraph before trusting the name
    -- of anything in this block.
    --
    -- THIS BLOCKS NOTHING.  It is a BEFORE INSERT trigger that returns NEW
    -- unconditionally, so every row it sees is inserted; it has no reject path
    -- and no ingress gate.  Its entire effect is to write at most one
    -- public.audit_log row.  It was previously called
    -- runtime_attention_legacy_producer_fence and wrote actor
    -- 'runtime_attention_cutover_fence' with a note of 'blocked_old_binary';
    -- two reviewers independently read that name plus SECURITY DEFINER and
    -- concluded an enforcement boundary existed here, which is why it was
    -- renamed (bu-kww1r) and why the audit vocabulary followed (bu-95gq7).
    -- Behaviour is unchanged; audit_log rows written before bu-95gq7 keep the
    -- old actor and note, so any query over them must accept both.
    --
    -- The real mechanism is cooperative self-suppression.  The retired
    -- model_breaker_attention and fleet_halt_attention helpers each debounced on
    --     SELECT ... FROM public.audit_log WHERE target = $1 AND action = $2
    --     ORDER BY ts DESC LIMIT 1
    -- with NO actor filter, so a row planted here under a different actor still
    -- satisfies their lookup and they skip before transport.  The old binary
    -- suppresses itself; nothing in the database compels it.
    --
    -- That holds only while an old binary honours its own debounce, so it fails
    -- in at least four ways that a real fence would not:
    --   * a producer that never performs the lookup is entirely unaffected;
    --   * both helpers failed OPEN on any lookup error -- they treated a failed
    --     debounce read as "not yet notified" and sent anyway;
    --   * the breaker debounce expired after a 15-minute cooldown, so this
    --     suppresses re-notification for a window, not permanently;
    --   * the ceiling debounce only matched within the same UTC month.
    -- Both helpers were retired in #3742 and no longer exist in this repository,
    -- so in the current tree nothing reads these markers at all.  They matter
    -- only against a deployed binary older than that commit.
    CREATE OR REPLACE FUNCTION public.runtime_attention_plant_legacy_debounce_marker()
    RETURNS trigger
    LANGUAGE plpgsql
    -- SECURITY DEFINER is retained deliberately, and not for capability: the
    -- canonical butler_*_rw roles already hold INSERT on public.audit_log via the
    -- broad public-schema grant above, so the invoker could write this row itself.
    -- It earns its keep as blast-radius isolation.  This runs BEFORE INSERT on
    -- model_dispatch_attempts, so a failed marker write aborts the dispatch-attempt
    -- INSERT with it.  Executing as the NOLOGIN, non-inherit
    -- runtime_attention_outbox_owner decouples the marker from the invoker's
    -- grants, so tightening that broad public-schema grant -- exactly what the ACL
    -- finalizer exists to do -- cannot turn this into a fleet-wide failure to
    -- record dispatch attempts.  core_199's catalog proof also asserts prosecdef.
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $runtime_attention_plant_legacy_debounce_marker_v2$
    DECLARE
        v_active_role TEXT := COALESCE(current_setting('role', true), '');
    BEGIN
        IF v_active_role = ANY (ARRAY[
            'butler_chronicler_rw', 'butler_concierge_rw', 'butler_education_rw',
            'butler_finance_rw', 'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw', 'butler_relationship_rw',
            'butler_switchboard_rw', 'butler_travel_rw'
        ]) AND COALESCE(
            current_setting('butlers.runtime_attention_producer_abi', true), ''
        ) <> '2' THEN
            IF NEW.outcome = 'runtime_failure' THEN
                -- (target, action) are the load-bearing pair -- they are what
                -- the retired helper's debounce matched on.  This note is read
                -- by nobody, so bu-95gq7 was free to replace the previous
                -- 'blocked_old_binary' with something that does not claim an
                -- enforcement that never existed.
                INSERT INTO public.audit_log (actor, action, target, note)
                VALUES (
                    'runtime_attention_legacy_debounce_marker',
                    'model_breaker_open_notified',
                    'model_breaker:' || NEW.catalog_entry_id::text,
                    'legacy_debounce_planted'
                );
            ELSIF NEW.outcome = 'quota_skip'
                  AND left(
                      COALESCE(NEW.failure_reason, ''),
                      length('Monthly spend ceiling reached')
                  ) = 'Monthly spend ceiling reached' THEN
                -- Here the note IS load-bearing: the retired fleet-halt helper
                -- compared it against the current window and skipped on a match.
                -- Do not change this format.
                INSERT INTO public.audit_log (actor, action, target, note)
                VALUES (
                    'runtime_attention_legacy_debounce_marker',
                    'ceiling_halt_notified',
                    'ceiling_halt',
                    to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM')
                );
            END IF;
        END IF;
        RETURN NEW;
    END;
    $runtime_attention_plant_legacy_debounce_marker_v2$;
END;
$runtime_attention_install_legacy_debounce_marker$;

-- Single source of truth for the v2 fleet-halt producer's body.
--
-- Same convergence contract as install_legacy_debounce_marker above: the
-- body cannot live only in upgrade_producers_v2, which core_199 invokes once
-- and which never re-runs on a database already at version 2.  finalize_interface
-- adopts this definition on every init-db rerun, so a fresh bootstrap and an
-- already-upgraded database cannot drift.
--
-- CREATE OR REPLACE preserves the function's OID, owner, and ACL, so the
-- finalizer's ownership work is not undone.
CREATE OR REPLACE FUNCTION runtime_attention_admin.install_fleet_halt_producer_v2()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_install_fleet_halt_producer_v2$
BEGIN
    CREATE OR REPLACE FUNCTION public.append_runtime_attention_fleet_halt()
    RETURNS UUID
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
    AS $runtime_attention_fleet_halt_v2$
    DECLARE
        -- transaction_timestamp(), not clock_timestamp(), and identical to v1's
        -- declaration below.  bu-jxelx: v2 had drifted to clock_timestamp() while
        -- the Python recorder computed a second, independent month expression for
        -- an advisory lock it took before inserting the denial row.  Across a UTC
        -- month rollover the two disagreed, so that lock serialized a month nobody
        -- was writing, at exactly the moment the fleet-halt path is most likely to
        -- fire; the unique index on fleet_halt_month kept it from double-paging, so
        -- it degraded silently.  bu-86t7r then removed the recorder's lock as
        -- redundant with the one this body takes below, which leaves v_month the
        -- only month the transaction names -- it is what this body locks on, what
        -- it filters evidence by, and what it writes.  Keep it one declaration read
        -- by all three: that, rather than the choice of clock, is what makes the
        -- bu-jxelx class of disagreement impossible.
        v_month DATE := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
        v_denied_count INTEGER;
        v_first_denied_at TIMESTAMPTZ;
        v_episode_id UUID;
        v_enabled BOOLEAN;
        v_activated_at TIMESTAMPTZ;
    BEGIN
        IF COALESCE(current_setting('role', true), '') <> ALL (ARRAY[
            'butler_chronicler_rw', 'butler_concierge_rw', 'butler_education_rw',
            'butler_finance_rw', 'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw', 'butler_relationship_rw',
            'butler_switchboard_rw', 'butler_travel_rw'
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
          -- Lower bound only, and anchored in UTC rather than the session
          -- TimeZone a bare `ts >= v_month` would borrow.  v_month is
          -- transaction-stable while the denial row's ts is the statement
          -- clock, so a transaction that crosses the UTC rollover stamps its
          -- row in the month *after* the one it locks, filters by, and writes.
          -- An equality on both bounds did not count that row, and when it was
          -- the month's first ceiling denial the RAISE below took the attempt
          -- row down with it (bu-guxz8).  Dropping the upper bound is safe
          -- here: every ts this query can see is stamped by the same server
          -- clock as now() -- the recorder writes clock_timestamp() and the
          -- column defaults to now() -- so a visible row can only sit past the
          -- month end by this transaction's own age, and a role that could
          -- hand-date a denial into a later month could equally hand-date one
          -- into this one, so the upper bound never bounded fabrication.  The
          -- widening only adds rows above the old window, so min(ts) is
          -- unchanged and the pre-activation guard below still compares the
          -- month's *first* denial against activation.
          AND ts >= (v_month::timestamp AT TIME ZONE 'UTC');
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
            hashtextextended('runtime_attention_fleet_halt:' || v_month::text, 0::bigint)
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
END;
$runtime_attention_install_fleet_halt_producer_v2$;

-- Re-fence the installed v4 condition interface on every init-db rerun: exact
-- owner, one owner-only RLS policy on the marker, Switchboard as the only
-- producer, and the migration login holding only the projection.
CREATE OR REPLACE FUNCTION runtime_attention_admin.finalize_condition_v4(p_migration_role NAME)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_finalize_condition_v4$
DECLARE
    v_acl_role NAME;
    v_policy_name NAME;
BEGIN
    IF to_regclass('public.runtime_attention_condition_episodes') IS NULL
       OR to_regprocedure('public.append_runtime_attention_condition(uuid)') IS NULL
       OR to_regprocedure('public.observe_runtime_attention_conditions()') IS NULL
       OR to_regclass('public.infra_conditions') IS NULL THEN
        RAISE EXCEPTION 'runtime-attention condition interface is incomplete';
    END IF;
    EXECUTE 'ALTER TABLE public.runtime_attention_condition_control OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER TABLE public.runtime_attention_condition_episodes OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_condition(uuid) OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.observe_runtime_attention_conditions() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_upgrade_condition_v4() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_condition(uuid) SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION public.observe_runtime_attention_conditions() SET search_path = pg_catalog, pg_temp';

    EXECUTE 'ALTER TABLE public.runtime_attention_condition_episodes ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE public.runtime_attention_condition_episodes FORCE ROW LEVEL SECURITY';
    FOR v_policy_name IN
        SELECT policy.polname::name
        FROM pg_policy AS policy
        WHERE policy.polrelid = 'public.runtime_attention_condition_episodes'::regclass
    LOOP
        EXECUTE format(
            'DROP POLICY %I ON public.runtime_attention_condition_episodes', v_policy_name
        );
    END LOOP;
    EXECUTE 'CREATE POLICY runtime_attention_condition_episodes_owner '
        || 'ON public.runtime_attention_condition_episodes '
        || 'FOR ALL TO runtime_attention_outbox_owner USING (true) WITH CHECK (true)';

    -- Only the no-login owner touches the marker and control rows directly;
    -- only Switchboard's role may append; only the migration login observes.
    FOR v_acl_role IN
        SELECT DISTINCT role_row.rolname::name
        FROM pg_class AS relation
        CROSS JOIN LATERAL aclexplode(
            COALESCE(relation.relacl, acldefault('r', relation.relowner))
        ) AS acl
        JOIN pg_roles AS role_row ON role_row.oid = acl.grantee
        WHERE relation.oid IN (
            'public.runtime_attention_condition_control'::regclass,
            'public.runtime_attention_condition_episodes'::regclass
        )
          AND role_row.rolname <> 'runtime_attention_outbox_owner'
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_condition_control FROM %I',
            v_acl_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_condition_episodes FROM %I',
            v_acl_role
        );
    END LOOP;
    FOR v_acl_role IN
        SELECT DISTINCT role_row.rolname::name
        FROM pg_proc AS interface_function
        CROSS JOIN LATERAL aclexplode(
            COALESCE(interface_function.proacl, acldefault('f', interface_function.proowner))
        ) AS acl
        JOIN pg_roles AS role_row ON role_row.oid = acl.grantee
        WHERE interface_function.oid IN (
            'public.append_runtime_attention_condition(uuid)'::regprocedure,
            'public.observe_runtime_attention_conditions()'::regprocedure,
            'public.runtime_attention_upgrade_condition_v4()'::regprocedure
        )
          AND role_row.rolname <> 'runtime_attention_outbox_owner'
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_condition(uuid) FROM %I',
            v_acl_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION public.observe_runtime_attention_conditions() FROM %I',
            v_acl_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_v4() FROM %I',
            v_acl_role
        );
    END LOOP;
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_condition_control FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON TABLE public.runtime_attention_condition_episodes FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.append_runtime_attention_condition(uuid) FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.observe_runtime_attention_conditions() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_v4() FROM PUBLIC';
    EXECUTE 'GRANT EXECUTE ON FUNCTION public.append_runtime_attention_condition(uuid) TO butler_switchboard_rw';
    EXECUTE format(
        'GRANT SELECT ON TABLE public.runtime_attention_condition_control TO %I', p_migration_role
    );
    EXECUTE format(
        'GRANT EXECUTE ON FUNCTION public.observe_runtime_attention_conditions() TO %I',
        p_migration_role
    );
    EXECUTE 'GRANT SELECT ON TABLE public.infra_conditions TO runtime_attention_outbox_owner';
END;
$runtime_attention_finalize_condition_v4$;

REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.finalize_condition_v4(NAME) FROM PUBLIC;

-- Re-fence the installed v5 marker sync on every init-db rerun.  The interface
-- finalizer drops every non-internal outbox trigger first, so this restores
-- the sync trigger beside the guard, with its owner-run definer pinned and
-- executable by nobody but its owner.
CREATE OR REPLACE FUNCTION runtime_attention_admin.finalize_condition_marker_v5()
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $runtime_attention_finalize_condition_marker_v5$
DECLARE
    v_acl_role NAME;
BEGIN
    IF to_regprocedure('public.runtime_attention_sync_condition_marker()') IS NULL
       OR to_regclass('public.runtime_attention_condition_episodes') IS NULL
       OR to_regclass('public.runtime_attention_outbox') IS NULL THEN
        RAISE EXCEPTION 'runtime-attention condition marker sync is incomplete';
    END IF;
    EXECUTE 'ALTER FUNCTION public.runtime_attention_sync_condition_marker() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_sync_condition_marker() SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_upgrade_condition_marker_v5() OWNER TO runtime_attention_outbox_owner';
    FOR v_acl_role IN
        SELECT DISTINCT role_row.rolname::name
        FROM pg_proc AS interface_function
        CROSS JOIN LATERAL aclexplode(
            COALESCE(interface_function.proacl, acldefault('f', interface_function.proowner))
        ) AS acl
        JOIN pg_roles AS role_row ON role_row.oid = acl.grantee
        WHERE interface_function.oid IN (
            'public.runtime_attention_sync_condition_marker()'::regprocedure,
            'public.runtime_attention_upgrade_condition_marker_v5()'::regprocedure
        )
          AND role_row.rolname <> 'runtime_attention_outbox_owner'
    LOOP
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_sync_condition_marker() FROM %I',
            v_acl_role
        );
        EXECUTE format(
            'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_marker_v5() FROM %I',
            v_acl_role
        );
    END LOOP;
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_sync_condition_marker() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_marker_v5() FROM PUBLIC';
    EXECUTE 'DROP TRIGGER IF EXISTS runtime_attention_condition_marker_sync_trigger '
        || 'ON public.runtime_attention_outbox';
    EXECUTE 'CREATE TRIGGER runtime_attention_condition_marker_sync_trigger '
        || 'AFTER UPDATE OF lifecycle_state ON public.runtime_attention_outbox '
        || 'FOR EACH ROW '
        || 'WHEN (NEW.source = ''control_plane_condition'' '
        || 'AND NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state) '
        || 'EXECUTE FUNCTION public.runtime_attention_sync_condition_marker()';
END;
$runtime_attention_finalize_condition_marker_v5$;

REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.finalize_condition_marker_v5() FROM PUBLIC;

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
       OR to_regprocedure('public.runtime_attention_delivery_lease_guard()') IS NULL
       OR to_regprocedure('public.runtime_attention_upgrade_operator_v3()') IS NULL
       OR to_regprocedure('public.runtime_attention_deactivate_operator_v3()') IS NULL
       OR to_regprocedure('public.runtime_attention_upgrade_condition_v4()') IS NULL
       OR to_regprocedure('public.runtime_attention_deactivate_condition_v4()') IS NULL
       OR to_regprocedure('public.runtime_attention_upgrade_condition_marker_v5()') IS NULL
       OR to_regprocedure('public.runtime_attention_deactivate_condition_marker_v5()') IS NULL THEN
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
            'public.runtime_attention_delivery_lease_guard()'::regprocedure,
            'public.runtime_attention_upgrade_operator_v3()'::regprocedure,
            'public.runtime_attention_deactivate_operator_v3()'::regprocedure,
            'public.runtime_attention_upgrade_condition_v4()'::regprocedure,
            'public.runtime_attention_deactivate_condition_v4()'::regprocedure,
            'public.runtime_attention_upgrade_condition_marker_v5()'::regprocedure,
            'public.runtime_attention_deactivate_condition_marker_v5()'::regprocedure
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
    -- The v3 upgrader is bootstrap-owned until its one-shot migration has
    -- created the operator control table.  Moving it earlier would make its
    -- DDL execute with the least-privilege outbox owner.  The deactivator only
    -- updates the outbox-owned control table and can be fenced immediately.
    EXECUTE 'ALTER FUNCTION public.runtime_attention_deactivate_operator_v3() OWNER TO runtime_attention_outbox_owner';
    IF to_regclass('public.runtime_attention_operator_control') IS NOT NULL THEN
        EXECUTE 'ALTER TABLE public.runtime_attention_operator_control OWNER TO runtime_attention_outbox_owner';
        EXECUTE 'ALTER FUNCTION public.runtime_attention_upgrade_operator_v3() OWNER TO runtime_attention_outbox_owner';
    END IF;
    -- The v4 condition upgrader follows the same bootstrap-owned-until-used
    -- rule; its installed objects are re-fenced on every rerun.
    EXECUTE 'ALTER FUNCTION public.runtime_attention_deactivate_condition_v4() OWNER TO runtime_attention_outbox_owner';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_deactivate_condition_marker_v5() OWNER TO runtime_attention_outbox_owner';
    IF to_regclass('public.runtime_attention_condition_control') IS NOT NULL THEN
        PERFORM runtime_attention_admin.finalize_condition_v4(v_migration_role);
    END IF;
    -- bu-mms5xl: every runtime-attention definer resolves names in pg_catalog
    -- and pg_temp only.  The migration login can CREATE in public, so a
    -- public entry would let it plant a better-matching overload (e.g.
    -- public.hashtextextended(text, integer)) that then runs as
    -- runtime_attention_outbox_owner.  Bodies schema-qualify every relation.
    -- The v3 operator functions are created once by their upgrader, so the
    -- pin on an already-upgraded database lands here.
    IF to_regprocedure('public.reissue_runtime_attention_episode(uuid)') IS NOT NULL THEN
        EXECUTE 'ALTER FUNCTION public.observe_runtime_attention_models() SET search_path = pg_catalog, pg_temp';
        EXECUTE 'ALTER FUNCTION public.observe_runtime_attention_fleet_halt() SET search_path = pg_catalog, pg_temp';
        EXECUTE 'ALTER FUNCTION public.reissue_runtime_attention_episode(uuid) SET search_path = pg_catalog, pg_temp';
    END IF;
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_model_breaker(bigint) SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION public.append_runtime_attention_fleet_halt() SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_active_switchboard_role() SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_outbox_guard() SET search_path = pg_catalog, pg_temp';
    EXECUTE 'ALTER FUNCTION public.runtime_attention_delivery_lease_guard() SET search_path = pg_catalog, pg_temp';

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
    -- The v5 condition-marker sync is the one other bootstrap-defined outbox
    -- trigger; restore it (and re-fence its definer) only while installed.
    IF to_regprocedure('public.runtime_attention_sync_condition_marker()') IS NOT NULL THEN
        PERFORM runtime_attention_admin.finalize_condition_marker_v5();
    END IF;

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
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_operator_v3() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_deactivate_operator_v3() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_upgrade_condition_marker_v5() FROM PUBLIC';
    EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION public.runtime_attention_deactivate_condition_marker_v5() FROM PUBLIC';
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
              'butler_chronicler_rw', 'butler_concierge_rw', 'butler_education_rw',
              'butler_finance_rw', 'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
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
        'butler_concierge_rw',
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
        'butler_concierge_rw',
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

    -- The delivery worker is activated (roster/switchboard/modules/__init__.py
    -- constructs and schedules it at startup), so the REQ-database-security-007
    -- carve-out that kept the finite terminal-error vocabulary and the optional
    -- notification reference ungranted no longer applies: Switchboard needs
    -- write access to record a proven terminal outcome. The
    -- ck_runtime_attention_outbox_delivery_evidence CHECK constraint remains the
    -- enforcement boundary -- only the fixed non-secret
    -- (delivery_error_class, delivery_error_detail) pairs are accepted, and
    -- notification_ref stays a plain scalar UUID with no cross-schema FK.
    EXECUTE 'GRANT SELECT, UPDATE (lifecycle_state, claim_token, claim_epoch, delivery_lease_epoch, claimed_by_instance, claimed_at, claim_expires_at, next_attempt_at, delivered_at, delivery_error_class, delivery_error_detail, notification_ref) '
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
            'butler_chronicler_rw', 'butler_concierge_rw', 'butler_education_rw',
            'butler_finance_rw', 'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw', 'butler_relationship_rw',
            'butler_switchboard_rw', 'butler_travel_rw',
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
        -- bu-kww1r renamed the marker planter.  Its body lives in
        -- upgrade_producers_v2, which does not re-run once a database is at v2,
        -- so adopt the new name here: ALTER ... RENAME preserves the OID, which
        -- is what the trigger binds to, so this is a pure rename and the marker
        -- keeps planting across it.  Without this the ALTERs below would raise
        -- "function does not exist" on every rerun of an existing v2 database.
        -- The rename alone leaves the stored body untouched; the adoption
        -- below refreshes it, which is what converges the audit literals.
        IF to_regprocedure('public.runtime_attention_plant_legacy_debounce_marker()') IS NULL
           AND to_regprocedure('public.runtime_attention_legacy_producer_fence()') IS NOT NULL
        THEN
            EXECUTE 'ALTER FUNCTION public.runtime_attention_legacy_producer_fence() '
                || 'RENAME TO runtime_attention_plant_legacy_debounce_marker';
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgrelid = 'public.model_dispatch_attempts'::regclass
              AND tgname = 'runtime_attention_legacy_producer_fence_trigger'
              AND NOT tgisinternal
        ) THEN
            EXECUTE 'ALTER TRIGGER runtime_attention_legacy_producer_fence_trigger '
                || 'ON public.model_dispatch_attempts '
                || 'RENAME TO runtime_attention_plant_legacy_debounce_marker_trigger';
        END IF;
        EXECUTE 'ALTER FUNCTION public.runtime_attention_plant_legacy_debounce_marker() '
            || 'OWNER TO runtime_attention_outbox_owner';
        EXECUTE 'ALTER FUNCTION public.runtime_attention_plant_legacy_debounce_marker() '
            || 'SET search_path = pg_catalog, pg_temp';
        EXECUTE 'REVOKE ALL PRIVILEGES ON FUNCTION '
            || 'public.runtime_attention_plant_legacy_debounce_marker() FROM PUBLIC';
        -- Adopt the current body last, after the ALTERs above have proven the
        -- renamed function exists.  Doing it earlier would let a missing rename
        -- adoption create a second function under the new name while the trigger
        -- stayed bound to the old OID -- convergence that silently did nothing.
        -- CREATE OR REPLACE keeps the OID, owner, and ACL just re-asserted.
        PERFORM runtime_attention_admin.install_legacy_debounce_marker();
        -- Same adoption for the v2 fleet-halt producer: its body also lives only
        -- in the one-shot upgrader, so an existing v2 database converges on the
        -- transaction-stable month key through this rerun (bu-jxelx).  The common
        -- section above already re-asserted this function's owner and search_path,
        -- and CREATE OR REPLACE keeps both.
        PERFORM runtime_attention_admin.install_fleet_halt_producer_v2();
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
        -- Terminal delivery evidence: only fixed, non-secret codes can ever
        -- be persisted (ck_runtime_attention_outbox_delivery_evidence below).
        -- Producer functions never write these; only the activated
        -- Switchboard delivery worker does, via mark_failed/mark_uncertain.
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
    SET search_path = pg_catalog, pg_temp
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
    SET search_path = pg_catalog, pg_temp
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
    SET search_path = pg_catalog, pg_temp
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
    SET search_path = pg_catalog, pg_temp
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
            'butler_chronicler_rw', 'butler_concierge_rw', 'butler_education_rw',
            'butler_finance_rw', 'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw', 'butler_relationship_rw',
            'butler_switchboard_rw', 'butler_travel_rw'
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
    SET search_path = pg_catalog, pg_temp
    AS $runtime_attention_fleet_halt$
    DECLARE
        v_month DATE := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
        v_denied_count INTEGER;
        v_first_denied_at TIMESTAMPTZ;
        v_episode_id UUID;
    BEGIN
        IF COALESCE(current_setting('role', true), '') <> ALL (ARRAY[
            'butler_chronicler_rw', 'butler_concierge_rw', 'butler_education_rw',
            'butler_finance_rw', 'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw', 'butler_relationship_rw',
            'butler_switchboard_rw', 'butler_travel_rw'
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
          -- Same lower-bound-only window as the v2 body above (bu-guxz8):
          -- the month key is transaction-stable, the denial's ts is the
          -- statement clock, and a rollover-crossing denial still has to count
          -- as evidence for the month this call is guarding.
          AND ts >= (v_month::timestamp AT TIME ZONE 'UTC');
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
    IF NOT COALESCE((SELECT rolsuper FROM pg_roles WHERE rolname = session_user), false)
       AND session_user <> v_migration_role THEN
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
    SET search_path = pg_catalog, pg_temp
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
            'butler_chronicler_rw', 'butler_concierge_rw', 'butler_education_rw',
            'butler_finance_rw', 'butler_general_rw', 'butler_health_rw', 'butler_home_rw',
            'butler_lifestyle_rw', 'butler_messenger_rw', 'butler_qa_rw', 'butler_relationship_rw',
            'butler_switchboard_rw', 'butler_travel_rw'
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

    -- The v2 fleet-halt producer's body is defined once, in
    -- runtime_attention_admin.install_fleet_halt_producer_v2, so that this
    -- one-shot upgrader and the re-runnable finalizer cannot drift apart.
    PERFORM runtime_attention_admin.install_fleet_halt_producer_v2();

    -- The planter's body is defined once, in
    -- runtime_attention_admin.install_legacy_debounce_marker, so that this
    -- one-shot upgrader and the re-runnable finalizer cannot drift apart.
    PERFORM runtime_attention_admin.install_legacy_debounce_marker();

    DROP TRIGGER IF EXISTS runtime_attention_legacy_producer_fence_trigger
        ON public.model_dispatch_attempts;
    DROP TRIGGER IF EXISTS runtime_attention_plant_legacy_debounce_marker_trigger
        ON public.model_dispatch_attempts;
    CREATE TRIGGER runtime_attention_plant_legacy_debounce_marker_trigger
        BEFORE INSERT ON public.model_dispatch_attempts
        FOR EACH ROW
        EXECUTE FUNCTION public.runtime_attention_plant_legacy_debounce_marker();

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
    -- Since core_199, `alembic downgrade` reaches this refusal only on a chain
    -- stopped at core_198: core_199's downgrade deliberately retains
    -- public.runtime_attention_producer_control and the legacy debounce-marker
    -- planter, which core_198's _TRUSTED_BOOTSTRAP_ROLLBACK_SQL preflight
    -- requires to be absent, so from head that preflight raises first and this
    -- function is never called.  A bootstrap superuser invoking
    -- rollback_interface() directly still lands here, and the migration login
    -- cannot (finalize_interface revokes its EXECUTE), so both refusals are all
    -- that stands between a hand-run rollback and a nonempty outbox.
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
REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.install_legacy_debounce_marker() FROM PUBLIC;
REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.install_fleet_halt_producer_v2() FROM PUBLIC;

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
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.install_legacy_debounce_marker() FROM %I', v_migration_role);
    EXECUTE format('REVOKE ALL PRIVILEGES ON FUNCTION runtime_attention_admin.install_fleet_halt_producer_v2() FROM %I', v_migration_role);

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

-- Owner-auth migration runs as a normal migration login. Provision its isolated
-- capability role here, under bootstrap authority; never grant CREATEROLE to
-- the migrator. Login credentials/membership remain explicit host provisioning.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard_auth_api') THEN
        CREATE ROLE dashboard_auth_api NOLOGIN NOINHERIT NOSUPERUSER NOCREATEROLE
            NOCREATEDB NOREPLICATION NOBYPASSRLS;
    END IF;
END;
$$;

-- Endpoint custody: fixed existing-bootstrap-owner installation, core_260.
-- No role, LOGIN, membership, credential or privileged host service is added.
DO $custody_bootstrap$
DECLARE
    migration_role name := COALESCE(
        NULLIF(current_setting('butlers.connecting_user', true), ''), 'butlers'
    )::name;
    existing_owner name;
BEGIN
    IF current_user::name = migration_role OR NOT EXISTS (
        SELECT FROM pg_catalog.pg_roles WHERE rolname=current_user AND rolsuper
    ) THEN
        RAISE EXCEPTION 'custody bootstrap requires existing privileged bootstrap authority';
    END IF;
    SELECT r.rolname INTO existing_owner
    FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_roles r ON r.oid=n.nspowner
    WHERE n.nspname='custody_admission';
    IF existing_owner IS NULL THEN
        EXECUTE pg_catalog.format('CREATE SCHEMA custody_admission AUTHORIZATION %I', current_user);
        COMMENT ON SCHEMA custody_admission IS 'butlers:endpoint-custody:core_260';
    ELSIF NOT EXISTS (
        SELECT FROM pg_catalog.pg_roles WHERE rolname=existing_owner AND rolsuper
    ) OR existing_owner=migration_role THEN
        RAISE EXCEPTION 'custody schema owner is not the existing trusted bootstrap owner';
    ELSE
        EXECUTE pg_catalog.format('SET ROLE %I', existing_owner);
    END IF;
END;
$custody_bootstrap$;

REVOKE ALL ON SCHEMA custody_admission FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA custody_admission REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA custody_admission REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE IF NOT EXISTS custody_admission.bootstrap_configuration (
    singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
    bootstrap_owner_oid oid NOT NULL,
    schema_identity jsonb,
    connecting_role name NOT NULL,
    version integer NOT NULL CHECK(version=1),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO custody_admission.bootstrap_configuration(
    singleton, bootstrap_owner_oid, connecting_role, version
) VALUES (
    true, (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user),
    COALESCE(NULLIF(current_setting('butlers.connecting_user',true),''),'butlers')::name, 1
) ON CONFLICT(singleton) DO NOTHING;

CREATE OR REPLACE FUNCTION custody_admission.canonical_json_at_depth(value jsonb,depth integer)
RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $custody_json$
DECLARE result text;
BEGIN
    IF depth<0 OR depth>64 THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    CASE pg_catalog.jsonb_typeof(value)
    WHEN 'object' THEN
        IF EXISTS(SELECT FROM pg_catalog.jsonb_object_keys(value) k WHERE k !~ '^[a-z][a-z0-9_]*$') THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        SELECT '{'||COALESCE(pg_catalog.string_agg(
            pg_catalog.to_jsonb(k)::text||':'||custody_admission.canonical_json_at_depth(v,depth+1),
            ',' ORDER BY k COLLATE "C"),'')||'}' INTO result
        FROM pg_catalog.jsonb_each(value) entry(k,v);
    WHEN 'array' THEN
        SELECT '['||COALESCE(pg_catalog.string_agg(
            custody_admission.canonical_json_at_depth(v,depth+1),',' ORDER BY n),'')||']' INTO result
        FROM pg_catalog.jsonb_array_elements(value) WITH ORDINALITY entry(v,n);
    WHEN 'number' THEN
        IF value::text !~ '^-?(0|[1-9][0-9]*)$' THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        result:=value::text;
    ELSE result:=value::text;
    END CASE;
    IF pg_catalog.octet_length(result)>8192 THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    RETURN result;
END;
$custody_json$;

CREATE OR REPLACE FUNCTION custody_admission.canonical_json(value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $custody_json_entry$
    SELECT custody_admission.canonical_json_at_depth(value,0)
$custody_json_entry$;

CREATE OR REPLACE FUNCTION custody_admission.binding_digest(value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,pg_temp AS $custody_digest$
    SELECT pg_catalog.encode(public.digest(
        pg_catalog.convert_to(custody_admission.canonical_json(value),'UTF8'),'sha256'
    ),'hex')
$custody_digest$;

CREATE OR REPLACE FUNCTION custody_admission.caller_role() RETURNS oid
LANGUAGE sql STABLE SET search_path=pg_catalog,pg_temp AS $custody_role$
    SELECT oid FROM pg_catalog.pg_roles
    WHERE rolname=CASE
        WHEN pg_catalog.current_setting('role',true) IN ('none','')
             OR pg_catalog.current_setting('role',true) IS NULL THEN session_user
        ELSE pg_catalog.current_setting('role',true)
    END
$custody_role$;

CREATE OR REPLACE FUNCTION custody_admission.host_only() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_host$
BEGIN
    IF COALESCE(pg_catalog.current_setting('role',true),'none') NOT IN ('none','')
       OR NOT EXISTS (
           SELECT FROM custody_admission.bootstrap_configuration b
           JOIN pg_catalog.pg_roles r ON r.oid=b.bootstrap_owner_oid
           WHERE b.singleton AND session_user IN (r.rolname,b.connecting_role)
       ) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
END;
$custody_host$;

CREATE OR REPLACE FUNCTION custody_admission.install_interface() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_install$
DECLARE owner_name name; statement text; prior_identity jsonb;
BEGIN
    SELECT r.rolname INTO owner_name
    FROM custody_admission.bootstrap_configuration b
    JOIN pg_catalog.pg_roles r ON r.oid=b.bootstrap_owner_oid WHERE b.singleton;
    IF owner_name IS NULL OR current_user<>owner_name THEN
        RAISE EXCEPTION 'custody untrusted installer' USING ERRCODE='42501';
    END IF;
    PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended('butlers:core_260:endpoint_custody',0)
    );
    SELECT schema_identity INTO prior_identity
      FROM custody_admission.bootstrap_configuration WHERE singleton FOR UPDATE;
    IF prior_identity IS NULL THEN
        -- First provisioning may capture only tables this fixed installer
        -- creates itself. Existing unrecorded relations are not adopted.
        IF EXISTS(SELECT FROM pg_catalog.pg_class r
            JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
            WHERE n.nspname='custody_admission' AND r.relkind IN ('r','p','v','m','f')
              AND r.relname<>'bootstrap_configuration')
           OR pg_catalog.to_regclass('public.custody_holds') IS NOT NULL THEN
            RAISE EXCEPTION 'custody unrecorded schema refused' USING ERRCODE='42501';
        END IF;
    ELSIF prior_identity IS DISTINCT FROM custody_admission.schema_identity() THEN
        RAISE EXCEPTION 'custody installed schema drift' USING ERRCODE='42501';
    END IF;
    EXECUTE $custody_tables$
        CREATE TABLE IF NOT EXISTS custody_admission.control (
            singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
            control_epoch bigint NOT NULL DEFAULT 1 CHECK(control_epoch>0),
            restore_epoch uuid NOT NULL DEFAULT pg_catalog.gen_random_uuid(),
            database_oid oid NOT NULL,
            admission_state text NOT NULL CHECK(admission_state IN ('ready','unavailable','revoked')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        INSERT INTO custody_admission.control(singleton,database_oid,admission_state)
        SELECT true,oid,'ready' FROM pg_catalog.pg_database WHERE datname=current_database()
        ON CONFLICT(singleton) DO NOTHING;
        CREATE TABLE IF NOT EXISTS custody_admission.anchor_proposals (
            nonce uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            database_oid oid NOT NULL, login_oid oid NOT NULL, role_oid oid NOT NULL,
            backend_pid integer NOT NULL, backend_start timestamptz NOT NULL,
            expires_at timestamptz NOT NULL, consumed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(database_oid,backend_pid,backend_start)
        );
        CREATE TABLE IF NOT EXISTS custody_admission.processes (
            process_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            logical_actor text NOT NULL, database_oid oid NOT NULL,
            login_oid oid NOT NULL, role_oid oid NOT NULL,
            anchor_pid integer NOT NULL, anchor_backend_start timestamptz NOT NULL,
            adapter_incarnation uuid NOT NULL, manifest_digest text NOT NULL,
            source_kinds jsonb NOT NULL, operations jsonb NOT NULL, audiences jsonb NOT NULL,
            control_epoch bigint NOT NULL, restore_epoch uuid NOT NULL,
            lease_expires_at timestamptz NOT NULL, revoked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(database_oid,anchor_pid,anchor_backend_start)
        );
        -- Actual INSERT evidence only: no row locator or recent timestamp can
        -- manufacture a birth. No payload, sender or business message is stored.
        CREATE TABLE IF NOT EXISTS custody_admission.accepted_births (
            record_id uuid PRIMARY KEY,
            received_at timestamptz NOT NULL,
            content_digest text NOT NULL CHECK(content_digest~'^[0-9a-f]{64}$'),
            first_process uuid NOT NULL REFERENCES custody_admission.processes(process_id),
            retired_at timestamptz,
            control_epoch bigint NOT NULL CHECK(control_epoch>0),
            restore_epoch uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS custody_admission.sources (
            source_ref uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            logical_actor text NOT NULL, source_family text NOT NULL,
            source_locator text NOT NULL, source_revision bigint NOT NULL CHECK(source_revision>0),
            projection jsonb NOT NULL, source_digest text NOT NULL,
            first_process uuid NOT NULL REFERENCES custody_admission.processes(process_id),
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(logical_actor,source_family,source_locator,source_revision)
        );
        -- Minimized current admission metadata; no raw channel or message bus.
        CREATE TABLE IF NOT EXISTS custody_admission.origin_bindings (
            origin_digest text PRIMARY KEY CHECK(origin_digest~'^[0-9a-f]{64}$'),
            source_ref uuid NOT NULL REFERENCES custody_admission.sources(source_ref),
            source_revision bigint NOT NULL CHECK(source_revision>0),
            source_digest text NOT NULL,
            binding_generation bigint NOT NULL DEFAULT 1 CHECK(binding_generation>0),
            owner_entity_id uuid,
            owner_birth timestamptz,
            expires_at timestamptz NOT NULL
        );
        CREATE TABLE IF NOT EXISTS custody_admission.source_origin_bindings (
            source_ref uuid PRIMARY KEY REFERENCES custody_admission.sources(source_ref),
            origin_digest text NOT NULL REFERENCES custody_admission.origin_bindings(origin_digest),
            origin_source_ref uuid NOT NULL REFERENCES custody_admission.sources(source_ref),
            origin_revision bigint NOT NULL CHECK(origin_revision>0),
            binding_generation bigint NOT NULL CHECK(binding_generation>0),
            owner_entity_id uuid NOT NULL,
            owner_birth timestamptz NOT NULL
        );
        CREATE TABLE IF NOT EXISTS custody_admission.source_associations (
            source_ref uuid NOT NULL REFERENCES custody_admission.sources(source_ref),
            process_id uuid NOT NULL REFERENCES custody_admission.processes(process_id),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(source_ref,process_id)
        );
        -- Private source reconciliation only; no business payload, callback
        -- token, route or cross-butler delivery message is carried here.
        CREATE TABLE IF NOT EXISTS custody_admission.accepted_work (
            record_id uuid PRIMARY KEY REFERENCES custody_admission.accepted_births(record_id),
            state text NOT NULL DEFAULT 'pending' CHECK(state IN (
                'pending','claimed','prepared','attempted','committed','unknown',
                'ignored','awaiting_selection','unavailable')),
            claim_ref uuid,
            claim_process uuid REFERENCES custody_admission.processes(process_id),
            lease_until timestamptz,
            source_ref uuid REFERENCES custody_admission.sources(source_ref),
            attempted_at timestamptz,
            finished_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX IF NOT EXISTS custody_accepted_work_pending
            ON custody_admission.accepted_work(created_at,record_id)
            WHERE state IN ('pending','claimed','prepared','attempted');
        CREATE TABLE IF NOT EXISTS custody_admission.calls (
            call_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            issuer_process uuid NOT NULL REFERENCES custody_admission.processes(process_id),
            destination_process uuid NOT NULL REFERENCES custody_admission.processes(process_id),
            source_ref uuid NOT NULL REFERENCES custody_admission.sources(source_ref),
            mint_request_id uuid NOT NULL, operation jsonb NOT NULL,
            operation_digest text NOT NULL, control_epoch bigint NOT NULL, restore_epoch uuid NOT NULL,
            expires_at timestamptz NOT NULL, challenge_ref uuid, challenge_expires_at timestamptz,
            state text NOT NULL CHECK(state IN ('minted','challenged','armed','committed','refused','unknown')),
            command_id uuid, result jsonb,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(issuer_process,source_ref,destination_process,mint_request_id)
        );
        CREATE TABLE IF NOT EXISTS custody_admission.connections (
            database_oid oid NOT NULL, backend_pid integer NOT NULL,
            backend_start timestamptz NOT NULL, login_oid oid NOT NULL, role_oid oid NOT NULL,
            acquisition_generation bigint NOT NULL DEFAULT 1,
            nonce uuid NOT NULL DEFAULT pg_catalog.gen_random_uuid(),
            process_id uuid REFERENCES custody_admission.processes(process_id),
            state text NOT NULL CHECK(state IN ('proposed','bound','released','revoked')),
            bound_until timestamptz NOT NULL, finished_at timestamptz,
            verified_call_id uuid REFERENCES custody_admission.calls(call_id),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(database_oid,backend_pid,backend_start), UNIQUE(nonce)
        );
        CREATE TABLE IF NOT EXISTS custody_admission.targets (
            target_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            owner_entity_id uuid NOT NULL, target_kind text NOT NULL CHECK(target_kind IN ('endpoint','account')),
            binding_digest text NOT NULL CHECK(binding_digest~'^[0-9a-f]{64}$'),
            binding_version bigint NOT NULL CHECK(binding_version>0),
            generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0),
            source_ref uuid NOT NULL REFERENCES custody_admission.sources(source_ref),
            ordering_key text NOT NULL UNIQUE,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS custody_admission.commands (
            command_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            kind text NOT NULL CHECK(kind IN ('browser','host','security_answer','endpoint_lock')),
            operation text NOT NULL CHECK(operation IN ('hold','release','replaced','yes','no','revoke_sessions','eligibility')),
            selection jsonb NOT NULL, selection_digest text NOT NULL,
            proof jsonb NOT NULL, source_ref uuid REFERENCES custody_admission.sources(source_ref),
            control_epoch bigint NOT NULL, restore_epoch uuid NOT NULL,
            expires_at timestamptz NOT NULL,
            state text NOT NULL DEFAULT 'prepared' CHECK(state IN ('prepared','committed','refused','unknown')),
            result jsonb, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS custody_admission.questions (
            question_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            source_ref uuid NOT NULL UNIQUE REFERENCES custody_admission.sources(source_ref),
            event_ref uuid NOT NULL, recipient_digest text NOT NULL,
            target_set jsonb NOT NULL, target_set_version bigint NOT NULL,
            yes_digest text NOT NULL, no_digest text NOT NULL,
            expires_at timestamptz NOT NULL,
            decision text NOT NULL DEFAULT 'pending' CHECK(decision IN ('pending','yes','no','expired')),
            decision_command_id uuid REFERENCES custody_admission.commands(command_id),
            presentation_ref text, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS public.custody_holds (
            episode_id uuid PRIMARY KEY DEFAULT pg_catalog.gen_random_uuid(),
            target_id uuid NOT NULL REFERENCES custody_admission.targets(target_id),
            generation bigint NOT NULL CHECK(generation>0),
            reason text NOT NULL CHECK(reason IN ('lost','stolen','replaced','disowned_access')),
            source_command_id uuid NOT NULL REFERENCES custody_admission.commands(command_id),
            held_from timestamptz NOT NULL DEFAULT clock_timestamp(),
            observed_incident_at timestamptz, case_id uuid,
            released_at timestamptz, disposition text CHECK(disposition IN ('released','replaced')),
            release_command_id uuid REFERENCES custody_admission.commands(command_id),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            UNIQUE(target_id,generation),
            CHECK((released_at IS NULL)=(disposition IS NULL))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS custody_one_active_target
            ON public.custody_holds(target_id) WHERE released_at IS NULL;
        CREATE TABLE IF NOT EXISTS custody_admission.receipts (
            command_id uuid PRIMARY KEY REFERENCES custody_admission.commands(command_id),
            result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE TABLE IF NOT EXISTS custody_admission.case_scopes (
            case_id uuid PRIMARY KEY REFERENCES public.fleet_cases(id),
            kind text NOT NULL CHECK(kind IN ('lost_device','account_security')),
            owner_entity_id uuid NOT NULL,
            membership_version bigint NOT NULL DEFAULT 1 CHECK(membership_version>0)
        );
        CREATE TABLE IF NOT EXISTS custody_admission.case_members (
            case_id uuid NOT NULL REFERENCES custody_admission.case_scopes(case_id),
            target_id uuid NOT NULL,
            generation bigint NOT NULL CHECK(generation>0),
            membership_version bigint NOT NULL CHECK(membership_version>0),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(case_id,target_id,generation),
            FOREIGN KEY(target_id,generation) REFERENCES public.custody_holds(target_id,generation)
        );
        CREATE TABLE IF NOT EXISTS custody_admission.effects (
            actor text NOT NULL, namespace text NOT NULL, effect_id uuid NOT NULL,
            source_ref uuid NOT NULL REFERENCES custody_admission.sources(source_ref),
            binding_digest text NOT NULL, started_at timestamptz NOT NULL,
            result jsonb, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(actor,namespace,effect_id)
        );
        CREATE TABLE IF NOT EXISTS custody_admission.provider_inventory (
            owner_entity_id uuid NOT NULL, provider text NOT NULL,
            version bigint NOT NULL CHECK(version>0),
            declared_contributors jsonb NOT NULL, current_sources jsonb NOT NULL DEFAULT '{}',
            complete boolean NOT NULL DEFAULT false,
            members jsonb NOT NULL DEFAULT '[]',
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY(owner_entity_id,provider)
        );
    $custody_tables$;
    EXECUTE 'ALTER TABLE public.custody_holds ENABLE ROW LEVEL SECURITY';
    EXECUTE 'ALTER TABLE public.custody_holds FORCE ROW LEVEL SECURITY';
    EXECUTE 'DROP POLICY IF EXISTS custody_bootstrap_engine ON public.custody_holds';
    EXECUTE pg_catalog.format(
        'CREATE POLICY custody_bootstrap_engine ON public.custody_holds '
        'USING(current_user=%L) WITH CHECK(current_user=%L)', owner_name, owner_name
    );
    IF prior_identity IS NULL THEN
        UPDATE custody_admission.bootstrap_configuration
          SET schema_identity=custody_admission.schema_identity() WHERE singleton;
    ELSIF prior_identity IS DISTINCT FROM custody_admission.schema_identity() THEN
        RAISE EXCEPTION 'custody installed schema drift' USING ERRCODE='42501';
    END IF;
    PERFORM custody_admission.install_dashboard_interface();
    PERFORM custody_admission.install_accepted_birth();
    PERFORM custody_admission.finalize_interface();
END;
$custody_install$;

-- Private helpers never receive ordinary EXEC. The active SET ROLE identity is
-- captured explicitly; current_user inside a definer is the bootstrap owner.
CREATE OR REPLACE FUNCTION custody_admission.closed(
    value jsonb, required text[], optional text[] DEFAULT ARRAY[]::text[]
) RETURNS void LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $custody_closed$
BEGIN
    IF pg_catalog.jsonb_typeof(value) IS DISTINCT FROM 'object'
       OR (value ?& required) IS DISTINCT FROM true
       OR EXISTS(SELECT FROM pg_catalog.jsonb_object_keys(value) k
                 WHERE NOT k=ANY(required||optional)) THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    PERFORM custody_admission.canonical_json(value);
END;
$custody_closed$;

CREATE OR REPLACE FUNCTION custody_admission.current_control() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_control$
DECLARE c record;
BEGIN
    -- This is also called by outer verification and writer binding. Establish
    -- auth-before-control here, not only in the later business helper; otherwise
    -- an outer guard can deadlock a revoker that already owns the singleton.
    IF pg_catalog.to_regclass('dashboard_auth.instance') IS NOT NULL THEN
        PERFORM FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
    END IF;
    SELECT * INTO c FROM custody_admission.control WHERE singleton FOR UPDATE;
    IF NOT FOUND OR c.admission_state<>'ready' OR c.database_oid<>(
        SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database()
    ) THEN RAISE EXCEPTION 'custody unavailable' USING ERRCODE='42501'; END IF;
    RETURN pg_catalog.jsonb_build_object('control_epoch',c.control_epoch,'restore_epoch',c.restore_epoch);
END;
$custody_control$;

CREATE OR REPLACE FUNCTION custody_admission.process_current(process uuid) RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_live$
BEGIN
    -- The bootstrap stages functions before ordinary core replay creates this
    -- feature's tables. PL/pgSQL resolves these fixed qualified relations on
    -- execution; a SQL-language body would fail during fresh bootstrap.
    RETURN EXISTS(
        SELECT FROM custody_admission.processes p
        JOIN custody_admission.control c ON c.singleton
        JOIN pg_catalog.pg_stat_activity a ON a.pid=p.anchor_pid
          AND a.backend_start=p.anchor_backend_start AND a.datid=p.database_oid
          AND a.usesysid=p.login_oid
        WHERE p.process_id=process AND p.revoked_at IS NULL
          AND p.lease_expires_at>pg_catalog.clock_timestamp()
          AND c.admission_state='ready' AND p.control_epoch=c.control_epoch
          AND p.restore_epoch=c.restore_epoch
          AND c.database_oid=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
    );
END;
$custody_live$;

CREATE OR REPLACE FUNCTION custody_admission.anchor() RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_anchor$
DECLARE result uuid;
BEGIN
    PERFORM custody_admission.current_control();
    SELECT p.process_id INTO result FROM custody_admission.processes p
    JOIN pg_catalog.pg_stat_activity a ON a.pid=pg_catalog.pg_backend_pid()
      AND a.backend_start=p.anchor_backend_start AND a.datid=p.database_oid
      AND a.usesysid=p.login_oid
    WHERE p.anchor_pid=a.pid AND p.role_oid=custody_admission.caller_role()
      AND custody_admission.process_current(p.process_id);
    IF result IS NULL THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    RETURN result;
END;
$custody_anchor$;

CREATE OR REPLACE FUNCTION custody_admission.writer() RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_writer$
DECLARE result uuid;
BEGIN
    PERFORM custody_admission.current_control();
    SELECT c.process_id INTO result FROM custody_admission.connections c
    JOIN pg_catalog.pg_stat_activity a ON a.pid=pg_catalog.pg_backend_pid()
      AND a.backend_start=c.backend_start AND a.datid=c.database_oid AND a.usesysid=c.login_oid
    WHERE c.backend_pid=a.pid AND c.role_oid=custody_admission.caller_role()
      AND c.state='bound' AND c.finished_at IS NOT NULL
      AND c.bound_until>pg_catalog.clock_timestamp()
      AND custody_admission.process_current(c.process_id) FOR UPDATE OF c;
    IF result IS NULL THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    RETURN result;
END;
$custody_writer$;

CREATE OR REPLACE FUNCTION custody_admission.require_verified_call(call_ref uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_verified_writer$
DECLARE process_id uuid;
BEGIN
    process_id:=custody_admission.writer();
    IF NOT EXISTS(SELECT FROM custody_admission.connections connection
        JOIN pg_catalog.pg_stat_activity backend ON backend.pid=connection.backend_pid
          AND backend.backend_start=connection.backend_start
          AND backend.datid=connection.database_oid AND backend.usesysid=connection.login_oid
        WHERE backend.pid=pg_catalog.pg_backend_pid() AND connection.process_id=process_id
          AND connection.role_oid=custody_admission.caller_role()
          AND connection.state='bound' AND connection.finished_at IS NOT NULL
          AND connection.bound_until>pg_catalog.clock_timestamp()
          AND connection.verified_call_id=require_verified_call.call_ref) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
END;
$custody_verified_writer$;

CREATE OR REPLACE FUNCTION custody_admission.host_enroll(manifest jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_enroll$
DECLARE proposal record; c jsonb; result uuid; role_name text; family text;
BEGIN
    PERFORM custody_admission.host_only();
    PERFORM custody_admission.closed(manifest,ARRAY[
        'nonce','logical_actor','role','adapter_incarnation','config_digest',
        'source_kinds','operations','audiences'
    ]);
    IF pg_catalog.jsonb_typeof(manifest->'logical_actor') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(manifest->'role') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(manifest->'config_digest') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(manifest->'nonce') IS DISTINCT FROM 'string'
       OR pg_catalog.jsonb_typeof(manifest->'adapter_incarnation') IS DISTINCT FROM 'string'
       OR manifest->>'nonce' IS DISTINCT FROM ((manifest->>'nonce')::uuid)::text
       OR manifest->>'adapter_incarnation' IS DISTINCT FROM ((manifest->>'adapter_incarnation')::uuid)::text
       OR pg_catalog.jsonb_typeof(manifest->'source_kinds') IS DISTINCT FROM 'array'
       OR pg_catalog.jsonb_typeof(manifest->'operations') IS DISTINCT FROM 'array'
       OR pg_catalog.jsonb_typeof(manifest->'audiences') IS DISTINCT FROM 'array'
       OR manifest->>'logical_actor' !~ '^[a-z][a-z0-9_-]{0,63}$'
       OR manifest->>'config_digest' !~ '^[0-9a-f]{64}$'
       OR pg_catalog.jsonb_array_length(manifest->'source_kinds')>16
       OR pg_catalog.jsonb_array_length(manifest->'operations')>16
       OR pg_catalog.jsonb_array_length(manifest->'audiences')>32 THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    IF EXISTS(SELECT FROM pg_catalog.jsonb_array_elements(manifest->'source_kinds') value
           WHERE pg_catalog.jsonb_typeof(value) IS DISTINCT FROM 'string')
       OR EXISTS(SELECT FROM pg_catalog.jsonb_array_elements(manifest->'operations') value
           WHERE pg_catalog.jsonb_typeof(value) IS DISTINCT FROM 'string')
       OR EXISTS(SELECT FROM pg_catalog.jsonb_array_elements(manifest->'audiences') value
           WHERE pg_catalog.jsonb_typeof(value) IS DISTINCT FROM 'string') THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    FOR family IN SELECT pg_catalog.jsonb_array_elements_text(manifest->'source_kinds') LOOP
        IF family<>ALL(ARRAY['accepted_ingress','provider_callback','owner_command',
           'host_command','scheduled_task','deferred_notice','domain_evidence','provider_inventory']) THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
    END LOOP;
    IF EXISTS(SELECT FROM pg_catalog.jsonb_array_elements_text(manifest->'operations') op
       WHERE op<>ALL(ARRAY['hold','release','replaced','yes','no','revoke_sessions',
                           'write','provider_start','eligibility','question']))
       OR EXISTS(SELECT FROM pg_catalog.jsonb_array_elements_text(manifest->'audiences') actor
          WHERE actor !~ '^[a-z][a-z0-9_-]{0,63}$') THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    role_name:=manifest->>'role';
    IF NOT (manifest->>'logical_actor' NOT IN ('dashboard','host-switchboard')
            AND manifest->>'logical_actor' NOT LIKE 'connector-%'
            AND role_name ~ '^butler_[A-Za-z_][A-Za-z0-9_]*_rw$'
        OR role_name ~ '^butler_[A-Za-z_][A-Za-z0-9_]*_rw$'
            AND manifest->>'logical_actor'='host-switchboard'
        OR role_name='dashboard_auth_api' AND manifest->>'logical_actor'='dashboard'
        OR role_name='connector_writer' AND manifest->>'logical_actor' LIKE 'connector-%') THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    -- Empty operation scope is a fixed canonical-writer allocation, not a
    -- receiving incarnation. It cannot mint ingress or command evidence.
    IF manifest->'operations'='[]'::jsonb AND (
        manifest->>'logical_actor'<>'relationship' OR role_name<>'butler_relationship_rw'
        OR manifest->'source_kinds'<>'["domain_evidence"]'::jsonb
        OR manifest->'audiences'<>'[]'::jsonb
    ) THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    IF manifest->>'logical_actor'='host-switchboard' AND (
        manifest->'source_kinds'<>'["host_command"]'::jsonb
        OR manifest->'audiences'<>'["switchboard"]'::jsonb
        OR NOT manifest->'operations' <@ '["hold","release","replaced","revoke_sessions","eligibility"]'::jsonb
    ) THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    IF manifest->>'logical_actor'='switchboard' THEN
        PERFORM custody_admission.install_accepted_birth();
    END IF;
    c:=custody_admission.current_control();
    SELECT * INTO proposal FROM custody_admission.anchor_proposals
      WHERE nonce=(manifest->>'nonce')::uuid FOR UPDATE;
    IF NOT FOUND OR proposal.consumed_at IS NOT NULL
       OR proposal.expires_at<=pg_catalog.clock_timestamp()
       OR proposal.role_oid IS DISTINCT FROM (SELECT oid FROM pg_catalog.pg_roles WHERE rolname=role_name)
       OR NOT EXISTS(SELECT FROM pg_catalog.pg_stat_activity a
          WHERE a.pid=proposal.backend_pid AND a.backend_start=proposal.backend_start
            AND a.datid=proposal.database_oid AND a.usesysid=proposal.login_oid) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    IF manifest->>'logical_actor'='dashboard' THEN
      IF pg_catalog.to_regnamespace('dashboard_auth') IS NULL THEN
          RAISE EXCEPTION 'custody unavailable' USING ERRCODE='42501';
      END IF;
      IF EXISTS(
        WITH RECURSIVE reachable(role_oid) AS (
            SELECT proposal.login_oid
            UNION SELECT membership.roleid FROM pg_catalog.pg_auth_members membership
              JOIN reachable ON membership.member=reachable.role_oid
        )
        SELECT FROM reachable JOIN pg_catalog.pg_roles login ON login.oid=reachable.role_oid
        WHERE login.rolsuper OR login.rolcreaterole OR login.rolcreatedb
           OR login.rolreplication OR login.rolbypassrls
           OR login.oid=(SELECT datdba FROM pg_catalog.pg_database WHERE datname=pg_catalog.current_database())
           OR login.rolname NOT IN ('dashboard_auth_api',
               (SELECT rolname FROM pg_catalog.pg_roles WHERE oid=proposal.login_oid))
           OR pg_catalog.has_function_privilege(login.oid,
               'dashboard_auth.host(text,jsonb)'::pg_catalog.regprocedure,'EXECUTE')
           OR pg_catalog.has_function_privilege(login.oid,
               'custody_admission.host_enroll(jsonb)'::pg_catalog.regprocedure,'EXECUTE')
           OR EXISTS(SELECT FROM pg_catalog.pg_namespace namespace
               WHERE namespace.nspname IN ('dashboard_auth','custody_admission')
                 AND (namespace.nspowner=login.oid
                      OR pg_catalog.has_schema_privilege(login.oid,namespace.oid,'CREATE')))
           OR EXISTS(SELECT FROM pg_catalog.pg_class relation
               JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace
               WHERE namespace.nspname IN ('dashboard_auth','custody_admission')
                 AND relation.relkind IN ('r','p','v','m','f')
                 AND (relation.relowner=login.oid
                      OR pg_catalog.has_table_privilege(login.oid,relation.oid,
                         'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                      OR pg_catalog.has_any_column_privilege(login.oid,relation.oid,
                         'SELECT,INSERT,UPDATE,REFERENCES')))
      ) THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    END IF;
    INSERT INTO custody_admission.processes(logical_actor,database_oid,login_oid,role_oid,
        anchor_pid,anchor_backend_start,adapter_incarnation,manifest_digest,source_kinds,
        operations,audiences,control_epoch,restore_epoch,lease_expires_at)
    VALUES(manifest->>'logical_actor',proposal.database_oid,proposal.login_oid,proposal.role_oid,
        proposal.backend_pid,proposal.backend_start,(manifest->>'adapter_incarnation')::uuid,
        custody_admission.binding_digest(manifest),manifest->'source_kinds',manifest->'operations',
        manifest->'audiences',(c->>'control_epoch')::bigint,(c->>'restore_epoch')::uuid,
        pg_catalog.clock_timestamp()+interval '30 seconds') RETURNING process_id INTO result;
    UPDATE custody_admission.anchor_proposals SET consumed_at=pg_catalog.clock_timestamp()
      WHERE nonce=proposal.nonce;
    RETURN pg_catalog.jsonb_build_object('process_id',result,'control_epoch',c->'control_epoch',
        'restore_epoch',c->'restore_epoch','lease_seconds',30,
        'login_oid',proposal.login_oid,'database_oid',proposal.database_oid,
        'anchor_pid',proposal.backend_pid,
        'anchor_backend_start',pg_catalog.to_char(proposal.backend_start AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
END;
$custody_enroll$;

CREATE OR REPLACE FUNCTION custody_admission.host_revoke(
    process_id uuid, expected_control_epoch bigint
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_revoke$
DECLARE c jsonb;
BEGIN
    PERFORM custody_admission.host_only();
    c:=custody_admission.current_control();
    IF (c->>'control_epoch')::bigint<>expected_control_epoch THEN
        RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
    END IF;
    UPDATE custody_admission.processes p SET revoked_at=COALESCE(p.revoked_at,pg_catalog.clock_timestamp())
      WHERE p.process_id=host_revoke.process_id;
    UPDATE custody_admission.connections w SET state='revoked' WHERE w.process_id=host_revoke.process_id;
    RETURN pg_catalog.jsonb_build_object('revoked',true);
END;
$custody_revoke$;

CREATE OR REPLACE FUNCTION custody_admission.host_revoke_control(expected_control_epoch bigint)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_revoke_all$
DECLARE c jsonb;
BEGIN
    PERFORM custody_admission.host_only();
    -- The auth singleton always precedes global custody control, including this
    -- host fence, so a once-prepared browser command cannot win on an old pool verdict.
    IF pg_catalog.to_regclass('dashboard_auth.instance') IS NOT NULL THEN
        PERFORM FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
    END IF;
    c:=custody_admission.current_control();
    IF (c->>'control_epoch')::bigint<>expected_control_epoch THEN
        RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
    END IF;
    UPDATE custody_admission.control SET control_epoch=control_epoch+1 WHERE singleton;
    UPDATE custody_admission.processes SET revoked_at=COALESCE(revoked_at,pg_catalog.clock_timestamp());
    UPDATE custody_admission.connections SET state='revoked';
    RETURN pg_catalog.jsonb_build_object('control_epoch',expected_control_epoch+1);
END;
$custody_revoke_all$;

CREATE OR REPLACE FUNCTION custody_admission.require_origin_current(expected_source_ref uuid)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_origin_current$
DECLARE source record; captured record; current_binding record; live_owner record;
    original_observation record; current_observation record;
BEGIN
    SELECT * INTO source FROM custody_admission.sources WHERE source_ref=expected_source_ref;
    IF NOT FOUND THEN RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000'; END IF;
    IF source.source_family='accepted_ingress' THEN
        -- Receiving writers read only this protected metadata, never peer SQL.
        -- Retirement and final admission serialize on the SAME frozen birth.
        PERFORM FROM custody_admission.accepted_births birth
          JOIN custody_admission.control control ON control.singleton
          WHERE birth.record_id=pg_catalog.substring(source.source_locator,7)::uuid
            AND birth.retired_at IS NULL AND birth.content_digest=source.projection->>'content_digest'
            AND birth.control_epoch=control.control_epoch AND birth.restore_epoch=control.restore_epoch
          FOR UPDATE OF birth;
        IF NOT FOUND THEN RAISE EXCEPTION 'custody stale accepted source' USING ERRCODE='42501'; END IF;
    END IF;
    IF source.projection->>'owner_entity_id' IS NULL
       OR NOT (source.source_family='accepted_ingress' OR source.projection ? 'answer') THEN RETURN; END IF;
    SELECT * INTO captured FROM custody_admission.source_origin_bindings
      WHERE source_ref=expected_source_ref;
    IF NOT FOUND THEN RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000'; END IF;
    SELECT * INTO current_binding FROM custody_admission.origin_bindings
      WHERE origin_digest=captured.origin_digest FOR UPDATE;
    IF NOT FOUND OR current_binding.expires_at<=pg_catalog.clock_timestamp()
       OR current_binding.source_revision<captured.origin_revision
       OR current_binding.binding_generation<>captured.binding_generation
       OR current_binding.owner_entity_id IS DISTINCT FROM captured.owner_entity_id
       OR current_binding.owner_birth IS DISTINCT FROM captured.owner_birth
       OR source.projection->>'owner_entity_id' IS DISTINCT FROM captured.owner_entity_id::text THEN
        RAISE EXCEPTION 'custody stale origin' USING ERRCODE='42501';
    END IF;
    -- A fresh canonical observation can renew the finite liveness witness
    -- without changing the underlying binding. The accepted report retains
    -- its original capture and original expiry; it is never filled/relinked
    -- from today's pointer. Compare actual immutable canonical content, not
    -- the renewable observation's source identity or lease timestamps.
    SELECT * INTO original_observation FROM custody_admission.sources
      WHERE source_ref=captured.origin_source_ref;
    IF NOT FOUND OR original_observation.logical_actor<>'relationship'
       OR original_observation.source_family<>'domain_evidence'
       OR original_observation.projection->>'intent' IS DISTINCT FROM 'identity_binding'
       OR original_observation.source_revision<>captured.origin_revision THEN
        RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
    END IF;
    SELECT * INTO current_observation FROM custody_admission.sources
      WHERE source_ref=current_binding.source_ref;
    IF NOT FOUND OR current_observation.logical_actor<>'relationship'
       OR current_observation.source_family<>'domain_evidence'
       OR current_observation.projection->>'intent' IS DISTINCT FROM 'identity_binding'
       OR current_observation.expires_at IS DISTINCT FROM current_binding.expires_at
       OR current_observation.source_digest IS DISTINCT FROM current_binding.source_digest
       OR current_observation.projection->>'origin_digest' IS DISTINCT FROM captured.origin_digest
       OR current_observation.projection->>'content_digest'
          IS DISTINCT FROM original_observation.projection->>'content_digest' THEN
        RAISE EXCEPTION 'custody stale origin' USING ERRCODE='42501';
    END IF;
    SELECT created_at,roles,metadata INTO live_owner FROM public.entities
      WHERE id=captured.owner_entity_id FOR UPDATE;
    IF NOT FOUND OR live_owner.created_at IS DISTINCT FROM captured.owner_birth
       OR NOT 'owner'=ANY(COALESCE(live_owner.roles,ARRAY[]::text[]))
       OR live_owner.metadata->>'merged_into' IS NOT NULL
       OR live_owner.metadata->>'deleted_at' IS NOT NULL
       OR COALESCE((live_owner.metadata->>'unidentified')='true',false) THEN
        RAISE EXCEPTION 'custody stale owner' USING ERRCODE='42501';
    END IF;
END;
$custody_origin_current$;

-- Installed only by the existing trusted installer/startup. Ordinary runtime
-- roles have no EXECUTE on either private helper and cannot own this trigger.
CREATE OR REPLACE FUNCTION custody_admission.accepted_birth() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_accepted_birth$
#variable_conflict use_variable
DECLARE process_id uuid; process record; c jsonb; content jsonb;
BEGIN
    -- Preserve only an unchanged original content/lifetime. Lifecycle/status
    -- updates are ordinary processing, but deletion or canonical-body mutation
    -- retires this birth one-way, including unbound legacy/admin writers.
    -- This metadata lock orders before either transaction COMMIT. No global
    -- auth/control lock is acquired after an unbound writer's domain row lock.
    IF TG_OP='DELETE' THEN
        UPDATE custody_admission.accepted_births
          SET retired_at=COALESCE(retired_at,pg_catalog.clock_timestamp())
          WHERE record_id=OLD.id AND received_at=OLD.received_at;
        RETURN OLD;
    ELSIF TG_OP='UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.received_at IS DISTINCT FROM OLD.received_at
           OR NEW.request_context IS DISTINCT FROM OLD.request_context
           OR NEW.raw_payload IS DISTINCT FROM OLD.raw_payload
           OR NEW.normalized_text IS DISTINCT FROM OLD.normalized_text
           OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
           OR NEW.direction IS DISTINCT FROM OLD.direction THEN
            UPDATE custody_admission.accepted_births
              SET retired_at=COALESCE(retired_at,pg_catalog.clock_timestamp())
              WHERE record_id=OLD.id AND received_at=OLD.received_at;
        END IF;
        RETURN NEW;
    END IF;
    -- Legacy unbound ingestion is still accepted, but it has NO custody birth.
    -- A stale/released binding cannot turn a legacy row into a fresh source.
    IF NOT EXISTS(SELECT FROM custody_admission.connections connection
        JOIN pg_catalog.pg_stat_activity backend ON backend.pid=connection.backend_pid
          AND backend.backend_start=connection.backend_start
          AND backend.datid=connection.database_oid AND backend.usesysid=connection.login_oid
        WHERE backend.pid=pg_catalog.pg_backend_pid() AND connection.state='bound'
          AND connection.role_oid=custody_admission.caller_role()) THEN
        RETURN NEW;
    END IF;
    process_id:=custody_admission.writer();
    SELECT * INTO process FROM custody_admission.processes
      WHERE processes.process_id=accepted_birth.process_id;
    IF process.logical_actor<>'switchboard' OR NOT process.source_kinds ? 'accepted_ingress'
       OR TG_OP<>'INSERT' OR TG_LEVEL<>'ROW' OR TG_WHEN<>'AFTER' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    IF NEW.schema_version IS DISTINCT FROM 'message_inbox.v2'
       OR NEW.direction IS DISTINCT FROM 'inbound'
       OR pg_catalog.jsonb_typeof(NEW.request_context) IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(NEW.raw_payload) IS DISTINCT FROM 'object'
       OR NEW.request_context->>'request_id' IS DISTINCT FROM NEW.id::text
       OR NEW.request_context->>'payload_type'='conversation_history'
       OR COALESCE(NEW.request_context->'source_sender_identities','[]'::jsonb)<>'[]'::jsonb
       OR NEW.normalized_text IS NULL THEN
        RETURN NEW; -- Unsupported/history input never acquires a custody source.
    END IF;
    c:=custody_admission.current_control(); -- Already locked BEFORE native locks.
    content:=pg_catalog.jsonb_build_object(
        'record_id',NEW.id,'received_at',pg_catalog.to_char(NEW.received_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'text_sha256',pg_catalog.encode(public.digest(pg_catalog.convert_to(NEW.normalized_text,'UTF8'),'sha256'),'hex'),
        'context_sha256',pg_catalog.encode(public.digest(pg_catalog.convert_to(NEW.request_context::text,'UTF8'),'sha256'),'hex'),
        'payload_sha256',pg_catalog.encode(public.digest(pg_catalog.convert_to(NEW.raw_payload::text,'UTF8'),'sha256'),'hex'));
    INSERT INTO custody_admission.accepted_births(
        record_id,received_at,content_digest,first_process,control_epoch,restore_epoch)
    VALUES(NEW.id,NEW.received_at,custody_admission.binding_digest(content),process_id,
        (c->>'control_epoch')::bigint,(c->>'restore_epoch')::uuid);
    INSERT INTO custody_admission.accepted_work(record_id) VALUES(NEW.id);
    -- No ON CONFLICT update: delete/recreate or another physical row cannot
    -- refill a frozen birth. Transaction rollback removes BOTH rows together.
    RETURN NEW;
END;
$custody_accepted_birth$;

CREATE OR REPLACE FUNCTION custody_admission.accepted_work(action text,payload jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_work$
#variable_conflict use_variable
DECLARE c jsonb; process record; work record; birth record; source record;
    reply text; process_id uuid;
BEGIN
    PERFORM custody_admission.host_only();
    IF action='claim' THEN
        PERFORM custody_admission.closed(payload,ARRAY['process_id']);
    ELSIF action='prepared' THEN
        PERFORM custody_admission.closed(payload,ARRAY['process_id','record_id','claim_ref','source_ref']);
    ELSIF action IN ('attempt','finish','ignore','selection','unavailable') THEN
        PERFORM custody_admission.closed(payload,ARRAY['process_id','record_id','claim_ref']);
    ELSE RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023'; END IF;
    process_id:=(payload->>'process_id')::uuid;
    c:=custody_admission.current_control();
    SELECT * INTO process FROM custody_admission.processes
      WHERE processes.process_id=accepted_work.process_id;
    IF NOT FOUND OR process.logical_actor<>'switchboard'
       OR NOT process.source_kinds ? 'accepted_ingress' OR NOT process.operations ? 'hold'
       OR NOT custody_admission.process_current(process_id) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    IF action='claim' THEN
        SELECT * INTO work FROM custody_admission.accepted_work candidate
          WHERE candidate.state='pending'
            OR candidate.state IN ('claimed','prepared','attempted') AND (
                candidate.lease_until<=pg_catalog.clock_timestamp()
                OR NOT custody_admission.process_current(candidate.claim_process))
          ORDER BY candidate.created_at,candidate.record_id LIMIT 1 FOR UPDATE;
        IF NOT FOUND THEN RETURN pg_catalog.jsonb_build_object('status','idle'); END IF;
        IF work.state='attempted' THEN
            -- A prior dispatcher may have crossed the network boundary. A
            -- committed owning receipt is proof; absence is UNKNOWN, never a
            -- resend permit. No source/generation is reminted on restart.
            UPDATE custody_admission.accepted_work SET
                state=CASE WHEN EXISTS(SELECT FROM custody_admission.receipts receipt
                    WHERE receipt.command_id=work.source_ref) THEN 'committed' ELSE 'unknown' END,
                finished_at=pg_catalog.clock_timestamp()
              WHERE record_id=work.record_id;
            RETURN pg_catalog.jsonb_build_object('status','reconciled');
        END IF;
        SELECT * INTO birth FROM custody_admission.accepted_births WHERE record_id=work.record_id;
        IF NOT FOUND OR birth.retired_at IS NOT NULL
           OR birth.control_epoch<>(c->>'control_epoch')::bigint
           OR birth.restore_epoch<>(c->>'restore_epoch')::uuid
           OR birth.received_at+interval '5 minutes'<=pg_catalog.clock_timestamp() THEN
            UPDATE custody_admission.accepted_work SET state='unavailable',
                finished_at=pg_catalog.clock_timestamp() WHERE record_id=work.record_id;
            RETURN pg_catalog.jsonb_build_object('status','reconciled');
        END IF;
        UPDATE custody_admission.accepted_work SET state='claimed',
            claim_ref=pg_catalog.gen_random_uuid(),claim_process=process_id,
            lease_until=pg_catalog.clock_timestamp()+interval '30 seconds'
          WHERE record_id=work.record_id RETURNING * INTO work;
        RETURN pg_catalog.jsonb_build_object('status','claimed','record_id',work.record_id,
            'claim_ref',work.claim_ref,'source_ref',work.source_ref);
    END IF;
    SELECT * INTO work FROM custody_admission.accepted_work
      WHERE record_id=(payload->>'record_id')::uuid FOR UPDATE;
    IF NOT FOUND OR work.claim_ref IS DISTINCT FROM (payload->>'claim_ref')::uuid
       OR work.claim_process IS DISTINCT FROM process_id
       OR work.lease_until<=pg_catalog.clock_timestamp()
       OR work.state NOT IN ('claimed','prepared','attempted') THEN
        RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
    END IF;
    IF action='prepared' THEN
        IF work.state<>'claimed' THEN RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001'; END IF;
        SELECT * INTO source FROM custody_admission.sources
          WHERE source_ref=(payload->>'source_ref')::uuid;
        IF NOT FOUND OR source.logical_actor<>'switchboard' OR source.source_family<>'accepted_ingress'
           OR source.source_locator<>'inbox:'||work.record_id::text
           OR source.projection->>'intent'<>'LOCK'
           OR source.expires_at<=pg_catalog.clock_timestamp()
           OR NOT EXISTS(SELECT FROM custody_admission.source_associations association
               WHERE association.source_ref=source.source_ref AND association.process_id=process_id)
           OR work.source_ref IS NOT NULL AND work.source_ref<>source.source_ref THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        UPDATE custody_admission.accepted_work SET state='prepared',source_ref=source.source_ref
          WHERE record_id=work.record_id;
        RETURN pg_catalog.jsonb_build_object('status','prepared','command_id',source.source_ref);
    ELSIF action='attempt' THEN
        IF work.state<>'prepared' OR work.source_ref IS NULL THEN
            RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001'; END IF;
        -- This marker commits BEFORE any registered MCP challenge/apply I/O.
        -- It is dispatch-attempt evidence, not a provider-start witness.
        UPDATE custody_admission.accepted_work SET state='attempted',
            attempted_at=pg_catalog.clock_timestamp(),lease_until=pg_catalog.clock_timestamp()+interval '30 seconds'
          WHERE record_id=work.record_id;
        RETURN pg_catalog.jsonb_build_object('status','attempted','command_id',work.source_ref);
    ELSIF action='finish' THEN
        IF work.state<>'attempted' THEN RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001'; END IF;
        UPDATE custody_admission.accepted_work SET
            state=CASE WHEN EXISTS(SELECT FROM custody_admission.receipts receipt
                WHERE receipt.command_id=work.source_ref) THEN 'committed' ELSE 'unknown' END,
            finished_at=pg_catalog.clock_timestamp()
          WHERE record_id=work.record_id RETURNING state INTO reply;
        RETURN pg_catalog.jsonb_build_object('status',reply);
    ELSE
        IF work.state<>'claimed' THEN RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001'; END IF;
        UPDATE custody_admission.accepted_work SET
            state=CASE action WHEN 'ignore' THEN 'ignored' WHEN 'selection' THEN 'awaiting_selection'
                  ELSE 'unavailable' END,finished_at=pg_catalog.clock_timestamp()
          WHERE record_id=work.record_id;
        RETURN pg_catalog.jsonb_build_object('status','settled');
    END IF;
END;
$custody_work$;

CREATE OR REPLACE FUNCTION custody_admission.install_accepted_birth() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_birth_install$
DECLARE root oid; owner_oid oid;
BEGIN
    -- Core-only databases have no Switchboard relation. Startup invokes this
    -- fixed installer again AFTER the owning migrations, before serving MCP.
    root:=pg_catalog.to_regclass('switchboard.message_inbox');
    IF root IS NULL THEN RETURN; END IF;
    SELECT bootstrap_owner_oid INTO owner_oid
      FROM custody_admission.bootstrap_configuration WHERE singleton;
    IF NOT EXISTS(SELECT FROM pg_catalog.pg_trigger
        WHERE tgrelid=root AND tgname='custody_accepted_birth') THEN
        EXECUTE 'CREATE TRIGGER custody_accepted_birth AFTER INSERT ON switchboard.message_inbox '
            'FOR EACH ROW EXECUTE FUNCTION custody_admission.accepted_birth()';
    END IF;
    IF NOT EXISTS(SELECT FROM pg_catalog.pg_trigger
        WHERE tgrelid=root AND tgname='custody_accepted_retire') THEN
        EXECUTE 'CREATE TRIGGER custody_accepted_retire AFTER UPDATE OR DELETE ON switchboard.message_inbox '
            'FOR EACH ROW EXECUTE FUNCTION custody_admission.accepted_birth()';
    END IF;
    -- Partition-local disable/replacement is a real bypass too. Check every
    -- present child, including the root; future partitions inherit the trigger.
    IF EXISTS(SELECT FROM pg_catalog.pg_partition_tree(root::pg_catalog.regclass) part_node
        CROSS JOIN (VALUES('custody_accepted_birth',5),('custody_accepted_retire',25)) expected(name,kind)
        LEFT JOIN pg_catalog.pg_trigger installed_trigger ON installed_trigger.tgrelid=part_node.relid
          AND installed_trigger.tgname=expected.name
        WHERE installed_trigger.oid IS NULL OR installed_trigger.tgtype<>expected.kind OR installed_trigger.tgenabled<>'O'
          OR installed_trigger.tgfoid<>'custody_admission.accepted_birth()'::pg_catalog.regprocedure
          OR installed_trigger.tgnargs<>0 OR installed_trigger.tgqual IS NOT NULL OR installed_trigger.tgisinternal)
       OR NOT EXISTS(SELECT FROM pg_catalog.pg_proc
           WHERE oid='custody_admission.accepted_birth()'::pg_catalog.regprocedure
             AND proowner=owner_oid AND prosecdef)
       OR NOT EXISTS(SELECT FROM pg_catalog.pg_class WHERE oid=root AND relkind='p') THEN
        RAISE EXCEPTION 'custody invalid accepted birth trigger' USING ERRCODE='42501';
    END IF;
END;
$custody_birth_install$;

CREATE OR REPLACE FUNCTION custody_admission.accepted_projection(projection jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_accepted_projection$
#variable_conflict use_variable
DECLARE process_id uuid; process record; accepted record; physical_count integer:=0;
    record_id uuid; content jsonb; row_digest text; requested jsonb; original_requested jsonb;
    binding record; observation record; prior record; issuer record; target record;
    owner_id uuid; issuer_id uuid; issuer_binding jsonb; selected jsonb:='[]'; ordering text;
    origin_channel text; origin_value text;
BEGIN
    process_id:=custody_admission.writer();
    SELECT * INTO process FROM custody_admission.processes WHERE processes.process_id=accepted_projection.process_id;
    IF NOT FOUND OR process.logical_actor<>'switchboard'
       OR projection->>'intent' IS DISTINCT FROM 'LOCK'
       OR projection->>'locator' !~ '^inbox:[0-9a-f-]{36}$'
       OR projection->'owner_entity_id'<>'null'::jsonb
       OR projection->'issuer_target'<>'null'::jsonb
       OR projection->'target_set'<>'[]'::jsonb
       OR projection->'revision' IS DISTINCT FROM '1'::jsonb
       OR projection->'target_set_version' IS DISTINCT FROM '1'::jsonb
       OR NOT pg_catalog.has_schema_privilege(process.role_oid,'switchboard','USAGE')
       OR NOT pg_catalog.has_table_privilege(process.role_oid,'switchboard.message_inbox','SELECT') THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    record_id:=pg_catalog.substring(projection->>'locator',7)::uuid;
    IF projection->>'locator'<>'inbox:'||record_id::text THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    -- This is the SOURCE-OWNING Switchboard connection. Receiving business
    -- writers never query peer inbox/fact tables; they consume registry metadata.
    FOR accepted IN SELECT id,received_at,request_context,raw_payload,normalized_text,schema_version,direction
        FROM switchboard.message_inbox WHERE id=record_id ORDER BY received_at FOR UPDATE LOOP
        physical_count:=physical_count+1;
    END LOOP;
    IF physical_count<>1 THEN
        RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
    END IF;
    IF accepted.schema_version IS DISTINCT FROM 'message_inbox.v2'
       OR accepted.direction IS DISTINCT FROM 'inbound'
       OR pg_catalog.jsonb_typeof(accepted.request_context) IS DISTINCT FROM 'object'
       OR pg_catalog.jsonb_typeof(accepted.raw_payload) IS DISTINCT FROM 'object'
       OR accepted.request_context->>'request_id' IS DISTINCT FROM record_id::text
       OR accepted.request_context->>'payload_type'='conversation_history'
       OR COALESCE(accepted.request_context->'source_sender_identities','[]'::jsonb)<>'[]'::jsonb
       OR accepted.normalized_text IS NULL
       OR pg_catalog.btrim(accepted.normalized_text) !~* '^LOCK +[0-9a-f -]+$' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    -- Independently derive the selector from this actual source row, rather
    -- than accepting the producer's origin hash for a different owner channel.
    -- Only the adopted transport-shaped identifiers are eligible here; email
    -- headers fail the existing shared owner-channel validator too.
    origin_channel:=accepted.request_context->>'source_channel';
    origin_value:=pg_catalog.btrim(accepted.request_context->>'source_sender_identity');
    IF origin_channel='email' AND origin_value ~ '^[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+\.[A-Za-z0-9_]+$' THEN
        origin_value:=pg_catalog.lower(origin_value);
    ELSIF origin_channel=ANY(ARRAY['telegram','telegram_user_id','telegram_user_client',
            'telegram_username','telegram_bot','telegram_chat_id']) THEN
        origin_channel:='telegram';
        origin_value:=pg_catalog.regexp_replace(origin_value,'^telegram:','');
        origin_value:=pg_catalog.regexp_replace(origin_value,'^@','');
        IF origin_value !~ '^(-?[0-9]+|[A-Za-z][A-Za-z0-9_]{4,31})$' THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        origin_value:=pg_catalog.lower(origin_value);
    ELSIF origin_channel=ANY(ARRAY['whatsapp','whatsapp_user_client','whatsapp_jid'])
          AND origin_value ~ '^[0-9]+(:[0-9]+)?@(s\.whatsapp\.net|lid)$' THEN
        origin_channel:='whatsapp_jid';
        origin_value:=pg_catalog.regexp_replace(origin_value,':[0-9]+@','@');
    ELSE
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    IF projection->>'origin_digest' IS DISTINCT FROM custody_admission.binding_digest(
        pg_catalog.jsonb_build_object('kind','owner-channel.v1','channel',origin_channel,'value',origin_value)) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    content:=pg_catalog.jsonb_build_object(
        'record_id',record_id,'received_at',pg_catalog.to_char(accepted.received_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'text_sha256',pg_catalog.encode(public.digest(pg_catalog.convert_to(accepted.normalized_text,'UTF8'),'sha256'),'hex'),
        'context_sha256',pg_catalog.encode(public.digest(pg_catalog.convert_to(accepted.request_context::text,'UTF8'),'sha256'),'hex'),
        'payload_sha256',pg_catalog.encode(public.digest(pg_catalog.convert_to(accepted.raw_payload::text,'UTF8'),'sha256'),'hex'));
    row_digest:=custody_admission.binding_digest(content);
    -- A current locator/read is not provenance. Require the immutable stamp
    -- produced by an actual INSERT on a live bound native owning writer.
    -- Restart may rehydrate this SAME report; it cannot change its birth or
    -- move evidence across a control/restore epoch. This does not implement
    -- the separately owned restored-history admission fence.
    IF NOT EXISTS(SELECT FROM custody_admission.accepted_births birth
        JOIN custody_admission.processes original ON original.process_id=birth.first_process
        JOIN custody_admission.control control ON control.singleton
        WHERE birth.record_id=record_id AND birth.retired_at IS NULL
          AND birth.received_at=accepted.received_at
          AND birth.content_digest=row_digest AND original.logical_actor='switchboard'
          AND birth.control_epoch=control.control_epoch AND birth.restore_epoch=control.restore_epoch) THEN
        RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
    END IF;
    IF projection->>'content_digest' IS DISTINCT FROM row_digest THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    requested:=projection->'selected_target_ids';
    IF pg_catalog.jsonb_typeof(requested) IS DISTINCT FROM 'array'
       OR pg_catalog.jsonb_array_length(requested) NOT BETWEEN 1 AND 64
       OR EXISTS(SELECT FROM pg_catalog.jsonb_array_elements(requested) id
            WHERE pg_catalog.jsonb_typeof(id) IS DISTINCT FROM 'string'
              OR id #>> '{}' IS DISTINCT FROM ((id #>> '{}')::uuid)::text) THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    SELECT pg_catalog.jsonb_agg(id::uuid::text ORDER BY id::uuid)
      INTO original_requested FROM pg_catalog.regexp_split_to_table(
        pg_catalog.regexp_replace(pg_catalog.btrim(accepted.normalized_text),'^LOCK[[:space:]]+','','i'),
        '[[:space:]]+') id;
    IF requested IS DISTINCT FROM original_requested
       OR (SELECT pg_catalog.count(DISTINCT id) FROM pg_catalog.jsonb_array_elements_text(requested) id)
             <>pg_catalog.jsonb_array_length(requested) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    SELECT * INTO prior FROM custody_admission.sources
      WHERE logical_actor='switchboard' AND source_family='accepted_ingress'
        AND source_locator=projection->>'locator' AND source_revision=(projection->>'revision')::bigint;
    IF FOUND THEN
        -- Replaying the original report carries its original association and
        -- expiry. Current resolver output cannot refill or promote that report.
        IF prior.projection->>'content_digest' IS DISTINCT FROM row_digest
           OR prior.projection->'selected_target_ids' IS DISTINCT FROM requested
           OR prior.projection->>'origin_digest' IS DISTINCT FROM projection->>'origin_digest'
           OR prior.expires_at<=pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        PERFORM custody_admission.require_origin_current(prior.source_ref);
        RETURN prior.projection;
    END IF;
    IF (projection->>'expires_at')::timestamptz>accepted.received_at+interval '5 minutes'
       OR accepted.received_at>pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    SELECT * INTO binding FROM custody_admission.origin_bindings
      WHERE origin_digest=projection->>'origin_digest' FOR UPDATE;
    IF NOT FOUND OR binding.owner_entity_id IS NULL OR binding.expires_at<=pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
    END IF;
    SELECT * INTO observation FROM custody_admission.sources WHERE source_ref=binding.source_ref;
    IF NOT FOUND OR observation.logical_actor<>'relationship'
       OR observation.source_family<>'domain_evidence'
       OR observation.projection->>'intent'<>'identity_binding'
       OR NOT EXISTS(SELECT FROM custody_admission.source_associations association
           WHERE association.source_ref=observation.source_ref
             AND custody_admission.process_current(association.process_id)) THEN
        RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
    END IF;
    owner_id:=binding.owner_entity_id;
    ordering:=owner_id::text||':endpoint:'||(projection->>'origin_digest')||':'||binding.binding_generation;
    SELECT * INTO issuer FROM custody_admission.targets WHERE ordering_key=ordering;
    issuer_id:=CASE WHEN FOUND THEN issuer.target_id ELSE pg_catalog.gen_random_uuid() END;
    issuer_binding:=pg_catalog.jsonb_build_object(
        'target_id',issuer_id,'target_kind','endpoint','binding_digest',projection->>'origin_digest',
        'binding_version',binding.binding_generation,'generation',COALESCE(issuer.generation,0));
    IF EXISTS(SELECT FROM public.custody_holds WHERE target_id=issuer_id AND released_at IS NULL) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    FOR target IN SELECT t.* FROM custody_admission.targets t
        WHERE t.target_id IN (SELECT id::uuid FROM pg_catalog.jsonb_array_elements_text(requested) id)
        ORDER BY t.ordering_key COLLATE "C" LOOP
        IF target.owner_entity_id<>owner_id THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
        selected:=selected||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
            'target_id',target.target_id,'target_kind',target.target_kind,'binding_digest',target.binding_digest,
            'binding_version',target.binding_version,'generation',target.generation));
    END LOOP;
    IF pg_catalog.jsonb_array_length(selected)<>pg_catalog.jsonb_array_length(requested) THEN
        RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
    END IF;
    RETURN projection||pg_catalog.jsonb_build_object(
        'owner_entity_id',owner_id,'issuer_target',issuer_id,'issuer_binding',issuer_binding,
        'target_set',selected,'target_set_version',binding.binding_generation);
END;
$custody_accepted_projection$;

CREATE OR REPLACE FUNCTION custody_admission.protocol(action text,p jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_protocol$
#variable_conflict use_variable
DECLARE
    a record; w record; process record; source record; call_row record; destination record;
    c jsonb; process_id uuid; source_id uuid; new_id uuid; operation_hash text; deadline timestamptz;
    generation bigint; result jsonb; target jsonb; owner_id uuid; binding jsonb;
    fresh_source boolean; origin_binding record; owner_birth timestamptz;
BEGIN
    -- Only fixed wrappers call this dispatcher; no ordinary role can EXEC it.
    PERFORM custody_admission.canonical_json(p);
    IF action='anchor_begin' THEN
        SELECT * INTO a FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid();
        INSERT INTO custody_admission.anchor_proposals(database_oid,login_oid,role_oid,
            backend_pid,backend_start,expires_at)
        VALUES(a.datid,a.usesysid,custody_admission.caller_role(),a.pid,a.backend_start,
            pg_catalog.clock_timestamp()+interval '10 seconds')
        ON CONFLICT(database_oid,backend_pid,backend_start) DO UPDATE
          SET nonce=pg_catalog.gen_random_uuid(),expires_at=EXCLUDED.expires_at
          WHERE custody_admission.anchor_proposals.consumed_at IS NULL
        RETURNING nonce INTO new_id;
        IF new_id IS NULL THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
        RETURN pg_catalog.jsonb_build_object('nonce',new_id);
    ELSIF action='connection_begin' THEN
        c:=custody_admission.current_control();
        SELECT * INTO a FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid();
        INSERT INTO custody_admission.connections(database_oid,backend_pid,backend_start,
            login_oid,role_oid,state,bound_until)
        VALUES(a.datid,a.pid,a.backend_start,a.usesysid,custody_admission.caller_role(),
            'proposed',pg_catalog.clock_timestamp()+interval '10 seconds')
        ON CONFLICT(database_oid,backend_pid,backend_start) DO UPDATE
          SET acquisition_generation=custody_admission.connections.acquisition_generation+1,
              nonce=pg_catalog.gen_random_uuid(),process_id=NULL,state='proposed',
              login_oid=EXCLUDED.login_oid,role_oid=EXCLUDED.role_oid,finished_at=NULL,
              verified_call_id=NULL,
              bound_until=EXCLUDED.bound_until
          WHERE custody_admission.connections.state IN ('released','revoked')
        RETURNING nonce,acquisition_generation INTO new_id,generation;
        IF new_id IS NULL THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
        RETURN pg_catalog.jsonb_build_object('writer_nonce',new_id,'acquisition_generation',generation);
    ELSIF action='connection_unbind' THEN
        -- Cleanup is allowed after lease/epoch revocation, but exclusively on
        -- this actual physical checkout and its exact server-issued generation.
        SELECT * INTO a FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid();
        UPDATE custody_admission.connections SET state='released',process_id=NULL,verified_call_id=NULL,
            bound_until=pg_catalog.clock_timestamp()
        WHERE database_oid=a.datid AND backend_pid=a.pid AND backend_start=a.backend_start
          AND login_oid=a.usesysid AND role_oid=custody_admission.caller_role()
          AND acquisition_generation=(p->>'acquisition_generation')::bigint
        RETURNING acquisition_generation INTO generation;
        IF generation IS NULL THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
        RETURN pg_catalog.jsonb_build_object('unbound',true,'acquisition_generation',generation);
    ELSIF action IN ('anchor_renew','bind_connection','challenge','respond','mint') THEN
        process_id:=custody_admission.anchor();
    ELSIF action='connection_finish' THEN
        c:=custody_admission.current_control();
        SELECT * INTO a FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid();
        SELECT * INTO w FROM custody_admission.connections
        WHERE nonce=(p->>'writer_nonce')::uuid AND database_oid=a.datid
          AND backend_pid=a.pid AND backend_start=a.backend_start AND login_oid=a.usesysid
          AND role_oid=custody_admission.caller_role() AND state='bound'
          AND finished_at IS NULL AND bound_until>pg_catalog.clock_timestamp() FOR UPDATE;
        IF NOT FOUND OR NOT custody_admission.process_current(w.process_id) THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        UPDATE custody_admission.connections SET finished_at=pg_catalog.clock_timestamp()
          WHERE nonce=w.nonce;
        RETURN pg_catalog.jsonb_build_object('process_id',w.process_id,
            'acquisition_generation',w.acquisition_generation);
    ELSE process_id:=custody_admission.writer();
    END IF;
    SELECT * INTO process FROM custody_admission.processes WHERE processes.process_id=protocol.process_id;
    c:=custody_admission.current_control();
    IF action='anchor_renew' THEN
        UPDATE custody_admission.processes SET lease_expires_at=pg_catalog.clock_timestamp()+interval '30 seconds'
          WHERE processes.process_id=protocol.process_id;
        RETURN pg_catalog.jsonb_build_object('lease_seconds',30);
    ELSIF action='bind_connection' THEN
        SELECT * INTO w FROM custody_admission.connections
          WHERE nonce=(p->>'writer_nonce')::uuid FOR UPDATE;
        IF NOT FOUND OR w.state<>'proposed' OR w.bound_until<=pg_catalog.clock_timestamp()
           OR w.login_oid<>process.login_oid OR w.role_oid<>process.role_oid
           OR w.database_oid<>process.database_oid
           OR NOT EXISTS(SELECT FROM pg_catalog.pg_stat_activity
               WHERE pid=w.backend_pid AND backend_start=w.backend_start AND datid=w.database_oid
                 AND usesysid=w.login_oid) THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        UPDATE custody_admission.connections SET process_id=protocol.process_id,state='bound',
            bound_until=LEAST(process.lease_expires_at,pg_catalog.clock_timestamp()+interval '30 seconds')
          WHERE nonce=w.nonce;
        RETURN pg_catalog.jsonb_build_object('acquisition_generation',w.acquisition_generation);
    ELSIF action='source_register' THEN
        IF process.operations='[]'::jsonb AND (
            p->>'source_family' IS DISTINCT FROM 'domain_evidence'
            OR p->'projection'->>'intent' IS DISTINCT FROM 'identity_binding'
        ) THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
        IF p->>'source_family'='accepted_ingress' THEN
            PERFORM custody_admission.closed(p->'projection',ARRAY[
                'locator','revision','content_digest','origin_digest','owner_entity_id',
                'issuer_target','target_set','target_set_version','expires_at','intent','selected_target_ids']);
            p:=pg_catalog.jsonb_set(p,'{projection}',
                custody_admission.accepted_projection(p->'projection'));
        END IF;
        -- Owner commands originate in the private current-auth controller. A
        -- matching role or projection cannot invent an unprepared command.
        IF p->>'source_family' IN ('owner_command','host_command') THEN
            IF p->'projection'->>'locator' !~ '^command:[0-9a-f-]{36}$' THEN
                RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
            END IF;
            SELECT * INTO call_row FROM custody_admission.commands command
              WHERE command.command_id=pg_catalog.substring(p->'projection'->>'locator',9)::uuid;
            IF NOT FOUND
               OR (p->>'source_family'='owner_command' AND (
                   process.logical_actor<>'dashboard' OR call_row.kind<>'browser'))
               OR (p->>'source_family'='host_command' AND (
                   process.logical_actor<>'host-switchboard' OR call_row.kind<>'host'))
               OR call_row.selection->'target_set' IS DISTINCT FROM p->'projection'->'target_set'
               OR call_row.selection->'target_set_version' IS DISTINCT FROM p->'projection'->'target_set_version'
               OR call_row.selection_digest IS DISTINCT FROM p->'projection'->>'content_digest'
               OR call_row.expires_at IS DISTINCT FROM (p->'projection'->>'expires_at')::timestamptz THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
            PERFORM custody_admission.command_current(call_row.command_id);
            -- The restricted dashboard role has no public entity reader. The
            -- installed engine selects the current canonical owner; a caller
            -- projection may not nominate that authority.
            IF p->'projection'->'owner_entity_id' IS DISTINCT FROM 'null'::jsonb
               OR p->'projection'->>'issuer_target' IS NOT NULL
               OR (SELECT pg_catalog.count(*) FROM public.entities entity
                   WHERE 'owner'=ANY(COALESCE(entity.roles,ARRAY[]::text[]))
                     AND entity.metadata->>'merged_into' IS NULL
                     AND entity.metadata->>'deleted_at' IS NULL
                     AND NOT COALESCE((entity.metadata->>'unidentified')='true',false))<>1 THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
            SELECT entity.id INTO owner_id FROM public.entities entity
              WHERE 'owner'=ANY(COALESCE(entity.roles,ARRAY[]::text[]))
                AND entity.metadata->>'merged_into' IS NULL
                AND entity.metadata->>'deleted_at' IS NULL
                AND NOT COALESCE((entity.metadata->>'unidentified')='true',false);
            p:=pg_catalog.jsonb_set(p,'{projection,owner_entity_id}',pg_catalog.to_jsonb(owner_id));
        END IF;
        PERFORM custody_admission.closed(p->'projection',ARRAY[
            'locator','revision','content_digest','origin_digest','owner_entity_id',
            'issuer_target','target_set','target_set_version','expires_at'
        ],ARRAY['intent','event_ref','question_ref','token_digest','answer','provider','inventory','issuer_binding','assurance','selected_target_ids']);
        IF NOT process.source_kinds ? (p->>'source_family')
           OR pg_catalog.jsonb_typeof(p->'projection'->'revision') IS DISTINCT FROM 'number'
           OR (p->'projection'->>'revision')::bigint<1
           OR pg_catalog.jsonb_typeof(p->'projection'->'locator') IS DISTINCT FROM 'string'
           OR p->'projection'->>'locator' !~ '^[a-zA-Z0-9:_-]{1,128}$'
           OR pg_catalog.jsonb_typeof(p->'projection'->'content_digest') IS DISTINCT FROM 'string'
           OR p->'projection'->>'content_digest' !~ '^[0-9a-f]{64}$'
           OR pg_catalog.jsonb_typeof(p->'projection'->'origin_digest') IS DISTINCT FROM 'string'
           OR p->'projection'->>'origin_digest' !~ '^[0-9a-f]{64}$'
           OR pg_catalog.jsonb_typeof(p->'projection'->'target_set') IS DISTINCT FROM 'array'
           OR pg_catalog.jsonb_array_length(p->'projection'->'target_set')>64
           OR pg_catalog.jsonb_typeof(p->'projection'->'expires_at') IS DISTINCT FROM 'string'
           OR p->'projection'->>'expires_at'
                !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z$'
           OR pg_catalog.jsonb_typeof(p->'projection'->'owner_entity_id')
                NOT IN ('null','string')
           OR pg_catalog.jsonb_typeof(p->'projection'->'issuer_target')
                NOT IN ('null','string') THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        deadline:=(p->'projection'->>'expires_at')::timestamptz;
        IF pg_catalog.to_char(deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
           IS DISTINCT FROM p->'projection'->>'expires_at' THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        IF deadline<=pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'custody expired' USING ERRCODE='42501';
        END IF;
        operation_hash:=custody_admission.binding_digest(p->'projection');
        PERFORM custody_admission.selection_valid(pg_catalog.jsonb_build_object(
            'target_set',p->'projection'->'target_set','target_set_version',p->'projection'->'target_set_version'));
        IF p->'projection' ? 'issuer_binding' THEN
            PERFORM custody_admission.selection_valid(pg_catalog.jsonb_build_object(
                'target_set',pg_catalog.jsonb_build_array(p->'projection'->'issuer_binding'),
                'target_set_version',1));
            IF p->'projection'->>'issuer_target'
               IS DISTINCT FROM p->'projection'->'issuer_binding'->>'target_id' THEN
                RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
            END IF;
        ELSIF p->'projection'->>'issuer_target' IS NOT NULL THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        owner_id:=(p->'projection'->>'owner_entity_id')::uuid;
        IF owner_id IS NOT NULL THEN
            IF p->'projection'->>'owner_entity_id'<>owner_id::text THEN
                RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
            END IF;
            -- Canonical owner role changes/delete cannot race this source capture.
            PERFORM FROM public.entities e WHERE e.id=owner_id FOR UPDATE;
            IF NOT FOUND OR NOT EXISTS(SELECT FROM public.entities e WHERE e.id=owner_id
                AND 'owner'=ANY(COALESCE(e.roles,ARRAY[]::text[]))
                AND e.metadata->>'merged_into' IS NULL
                AND e.metadata->>'deleted_at' IS NULL
                AND NOT COALESCE((e.metadata->>'unidentified')='true',false)) THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
            SELECT created_at INTO owner_birth FROM public.entities WHERE id=owner_id;
        END IF;
        SELECT * INTO source FROM custody_admission.sources
          WHERE logical_actor=process.logical_actor AND source_family=p->>'source_family'
            AND source_locator=p->'projection'->>'locator'
            AND source_revision=(p->'projection'->>'revision')::bigint;
        IF FOUND THEN
            IF source.source_digest<>operation_hash OR source.projection<>p->'projection' THEN
                RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
            END IF;
            source_id:=source.source_ref;
        ELSE source_id:=NULL;
        END IF;
        IF p->'projection'->>'intent'='identity_binding' THEN
            PERFORM FROM custody_admission.origin_bindings
              WHERE origin_digest=p->'projection'->>'origin_digest' FOR UPDATE;
        ELSIF owner_id IS NOT NULL AND (p->>'source_family'='accepted_ingress'
                                       OR p->'projection' ? 'answer') THEN
            SELECT * INTO origin_binding FROM custody_admission.origin_bindings
              WHERE origin_digest=p->'projection'->>'origin_digest' FOR UPDATE;
            IF source_id IS NOT NULL THEN
                PERFORM custody_admission.require_origin_current(source_id);
            ELSIF NOT FOUND OR origin_binding.expires_at<=pg_catalog.clock_timestamp()
                  OR origin_binding.owner_entity_id IS DISTINCT FROM owner_id
                  OR origin_binding.owner_birth IS DISTINCT FROM owner_birth THEN
                RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
            END IF;
        END IF;
        -- The complete existing target union is locked BEFORE inserting an
        -- immutable source decision. Brand-new targets are born afterward in
        -- this same control-serialized transaction; no existing target may be
        -- acquired late after source/decision locks.
        PERFORM FROM custody_admission.targets existing_target
        WHERE existing_target.target_id IN (
            SELECT (v->>'target_id')::uuid FROM pg_catalog.jsonb_array_elements(
                (p->'projection'->'target_set')||CASE
                WHEN p->'projection' ? 'issuer_binding'
                THEN pg_catalog.jsonb_build_array(p->'projection'->'issuer_binding')
                ELSE '[]'::jsonb END) v)
        ORDER BY existing_target.ordering_key COLLATE "C" FOR UPDATE;
        INSERT INTO custody_admission.sources(logical_actor,source_family,source_locator,
            source_revision,projection,source_digest,first_process,expires_at)
        VALUES(process.logical_actor,p->>'source_family',p->'projection'->>'locator',
            (p->'projection'->>'revision')::bigint,p->'projection',operation_hash,process_id,deadline)
        ON CONFLICT(logical_actor,source_family,source_locator,source_revision) DO NOTHING
        RETURNING source_ref INTO source_id;
        fresh_source:=source_id IS NOT NULL;
        IF source_id IS NULL THEN
            SELECT * INTO source FROM custody_admission.sources
            WHERE logical_actor=process.logical_actor AND source_family=p->>'source_family'
              AND source_locator=p->'projection'->>'locator'
              AND source_revision=(p->'projection'->>'revision')::bigint;
            IF source.source_digest<>operation_hash OR source.projection<>p->'projection' THEN
                RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
            END IF;
            source_id:=source.source_ref;
        END IF;
        IF p->'projection'->>'intent'='identity_binding' THEN
            -- Fixed Relationship producers read canonical rows in this SAME
            -- transaction. This is a current pointer, not caller attribution.
            IF process.logical_actor<>'relationship' OR p->>'source_family'<>'domain_evidence'
               OR p->'projection'->>'locator'<>'identity:'||(p->'projection'->>'origin_digest')
               OR p->'projection'->'target_set'<>'[]'::jsonb
               OR p->'projection'->>'issuer_target' IS NOT NULL
               OR deadline>pg_catalog.clock_timestamp()+interval '5 minutes'
               OR p->'projection'->'target_set_version' IS DISTINCT FROM p->'projection'->'revision' THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
            INSERT INTO custody_admission.origin_bindings(origin_digest,source_ref,
                source_revision,source_digest,owner_entity_id,owner_birth,expires_at)
            VALUES(p->'projection'->>'origin_digest',source_id,
                (p->'projection'->>'revision')::bigint,operation_hash,owner_id,owner_birth,deadline)
            ON CONFLICT(origin_digest) DO UPDATE SET source_ref=EXCLUDED.source_ref,
                source_revision=EXCLUDED.source_revision,source_digest=EXCLUDED.source_digest,
                binding_generation=origin_bindings.binding_generation+CASE
                  WHEN (SELECT previous_observation.projection->>'content_digest'
                        FROM custody_admission.sources previous_observation
                        WHERE previous_observation.source_ref=origin_bindings.source_ref)
                       IS DISTINCT FROM p->'projection'->>'content_digest' THEN 1 ELSE 0 END,
                owner_entity_id=EXCLUDED.owner_entity_id,owner_birth=EXCLUDED.owner_birth,
                expires_at=EXCLUDED.expires_at
              WHERE origin_bindings.source_revision<EXCLUDED.source_revision;
            IF NOT EXISTS(SELECT FROM custody_admission.origin_bindings current_binding
                WHERE current_binding.origin_digest=p->'projection'->>'origin_digest'
                  AND current_binding.source_ref=source_id
                  AND current_binding.source_revision=(p->'projection'->>'revision')::bigint
                  AND current_binding.source_digest=operation_hash
                  AND current_binding.owner_entity_id IS NOT DISTINCT FROM owner_id
                  AND current_binding.owner_birth IS NOT DISTINCT FROM owner_birth
                  AND current_binding.expires_at=deadline) THEN
                RAISE EXCEPTION 'custody stale origin' USING ERRCODE='40001';
            END IF;
        ELSIF owner_id IS NOT NULL AND (p->>'source_family'='accepted_ingress'
                                       OR p->'projection' ? 'answer') THEN
            IF fresh_source THEN
                SELECT * INTO origin_binding FROM custody_admission.origin_bindings
                  WHERE origin_digest=p->'projection'->>'origin_digest' FOR UPDATE;
                IF NOT FOUND OR origin_binding.expires_at<=pg_catalog.clock_timestamp()
                   OR origin_binding.owner_entity_id IS DISTINCT FROM owner_id THEN
                    RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
                END IF;
                INSERT INTO custody_admission.source_origin_bindings(source_ref,origin_digest,
                    origin_source_ref,origin_revision,binding_generation,owner_entity_id,owner_birth)
                VALUES(source_id,origin_binding.origin_digest,origin_binding.source_ref,
                    origin_binding.source_revision,origin_binding.binding_generation,
                    owner_id,origin_binding.owner_birth);
            END IF;
            -- Replay/restart MUST retain the original version; missing old
            -- capture is unavailable, never retroactively filled from today.
            PERFORM custody_admission.require_origin_current(source_id);
        END IF;
        FOR target IN SELECT value FROM pg_catalog.jsonb_array_elements(
            (p->'projection'->'target_set')||CASE WHEN p->'projection' ? 'issuer_binding'
            THEN pg_catalog.jsonb_build_array(p->'projection'->'issuer_binding') ELSE '[]'::jsonb END
        ) ORDER BY (value->>'target_kind')||':'||(value->>'binding_digest')||':'||
                   (value->>'binding_version') COLLATE "C" LOOP
            PERFORM custody_admission.selection_valid(pg_catalog.jsonb_build_object(
                'target_set',pg_catalog.jsonb_build_array(target),'target_set_version',1));
            IF owner_id IS NULL THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
            INSERT INTO custody_admission.targets(target_id,owner_entity_id,target_kind,
                binding_digest,binding_version,source_ref,ordering_key)
            VALUES((target->>'target_id')::uuid,owner_id,target->>'target_kind',target->>'binding_digest',
                (target->>'binding_version')::bigint,source_id,
                owner_id::text||':'||(target->>'target_kind')||':'||(target->>'binding_digest')||':'||(target->>'binding_version'))
            ON CONFLICT(target_id) DO NOTHING;
            IF NOT EXISTS(SELECT FROM custody_admission.targets t WHERE t.target_id=(target->>'target_id')::uuid
                AND t.owner_entity_id=owner_id AND t.target_kind=target->>'target_kind'
                AND t.binding_digest=target->>'binding_digest' AND t.binding_version=(target->>'binding_version')::bigint
                AND (NOT fresh_source OR t.generation=(target->>'generation')::bigint)) THEN
                RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
            END IF;
        END LOOP;
        INSERT INTO custody_admission.source_associations(source_ref,process_id)
          VALUES(source_id,process_id) ON CONFLICT DO NOTHING;
        RETURN pg_catalog.jsonb_build_object('source_ref',source_id,'source_digest',operation_hash,
            'projection',p->'projection');
    ELSIF action='mint' THEN
        PERFORM custody_admission.closed(p->'operation',ARRAY[
            'mint_request_id','operation','method','arguments','target_set','target_set_version'
        ],ARRAY['command_id','effect_namespace','effect_id','content_digest']);
        SELECT * INTO source FROM custody_admission.sources
          WHERE source_ref=(p->>'source_ref')::uuid;
        IF NOT FOUND OR source.logical_actor<>process.logical_actor
           OR NOT EXISTS(SELECT FROM custody_admission.source_associations
               WHERE source_ref=source.source_ref AND source_associations.process_id=protocol.process_id)
           OR source.expires_at<=pg_catalog.clock_timestamp()
           OR NOT process.operations ? (p->'operation'->>'operation')
           OR NOT process.audiences ? (p->>'destination_actor')
           OR p->'operation'->'target_set' IS DISTINCT FROM source.projection->'target_set'
           OR p->'operation'->'target_set_version' IS DISTINCT FROM source.projection->'target_set_version' THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        PERFORM custody_admission.require_origin_current(source.source_ref);
        PERFORM custody_admission.command_source_current(source.source_ref,p->'operation');
        -- A logical audience is not an incarnation selector. An overlapping
        -- live daemon must be reconciled, never silently chosen by birth time.
        SELECT pg_catalog.count(*) INTO generation FROM custody_admission.processes
          WHERE logical_actor=p->>'destination_actor'
            AND operations<>'[]'::jsonb
            AND custody_admission.process_current(processes.process_id);
        IF generation<>1 THEN RAISE EXCEPTION 'custody unavailable' USING ERRCODE='42501'; END IF;
        SELECT * INTO destination FROM custody_admission.processes
          WHERE logical_actor=p->>'destination_actor'
            AND operations<>'[]'::jsonb
            AND custody_admission.process_current(processes.process_id);
        IF NOT FOUND THEN RAISE EXCEPTION 'custody unavailable' USING ERRCODE='42501'; END IF;
        SELECT * INTO call_row FROM custody_admission.calls
        WHERE issuer_process=process_id AND source_ref=source.source_ref
          AND destination_process=destination.process_id
          AND mint_request_id=(p->'operation'->>'mint_request_id')::uuid;
        IF FOUND THEN
            IF call_row.operation<>p->'operation' OR call_row.expires_at<=pg_catalog.clock_timestamp() THEN
                RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
            END IF;
            binding:=pg_catalog.jsonb_build_object(
                'call_ref',call_row.call_id,'source_ref',source.source_ref,'source_digest',source.source_digest,
                'issuer_process',process_id,'destination_process',destination.process_id,
                'destination_actor',destination.logical_actor,'operation',call_row.operation,
                'control_epoch',call_row.control_epoch,'restore_epoch',call_row.restore_epoch,
                'expires_at',pg_catalog.to_char(call_row.expires_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
            IF custody_admission.binding_digest(binding)<>call_row.operation_digest THEN
                RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
            END IF;
            RETURN pg_catalog.jsonb_build_object('call_ref',call_row.call_id,
                'operation_digest',call_row.operation_digest,'expires_at',call_row.expires_at,
                'binding',binding);
        END IF;
        new_id:=pg_catalog.gen_random_uuid();
        deadline:=LEAST(source.expires_at,pg_catalog.clock_timestamp()+interval '30 seconds');
        binding:=pg_catalog.jsonb_build_object(
            'call_ref',new_id,'source_ref',source.source_ref,'source_digest',source.source_digest,
            'issuer_process',process_id,'destination_process',destination.process_id,
            'destination_actor',destination.logical_actor,
            'operation',p->'operation','control_epoch',c->'control_epoch','restore_epoch',c->'restore_epoch',
            'expires_at',pg_catalog.to_char(deadline AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
        operation_hash:=custody_admission.binding_digest(binding);
        INSERT INTO custody_admission.calls(call_id,issuer_process,destination_process,source_ref,
            mint_request_id,operation,operation_digest,control_epoch,restore_epoch,expires_at,state,command_id)
        VALUES(new_id,process_id,destination.process_id,source.source_ref,
            (p->'operation'->>'mint_request_id')::uuid,p->'operation',operation_hash,
            (c->>'control_epoch')::bigint,(c->>'restore_epoch')::uuid,deadline,'minted',
            (p->'operation'->>'command_id')::uuid);
        RETURN pg_catalog.jsonb_build_object('call_ref',new_id,'operation_digest',operation_hash,
            'expires_at',deadline,'binding',binding);
    END IF;
    -- Both processes must still be current; a copied locator/digest/challenge
    -- has no authority without its source anchor and receiving bound writer.
    IF action IN ('challenge','respond') THEN
        SELECT * INTO call_row FROM custody_admission.calls
          WHERE call_id=(p->>'call_ref')::uuid FOR UPDATE;
    ELSE
        -- Business verification reads before targets; its actual decision lock
        -- is taken only by the final writer after the complete sorted target set.
        SELECT * INTO call_row FROM custody_admission.calls
          WHERE call_id=(p->>'call_ref')::uuid;
    END IF;
    IF NOT FOUND OR NOT custody_admission.process_current(call_row.issuer_process)
       OR NOT custody_admission.process_current(call_row.destination_process)
       OR call_row.expires_at<=pg_catalog.clock_timestamp()
       OR call_row.control_epoch<>(c->>'control_epoch')::bigint
       OR call_row.restore_epoch<>(c->>'restore_epoch')::uuid
       OR NOT EXISTS(SELECT FROM custody_admission.sources WHERE source_ref=call_row.source_ref
                     AND expires_at>pg_catalog.clock_timestamp()) THEN
        RAISE EXCEPTION 'custody expired' USING ERRCODE='42501';
    END IF;
    IF action='challenge' THEN
        IF process_id<>call_row.destination_process OR call_row.operation_digest<>p->>'operation_digest'
           OR call_row.state NOT IN ('minted','challenged') THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        new_id:=pg_catalog.gen_random_uuid();
        UPDATE custody_admission.calls SET challenge_ref=new_id,state='challenged',
            challenge_expires_at=LEAST(expires_at,pg_catalog.clock_timestamp()+interval '10 seconds')
          WHERE call_id=call_row.call_id;
        RETURN pg_catalog.jsonb_build_object('challenge_ref',new_id);
    ELSIF action='respond' THEN
        IF process_id<>call_row.issuer_process OR call_row.state<>'challenged'
           OR call_row.challenge_ref IS DISTINCT FROM (p->>'challenge_ref')::uuid
           OR call_row.challenge_expires_at<=pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        UPDATE custody_admission.calls SET state='armed' WHERE call_id=call_row.call_id;
        RETURN pg_catalog.jsonb_build_object('armed',true);
    ELSIF action='verify' THEN
        PERFORM custody_admission.command_source_current(call_row.source_ref,call_row.operation);
        IF process_id<>call_row.destination_process OR call_row.state NOT IN ('armed','committed')
           OR call_row.operation_digest<>p->>'operation_digest'
           OR call_row.challenge_ref IS DISTINCT FROM (p->>'challenge_ref')::uuid
           OR call_row.challenge_expires_at<=pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        -- Private SAME-checkout online-verification binding. A receiver's
        -- ordinary admit_write call cannot borrow another source association
        -- or an unrelated verdict from this pool. Rechecking the same call is
        -- permitted; switching call identity inside one transaction is not.
        SELECT * INTO a FROM pg_catalog.pg_stat_activity WHERE pid=pg_catalog.pg_backend_pid();
        UPDATE custody_admission.connections SET verified_call_id=call_row.call_id
          WHERE database_oid=a.datid AND backend_pid=a.pid AND backend_start=a.backend_start
            AND login_oid=a.usesysid AND role_oid=custody_admission.caller_role()
            AND connections.process_id=protocol.process_id AND state='bound'
            AND finished_at IS NOT NULL AND bound_until>pg_catalog.clock_timestamp()
            AND (verified_call_id IS NULL OR verified_call_id=call_row.call_id);
        IF NOT FOUND THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
        RETURN pg_catalog.jsonb_build_object('source_ref',call_row.source_ref,
            'operation',call_row.operation,'command_id',call_row.command_id);
    ELSIF action='result_read' THEN
        PERFORM custody_admission.require_verified_call(call_row.call_id);
        PERFORM custody_admission.command_source_current(call_row.source_ref,call_row.operation);
        IF process_id<>call_row.destination_process
           OR call_row.state NOT IN ('armed','committed')
           OR call_row.challenge_expires_at<=pg_catalog.clock_timestamp()
           OR call_row.operation->>'method'<>'custody.result'
           OR call_row.command_id IS DISTINCT FROM (p->>'command_id')::uuid
 THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        SELECT r.result INTO result FROM custody_admission.receipts r WHERE command_id=call_row.command_id;
        RETURN COALESCE(result,pg_catalog.jsonb_build_object('status','unknown'));
    END IF;
    RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
END;
$custody_protocol$;

CREATE OR REPLACE FUNCTION custody_admission.selection_valid(selection jsonb) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path=pg_catalog,pg_temp AS $custody_selection$
DECLARE target jsonb; previous text; key text;
BEGIN
    PERFORM custody_admission.closed(selection,ARRAY['target_set','target_set_version'],
        ARRAY['case_id','reason','observed_incident_at','provider','provider_version','result_command_id']);
    IF selection ? 'result_command_id' AND (
        pg_catalog.jsonb_typeof(selection->'result_command_id') IS DISTINCT FROM 'string'
        OR selection->>'result_command_id' IS DISTINCT FROM ((selection->>'result_command_id')::uuid)::text
    ) THEN RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023'; END IF;
    IF pg_catalog.jsonb_typeof(selection->'target_set') IS DISTINCT FROM 'array'
       OR pg_catalog.jsonb_array_length(selection->'target_set')>64
       OR pg_catalog.jsonb_typeof(selection->'target_set_version') IS DISTINCT FROM 'number'
       OR (selection->>'target_set_version')::bigint<1 THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    FOR target IN SELECT pg_catalog.jsonb_array_elements(selection->'target_set') LOOP
        PERFORM custody_admission.closed(target,ARRAY[
            'target_id','target_kind','binding_digest','binding_version','generation'
        ]);
        IF pg_catalog.jsonb_typeof(target->'target_id') IS DISTINCT FROM 'string'
           OR target->>'target_id' IS DISTINCT FROM ((target->>'target_id')::uuid)::text
           OR pg_catalog.jsonb_typeof(target->'target_kind') IS DISTINCT FROM 'string'
           OR target->>'target_kind'<>ALL(ARRAY['endpoint','account'])
           OR pg_catalog.jsonb_typeof(target->'binding_digest') IS DISTINCT FROM 'string'
           OR target->>'binding_digest' !~ '^[0-9a-f]{64}$'
           OR pg_catalog.jsonb_typeof(target->'binding_version') IS DISTINCT FROM 'number'
           OR (target->>'binding_version')::bigint<1
           OR pg_catalog.jsonb_typeof(target->'generation') IS DISTINCT FROM 'number'
           OR (target->>'generation')::bigint<0 THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        key:=(target->>'target_kind')||':'||(target->>'binding_digest')||':'||
            (target->>'binding_version');
        IF previous IS NOT NULL AND previous COLLATE "C">=key COLLATE "C" THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        previous:=key;
    END LOOP;
    IF (SELECT pg_catalog.count(DISTINCT v->>'target_id')
        FROM pg_catalog.jsonb_array_elements(selection->'target_set') v)
       <>pg_catalog.jsonb_array_length(selection->'target_set') THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
END;
$custody_selection$;

CREATE OR REPLACE FUNCTION custody_admission.lock_targets(selection jsonb,issuer uuid DEFAULT NULL)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_lock_targets$
DECLARE target jsonb; actual record;
BEGIN
    PERFORM custody_admission.selection_valid(selection);
    -- Caller has already locked auth (if applicable) then global control. This
    -- is the complete target union, including selected case members and issuer.
    PERFORM FROM custody_admission.targets t
    WHERE t.target_id=issuer
       OR t.target_id IN (SELECT (v->>'target_id')::uuid
                          FROM pg_catalog.jsonb_array_elements(selection->'target_set') v)
       OR t.target_id IN (SELECT m.target_id FROM custody_admission.case_members m
                          WHERE m.case_id=(selection->>'case_id')::uuid)
    ORDER BY t.ordering_key COLLATE "C" FOR UPDATE;
    FOR target IN SELECT pg_catalog.jsonb_array_elements(selection->'target_set') LOOP
        SELECT * INTO actual FROM custody_admission.targets WHERE target_id=(target->>'target_id')::uuid;
        IF NOT FOUND OR actual.target_kind<>target->>'target_kind'
           OR actual.binding_digest<>target->>'binding_digest'
           OR actual.binding_version<>(target->>'binding_version')::bigint THEN
            RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
        END IF;
    END LOOP;
    IF issuer IS NOT NULL AND (
       NOT EXISTS(SELECT FROM custody_admission.targets WHERE target_id=issuer)
       OR EXISTS(SELECT FROM public.custody_holds WHERE target_id=issuer AND released_at IS NULL)
    ) THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
END;
$custody_lock_targets$;

CREATE OR REPLACE FUNCTION custody_admission.browser_check(proof jsonb) RETURNS timestamptz
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_browser$
DECLARE instance record; session record; deadline timestamptz;
BEGIN
    PERFORM custody_admission.closed(proof,ARRAY[
        'session_digest','csrf_digest','origin','rp_id','key_generation',
        'credential_epoch','session_epoch'
    ]);
    SELECT * INTO instance FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
    IF NOT FOUND OR instance.state<>ALL(ARRAY['configured_key','keyless_enrolled'])
       OR instance.origin IS NULL OR instance.origin IS DISTINCT FROM proof->>'origin'
       OR instance.rp_id IS DISTINCT FROM proof->>'rp_id'
       OR instance.key_generation IS DISTINCT FROM proof->>'key_generation'
       OR instance.credential_epoch<>(proof->>'credential_epoch')::bigint
       OR instance.session_epoch<>(proof->>'session_epoch')::bigint THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    SELECT * INTO session FROM dashboard_auth.sessions
      WHERE digest=proof->>'session_digest' AND NOT revoked
        AND credential_epoch=instance.credential_epoch AND session_epoch=instance.session_epoch
        AND expires_at>pg_catalog.clock_timestamp() FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    SELECT LEAST(session.expires_at,expires_at,pg_catalog.clock_timestamp()+interval '30 seconds')
      INTO deadline FROM dashboard_auth.csrf WHERE session_digest=session.digest
        AND digest=proof->>'csrf_digest' AND expires_at>pg_catalog.clock_timestamp() FOR UPDATE;
    IF deadline IS NULL THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    RETURN deadline;
END;
$custody_browser$;

CREATE OR REPLACE FUNCTION custody_admission.prepare(
    kind text, operation text, proof jsonb, selection jsonb, source_ref uuid DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_prepare$
#variable_conflict use_variable
DECLARE c jsonb; deadline timestamptz; process_id uuid; process record; source record; result uuid; issuer uuid;
BEGIN
    IF kind='browser' THEN
        -- The restricted role cannot borrow the host proof. Actual session and
        -- CSRF rows/config are checked while the auth singleton is locked.
        IF custody_admission.caller_role()<>(SELECT oid FROM pg_catalog.pg_roles WHERE rolname='dashboard_auth_api') THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        deadline:=custody_admission.browser_check(proof);
        process_id:=custody_admission.writer();
        IF operation<>ALL(ARRAY['hold','release','replaced','revoke_sessions','eligibility']) THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
    ELSIF kind='host' THEN
        PERFORM custody_admission.host_only();
        PERFORM FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
        deadline:=pg_catalog.clock_timestamp()+interval '30 seconds';
        IF operation<>ALL(ARRAY['hold','release','replaced','revoke_sessions','eligibility']) OR proof<>'{}'::jsonb THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
    ELSE
        -- Both source branches are private engine calls, not runtime EXEC.
        PERFORM FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
        process_id:=custody_admission.writer();
        SELECT * INTO process FROM custody_admission.processes WHERE processes.process_id=prepare.process_id;
        SELECT * INTO source FROM custody_admission.sources WHERE sources.source_ref=prepare.source_ref;
        IF NOT FOUND OR process.logical_actor<>'switchboard'
           OR source.expires_at<=pg_catalog.clock_timestamp()
           OR NOT EXISTS(SELECT FROM custody_admission.calls call_row
               WHERE call_row.source_ref=source.source_ref AND destination_process=process_id
                 AND state='armed' AND challenge_expires_at>pg_catalog.clock_timestamp()
                 AND expires_at>pg_catalog.clock_timestamp()) THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        deadline:=LEAST(source.expires_at,pg_catalog.clock_timestamp()+interval '30 seconds');
        issuer:=(source.projection->>'issuer_target')::uuid;
        IF kind='endpoint_lock' THEN
            IF operation<>'hold' OR source.source_family<>'accepted_ingress'
               OR source.projection->>'intent'<>'LOCK'
               OR source.projection->>'owner_entity_id' IS NULL
               OR source.projection->>'issuer_target' IS NULL OR proof<>'{}'::jsonb
               OR selection->'target_set' IS DISTINCT FROM source.projection->'target_set'
               OR selection->'target_set_version' IS DISTINCT FROM source.projection->'target_set_version' THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
        ELSIF kind<>'security_answer' OR operation<>ALL(ARRAY['yes','no']) THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
    END IF;
    c:=custody_admission.current_control();
    IF operation='eligibility' THEN
        -- This fresh private ticket authorizes only reading the original
        -- durable result. It cannot remint the original mutation/generations.
        PERFORM custody_admission.closed(selection,ARRAY[
            'target_set','target_set_version','result_command_id']);
        IF kind NOT IN ('browser','host') OR selection->'target_set'<>'[]'::jsonb
           OR selection->'target_set_version'<>'1'::jsonb
           OR NOT EXISTS(SELECT FROM custody_admission.commands original
               WHERE original.command_id=(selection->>'result_command_id')::uuid
                 AND original.operation<>'eligibility') THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
    ELSIF selection ? 'result_command_id' THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    PERFORM custody_admission.lock_targets(selection,issuer);
    IF kind IN ('endpoint_lock','security_answer') AND EXISTS(
        SELECT FROM custody_admission.targets t
        JOIN pg_catalog.jsonb_array_elements(selection->'target_set') v
          ON t.target_id=(v->>'target_id')::uuid
        WHERE t.owner_entity_id IS DISTINCT FROM (source.projection->>'owner_entity_id')::uuid
    ) THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    result:=CASE WHEN kind IN ('endpoint_lock','security_answer') THEN source_ref ELSE pg_catalog.gen_random_uuid() END;
    INSERT INTO custody_admission.commands(command_id,kind,operation,selection,selection_digest,
        proof,source_ref,control_epoch,restore_epoch,expires_at)
    VALUES(result,kind,operation,selection,custody_admission.binding_digest(selection),proof,source_ref,
        (c->>'control_epoch')::bigint,(c->>'restore_epoch')::uuid,deadline)
    ON CONFLICT(command_id) DO NOTHING;
    IF EXISTS(SELECT FROM custody_admission.commands command
       WHERE command_id=result AND (command.kind<>kind OR command.operation<>operation
         OR command.selection<>selection OR command.proof<>proof
         OR command.source_ref IS DISTINCT FROM prepare.source_ref)) THEN
        RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
    END IF;
    RETURN pg_catalog.jsonb_build_object('command_id',result,'expires_at',deadline,
        'selection_digest',custody_admission.binding_digest(selection));
END;
$custody_prepare$;

CREATE OR REPLACE FUNCTION custody_admission.command_current(command_id uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_command_current$
DECLARE command record; c jsonb; source record; question record; result jsonb;
BEGIN
    SELECT * INTO command FROM custody_admission.commands WHERE commands.command_id=command_current.command_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    IF command.kind='browser' THEN PERFORM custody_admission.browser_check(command.proof);
    ELSE PERFORM FROM dashboard_auth.instance WHERE singleton FOR UPDATE; END IF;
    c:=custody_admission.current_control();
    IF command.expires_at<=pg_catalog.clock_timestamp()
       OR command.control_epoch<>(c->>'control_epoch')::bigint
       OR command.restore_epoch<>(c->>'restore_epoch')::uuid THEN
        RAISE EXCEPTION 'custody expired' USING ERRCODE='42501';
    END IF;
    IF command.kind IN ('endpoint_lock','security_answer') THEN
        SELECT * INTO source FROM custody_admission.sources WHERE source_ref=command.source_ref;
        IF NOT FOUND OR source.expires_at<=pg_catalog.clock_timestamp() THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        PERFORM custody_admission.require_origin_current(source.source_ref);
        -- This helper runs before targets and again after waits. Hold the actual
        -- shared owner anchor, not only a role value read before COMMIT.
        PERFORM FROM public.entities e
          WHERE e.id=(source.projection->>'owner_entity_id')::uuid FOR UPDATE;
        IF NOT FOUND OR NOT EXISTS(SELECT FROM public.entities e
            WHERE e.id=(source.projection->>'owner_entity_id')::uuid
              AND 'owner'=ANY(COALESCE(e.roles,ARRAY[]::text[]))) THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        IF command.kind='endpoint_lock' AND (
            command.operation<>'hold' OR source.source_family<>'accepted_ingress'
            OR source.projection->>'intent'<>'LOCK' OR command.proof<>'{}'::jsonb
        ) THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
        IF command.kind='security_answer' THEN
            PERFORM custody_admission.closed(command.proof,ARRAY[
                'question_id','callback_source_ref','answer','token_digest'
            ]);
            SELECT * INTO question FROM custody_admission.questions
              WHERE question_id=(command.proof->>'question_id')::uuid;
            IF NOT FOUND OR question.expires_at<=pg_catalog.clock_timestamp()
               OR question.decision NOT IN ('pending',command.operation)
               OR command.proof->>'answer'<>command.operation
               OR command.proof->>'callback_source_ref'<>command.source_ref::text
               OR source.projection->>'question_ref'<>question.question_id::text
               OR source.projection->>'answer'<>command.operation
               OR source.projection->>'token_digest'<>command.proof->>'token_digest'
               OR question.recipient_digest IS DISTINCT FROM source.projection->>'origin_digest'
               OR (CASE command.operation WHEN 'yes' THEN question.yes_digest ELSE question.no_digest END)
                    IS DISTINCT FROM command.proof->>'token_digest'
               OR question.target_set IS DISTINCT FROM command.selection->'target_set'
               OR question.target_set_version<>(command.selection->>'target_set_version')::bigint THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
        END IF;
    END IF;
    RETURN pg_catalog.to_jsonb(command);
END;
$custody_command_current$;

CREATE OR REPLACE FUNCTION custody_admission.command_source_current(source_ref uuid,operation jsonb)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_command_source$
DECLARE source record; command jsonb; bound_id uuid;
BEGIN
    SELECT * INTO source FROM custody_admission.sources
      WHERE sources.source_ref=command_source_current.source_ref;
    IF NOT FOUND THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    IF source.source_family NOT IN ('owner_command','host_command') THEN
        -- An ingress/callback source can read only its own server-allocated
        -- command. A copied receipt locator is never an owning read grant.
        IF operation->>'method'='custody.result' THEN
            IF operation->>'command_id' IS DISTINCT FROM source.source_ref::text
               OR operation->'arguments'->>'command_id' IS DISTINCT FROM source.source_ref::text THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
            command:=custody_admission.command_current(source.source_ref);
            IF command->>'source_ref' IS DISTINCT FROM source.source_ref::text THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
        END IF;
        RETURN;
    END IF;
    IF source.projection->>'locator' !~ '^command:[0-9a-f-]{36}$' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    command:=custody_admission.command_current(
        pg_catalog.substring(source.projection->>'locator',9)::uuid);
    PERFORM custody_admission.closed(operation->'arguments',ARRAY['command_id']);
    IF command->>'operation'='eligibility' THEN
        bound_id:=(command->'selection'->>'result_command_id')::uuid;
        IF operation->>'operation' IS DISTINCT FROM 'eligibility'
           OR operation->>'method' IS DISTINCT FROM 'custody.result' THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
    ELSE
        bound_id:=(command->>'command_id')::uuid;
        IF operation->>'operation' IS DISTINCT FROM command->>'operation'
           OR operation->>'method' IS NULL
           OR operation->>'method'<>ALL(ARRAY['custody.commit','custody.result']) THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
    END IF;
    IF operation->>'command_id' IS DISTINCT FROM bound_id::text
       OR operation->'arguments'->>'command_id' IS DISTINCT FROM bound_id::text
       OR operation->'target_set' IS DISTINCT FROM command->'selection'->'target_set'
       OR operation->'target_set_version' IS DISTINCT FROM command->'selection'->'target_set_version'
       OR source.projection->>'content_digest' IS DISTINCT FROM command->>'selection_digest' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
END;
$custody_command_source$;

CREATE OR REPLACE FUNCTION custody_admission.commit_command(command_id uuid,call_ref uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_commit$
#variable_conflict use_variable
DECLARE
    c jsonb; command record; call_row record; source record; process record;
    process_id uuid; target jsonb; actual record; episode record; case_id uuid;
    correlation text; result jsonb; episodes jsonb:='[]'; selected_case uuid; issuer uuid;
    scope record; case_kind text; case_version bigint; expected_case_version bigint;
    containment_unknown boolean:=false; complete_empty boolean:=false;
BEGIN
    -- Auth FIRST, never an authorize() verdict from another acquisition. Even
    -- non-browser command branches share this fixed order with global revocation.
    PERFORM FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
    process_id:=custody_admission.writer();
    PERFORM custody_admission.require_verified_call(call_ref);
    SELECT * INTO process FROM custody_admission.processes WHERE processes.process_id=commit_command.process_id;
    IF process.logical_actor<>'switchboard' THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    c:=custody_admission.current_control();
    SELECT * INTO call_row FROM custody_admission.calls WHERE call_id=call_ref;
    IF NOT FOUND OR call_row.destination_process<>process_id
       OR call_row.command_id IS DISTINCT FROM command_id OR call_row.state NOT IN ('armed','committed')
       OR call_row.challenge_expires_at<=pg_catalog.clock_timestamp()
       OR call_row.expires_at<=pg_catalog.clock_timestamp()
       OR NOT custody_admission.process_current(call_row.issuer_process)
       OR call_row.control_epoch<>(c->>'control_epoch')::bigint
       OR call_row.restore_epoch<>(c->>'restore_epoch')::uuid THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    SELECT * INTO source FROM custody_admission.sources WHERE source_ref=call_row.source_ref;
    IF NOT FOUND OR source.expires_at<=pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    PERFORM custody_admission.command_source_current(source.source_ref,call_row.operation);
    IF call_row.operation->>'method'<>'custody.commit'
       OR call_row.operation->>'operation'='eligibility' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    issuer:=(source.projection->>'issuer_target')::uuid;
    SELECT * INTO command FROM custody_admission.commands WHERE commands.command_id=commit_command.command_id;
    IF NOT FOUND AND call_row.operation->>'operation'='hold'
       AND source.source_family='accepted_ingress' THEN
        -- Server source_ref is allocated before mint and is the stable private
        -- cheap-command identity. The armed body already binds this exact ID.
        PERFORM custody_admission.prepare('endpoint_lock','hold','{}',
            pg_catalog.jsonb_build_object('target_set',source.projection->'target_set',
                'target_set_version',source.projection->'target_set_version','reason','lost'),source.source_ref);
        SELECT * INTO command FROM custody_admission.commands WHERE commands.command_id=commit_command.command_id;
    END IF;
    IF NOT FOUND
       OR command.kind IN ('endpoint_lock','security_answer')
            AND command.source_ref IS DISTINCT FROM source.source_ref
       OR command.selection->'target_set' IS DISTINCT FROM call_row.operation->'target_set'
       OR command.selection->'target_set_version' IS DISTINCT FROM call_row.operation->'target_set_version'
       OR command.operation IS DISTINCT FROM call_row.operation->>'operation' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    -- Provider inventory is a control-group lock before targets; caller supplied
    -- completeness never suffices. Unknown no still records answer/case evidence.
    IF command.selection ? 'provider' THEN
        SELECT * INTO actual FROM custody_admission.provider_inventory
          WHERE owner_entity_id=(source.projection->>'owner_entity_id')::uuid
            AND provider=command.selection->>'provider' FOR UPDATE;
        IF NOT FOUND OR NOT actual.complete THEN
            IF command.operation<>'no' THEN
                RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
            END IF;
            -- Commit the actual NO/case/evidence, but never acknowledge a
            -- partial guessed set as complete containment or mutate that set.
            containment_unknown:=true;
        ELSE
            IF actual.version<>(command.selection->>'provider_version')::bigint
               OR actual.members IS DISTINCT FROM command.selection->'target_set' THEN
                -- An old answer is not transplanted to a changed inventory.
                RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
            END IF;
            complete_empty:=actual.members='[]'::jsonb;
        END IF;
    END IF;
    -- Acquire/check the command's complete auth species before target locks;
    -- repeat clock/currentness checks after those locks have finished waiting.
    PERFORM custody_admission.command_current(command_id);
    selected_case:=(command.selection->>'case_id')::uuid;
    IF selected_case IS NOT NULL THEN
        -- Pre-read membership BEFORE complete target union locking. The same
        -- immutable version must survive the later case lock; no late target.
        SELECT membership_version INTO expected_case_version
          FROM custody_admission.case_scopes WHERE case_id=selected_case;
        IF expected_case_version IS NULL
           OR expected_case_version<>(command.selection->>'target_set_version')::bigint THEN
            RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
        END IF;
    END IF;
    PERFORM custody_admission.lock_targets(command.selection,issuer);
    PERFORM custody_admission.command_current(command_id);
    -- Clock and both actual processes follow every target/auth wait. A check
    -- made before a blocking lock is not a committing currentness verdict.
    IF NOT custody_admission.process_current(call_row.issuer_process)
       OR NOT custody_admission.process_current(call_row.destination_process)
       OR call_row.challenge_expires_at<=pg_catalog.clock_timestamp()
       OR call_row.expires_at<=pg_catalog.clock_timestamp()
       OR source.expires_at<=pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'custody expired' USING ERRCODE='42501';
    END IF;
    PERFORM custody_admission.writer();
    SELECT * INTO command FROM custody_admission.commands WHERE commands.command_id=commit_command.command_id FOR UPDATE;
    SELECT * INTO call_row FROM custody_admission.calls WHERE call_id=call_ref FOR UPDATE;
    IF command.state='committed' THEN
        SELECT r.result INTO result FROM custody_admission.receipts r WHERE r.command_id=command_id;
        IF result IS NULL THEN RAISE EXCEPTION 'custody unknown' USING ERRCODE='42501'; END IF;
        RETURN result;
    END IF;
    IF command.state<>'prepared' THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    IF command.kind='security_answer' THEN
        PERFORM FROM custody_admission.questions WHERE question_id=(command.proof->>'question_id')::uuid FOR UPDATE;
        UPDATE custody_admission.questions SET decision=command.operation,decision_command_id=command_id
          WHERE question_id=(command.proof->>'question_id')::uuid AND decision='pending';
    END IF;
    IF command.operation IN ('hold','no') THEN
        IF pg_catalog.jsonb_array_length(command.selection->'target_set')=0 AND command.operation='hold' THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        FOR target IN SELECT value FROM pg_catalog.jsonb_array_elements(command.selection->'target_set')
          WHERE NOT containment_unknown LOOP
            SELECT * INTO actual FROM custody_admission.targets WHERE target_id=(target->>'target_id')::uuid;
            SELECT * INTO episode FROM public.custody_holds WHERE target_id=actual.target_id AND released_at IS NULL FOR UPDATE;
            IF FOUND THEN
                IF actual.generation NOT IN ((target->>'generation')::bigint,(target->>'generation')::bigint+1) THEN
                    RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
                END IF;
            ELSE
                IF actual.generation<>(target->>'generation')::bigint THEN
                    RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
                END IF;
                UPDATE custody_admission.targets SET generation=generation+1 WHERE target_id=actual.target_id;
                INSERT INTO public.custody_holds(target_id,generation,reason,source_command_id,observed_incident_at)
                VALUES(actual.target_id,actual.generation+1,COALESCE(command.selection->>'reason','disowned_access'),
                    command_id,(command.selection->>'observed_incident_at')::timestamptz)
                RETURNING * INTO episode;
            END IF;
            episodes:=episodes||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'episode_id',episode.episode_id,'target_id',episode.target_id,'generation',episode.generation));
        END LOOP;
        -- Required canonical case/evidence use this SAME connection/transaction.
        -- No public fleet_cases pool wrapper or optional evidence outage can fake ACK.
        case_kind:=CASE WHEN command.operation='hold'
            AND COALESCE(command.selection->>'reason','lost') IN ('lost','stolen','replaced')
            THEN 'lost_device' ELSE 'account_security' END;
        IF case_kind='account_security' THEN
            IF command.operation='no' AND source.projection->>'event_ref' IS NULL THEN
                RAISE EXCEPTION 'custody unavailable' USING ERRCODE='55000';
            END IF;
            correlation:='account_security:'||COALESCE(source.projection->>'event_ref',command_id::text);
        ELSE
            -- Actual held generations are stable across repeated fresh LOCK
            -- requests; caller pre-hold expected generations are not the key.
            correlation:='custody_lost:'||custody_admission.binding_digest(
                pg_catalog.jsonb_build_object('episodes',episodes));
        END IF;
        SELECT id INTO case_id FROM public.fleet_cases WHERE correlation_key=correlation AND state<>'closed' FOR UPDATE;
        IF case_id IS NULL THEN
            INSERT INTO public.fleet_cases(correlation_key,posture) VALUES(correlation,'active') RETURNING id INTO case_id;
        END IF;
        INSERT INTO custody_admission.case_scopes(case_id,kind,owner_entity_id)
          VALUES(case_id,case_kind,(source.projection->>'owner_entity_id')::uuid)
          ON CONFLICT(case_id) DO NOTHING;
        SELECT * INTO scope FROM custody_admission.case_scopes
          WHERE case_scopes.case_id=commit_command.case_id FOR UPDATE;
        IF scope.kind<>case_kind
           OR scope.owner_entity_id IS DISTINCT FROM (source.projection->>'owner_entity_id')::uuid
           OR NOT EXISTS(SELECT FROM public.fleet_cases WHERE id=case_id AND state<>'closed') THEN
            RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
        END IF;
        case_version:=scope.membership_version;
        IF EXISTS(SELECT FROM pg_catalog.jsonb_array_elements(episodes) v
            WHERE NOT EXISTS(SELECT FROM custody_admission.case_members m
                WHERE m.case_id=case_id AND m.target_id=(v->>'target_id')::uuid
                  AND m.generation=(v->>'generation')::bigint)) THEN
            case_version:=case_version+1;
            UPDATE custody_admission.case_scopes SET membership_version=case_version
              WHERE case_scopes.case_id=commit_command.case_id;
        END IF;
        FOR target IN SELECT pg_catalog.jsonb_array_elements(episodes) LOOP
            -- One episode can be evidence/member of separate cases. Its public
            -- case_id remains the first association; the explicit private
            -- generation membership is authoritative for selected case closure.
            UPDATE public.custody_holds SET case_id=COALESCE(custody_holds.case_id,commit_command.case_id)
              WHERE episode_id=(target->>'episode_id')::uuid;
            INSERT INTO custody_admission.case_members(case_id,target_id,generation,membership_version)
              VALUES(case_id,(target->>'target_id')::uuid,(target->>'generation')::bigint,
                  case_version) ON CONFLICT DO NOTHING;
        END LOOP;
        INSERT INTO public.fleet_case_evidence(case_id,contributor,kind,ref,payload)
        VALUES(case_id,'switchboard','custody_recovery',command_id::text,
            pg_catalog.jsonb_build_object('command_id',command_id,'containment',
                CASE WHEN containment_unknown THEN 'unknown'
                     WHEN pg_catalog.jsonb_array_length(episodes)>0 THEN 'held'
                     WHEN complete_empty THEN 'no_known_target' ELSE 'unknown' END,
                'recovery_doors',ARRAY['sim_carrier','account_sign_out']))
        ON CONFLICT(case_id,contributor,kind,ref) DO NOTHING;
    ELSIF command.operation IN ('release','replaced') THEN
        IF pg_catalog.jsonb_array_length(command.selection->'target_set')=0 THEN
            RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
        END IF;
        FOR target IN SELECT pg_catalog.jsonb_array_elements(command.selection->'target_set') LOOP
            UPDATE public.custody_holds SET released_at=pg_catalog.clock_timestamp(),
                disposition=CASE command.operation WHEN 'release' THEN 'released' ELSE 'replaced' END,
                release_command_id=command_id
              WHERE target_id=(target->>'target_id')::uuid AND generation=(target->>'generation')::bigint
                AND released_at IS NULL RETURNING * INTO episode;
            IF NOT FOUND THEN RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001'; END IF;
            episodes:=episodes||pg_catalog.jsonb_build_array(pg_catalog.jsonb_build_object(
                'episode_id',episode.episode_id,'target_id',episode.target_id,'generation',episode.generation));
        END LOOP;
        IF selected_case IS NOT NULL THEN
            -- Never infer disposal from selected single target or ordinary case close.
            PERFORM FROM public.fleet_cases WHERE id=selected_case FOR UPDATE;
            SELECT * INTO scope FROM custody_admission.case_scopes WHERE case_scopes.case_id=selected_case FOR UPDATE;
            IF NOT FOUND OR scope.membership_version<>expected_case_version THEN
                RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001';
            END IF;
            IF scope.kind='lost_device'
               AND EXISTS(SELECT FROM custody_admission.case_members WHERE case_id=selected_case)
               AND NOT EXISTS(SELECT FROM custody_admission.case_members m
                 LEFT JOIN public.custody_holds h ON h.target_id=m.target_id AND h.generation=m.generation
                 WHERE m.case_id=selected_case AND (h.episode_id IS NULL OR h.released_at IS NULL)) THEN
                UPDATE public.fleet_cases SET state='closed',outcome='custody_recovered',
                    closed_at=pg_catalog.clock_timestamp(),updated_at=pg_catalog.clock_timestamp()
                  WHERE id=selected_case AND state<>'closed';
            END IF;
        END IF;
    ELSIF command.operation='revoke_sessions' THEN
        UPDATE dashboard_auth.instance SET session_epoch=session_epoch+1 WHERE singleton;
        UPDATE dashboard_auth.sessions SET revoked=true WHERE NOT revoked;
    ELSIF command.operation<>'yes' THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    result:=pg_catalog.jsonb_build_object('status','committed','command_id',command_id,
        'operation',command.operation,'episodes',episodes,'case_id',COALESCE(case_id,selected_case),
        'case_membership_version',COALESCE(case_version,expected_case_version),
        'containment',CASE WHEN containment_unknown THEN 'unknown'
            WHEN pg_catalog.jsonb_array_length(episodes)>0 AND command.operation IN ('hold','no') THEN 'held'
            WHEN complete_empty THEN 'no_known_target' ELSE NULL END);
    INSERT INTO custody_admission.receipts(command_id,result) VALUES(command_id,result);
    UPDATE custody_admission.commands SET state='committed',result=commit_command.result WHERE commands.command_id=commit_command.command_id;
    UPDATE custody_admission.calls SET state='committed',result=commit_command.result WHERE call_id=call_ref;
    RETURN result;
END;
$custody_commit$;

CREATE OR REPLACE FUNCTION custody_admission.admit_write(source_ref uuid,operation text,target_set jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_admit$
DECLARE process_id uuid; source record; process record; target jsonb; selection jsonb;
    call_row record; actual_backend record; source_owned boolean;
BEGIN
    process_id:=custody_admission.writer();
    SELECT * INTO process FROM custody_admission.processes WHERE processes.process_id=admit_write.process_id;
    SELECT * INTO source FROM custody_admission.sources WHERE sources.source_ref=admit_write.source_ref;
    IF NOT FOUND OR source.expires_at<=pg_catalog.clock_timestamp()
       OR NOT process.operations ? operation
       OR target_set IS DISTINCT FROM source.projection->'target_set' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    source_owned:=source.logical_actor=process.logical_actor AND EXISTS(
        SELECT FROM custody_admission.source_associations
          WHERE source_associations.source_ref=admit_write.source_ref
            AND source_associations.process_id=admit_write.process_id);
    IF NOT source_owned THEN
        -- Cross-butler source transport is registered MCP only. This metadata
        -- records the actual online guard on THIS acquired writer; it is not
        -- a delivery bus or a public actor/UUID source claim.
        SELECT * INTO actual_backend FROM pg_catalog.pg_stat_activity
          WHERE pid=pg_catalog.pg_backend_pid();
        SELECT calls.* INTO call_row FROM custody_admission.connections connection
          JOIN custody_admission.calls calls ON calls.call_id=connection.verified_call_id
          WHERE connection.database_oid=actual_backend.datid
            AND connection.backend_pid=actual_backend.pid
            AND connection.backend_start=actual_backend.backend_start
            AND connection.login_oid=actual_backend.usesysid
            AND connection.role_oid=custody_admission.caller_role()
            AND connection.process_id=admit_write.process_id;
        IF NOT FOUND OR call_row.source_ref<>source.source_ref
           OR call_row.destination_process<>process_id
           OR call_row.operation->>'operation' IS DISTINCT FROM operation
           OR call_row.operation->'target_set' IS DISTINCT FROM target_set
           OR call_row.operation->'target_set_version'
                IS DISTINCT FROM source.projection->'target_set_version' THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
        PERFORM custody_admission.protocol('verify',pg_catalog.jsonb_build_object(
            'call_ref',call_row.call_id,'challenge_ref',call_row.challenge_ref,
            'operation_digest',call_row.operation_digest));
    END IF;
    PERFORM custody_admission.require_origin_current(source.source_ref);
    selection:=pg_catalog.jsonb_build_object('target_set',target_set,
        'target_set_version',source.projection->'target_set_version');
    PERFORM custody_admission.lock_targets(selection,(source.projection->>'issuer_target')::uuid);
    PERFORM custody_admission.writer();
    IF source.expires_at<=pg_catalog.clock_timestamp() THEN
        RAISE EXCEPTION 'custody expired' USING ERRCODE='42501';
    END IF;
    PERFORM custody_admission.require_origin_current(source.source_ref);
    FOR target IN SELECT pg_catalog.jsonb_array_elements(target_set) LOOP
        IF EXISTS(SELECT FROM custody_admission.targets WHERE target_id=(target->>'target_id')::uuid
              AND generation<>(target->>'generation')::bigint)
           OR EXISTS(SELECT FROM public.custody_holds WHERE target_id=(target->>'target_id')::uuid AND released_at IS NULL) THEN
            RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
        END IF;
    END LOOP;
    RETURN pg_catalog.jsonb_build_object('admitted',true);
END;
$custody_admit$;

CREATE OR REPLACE FUNCTION custody_admission.mark_provider_start(
    source_ref uuid,call_ref uuid,operation_digest text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_start$
#variable_conflict use_variable
DECLARE process_id uuid; process record; call_row record; effect record; effect_namespace text; effect_id uuid; effect_binding text; control jsonb;
BEGIN
    process_id:=custody_admission.writer();
    PERFORM custody_admission.require_verified_call(call_ref);
    control:=custody_admission.current_control();
    SELECT * INTO process FROM custody_admission.processes WHERE processes.process_id=mark_provider_start.process_id;
    SELECT * INTO call_row FROM custody_admission.calls WHERE call_id=call_ref;
    IF NOT FOUND OR call_row.destination_process<>process_id
       OR call_row.source_ref<>source_ref OR call_row.operation_digest<>operation_digest
       OR call_row.state<>'armed' OR call_row.challenge_expires_at<=pg_catalog.clock_timestamp()
       OR call_row.expires_at<=pg_catalog.clock_timestamp()
       OR NOT custody_admission.process_current(call_row.issuer_process)
       OR NOT process.operations ? 'provider_start'
       OR call_row.control_epoch<>(control->>'control_epoch')::bigint
       OR call_row.restore_epoch<>(control->>'restore_epoch')::uuid
       OR call_row.operation->>'operation'<>'provider_start' THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    PERFORM custody_admission.require_origin_current(call_row.source_ref);
    -- Receiver association is a call grant, not a fabricated owning source.
    PERFORM custody_admission.lock_targets(pg_catalog.jsonb_build_object(
        'target_set',call_row.operation->'target_set',
        'target_set_version',call_row.operation->'target_set_version'),
        (SELECT (s.projection->>'issuer_target')::uuid FROM custody_admission.sources s
         WHERE s.source_ref=call_row.source_ref
           AND (s.source_family='accepted_ingress' OR s.projection ? 'answer')));
    IF EXISTS(SELECT FROM pg_catalog.jsonb_array_elements(call_row.operation->'target_set') v
       JOIN custody_admission.targets t ON t.target_id=(v->>'target_id')::uuid
       WHERE t.generation<>(v->>'generation')::bigint
          OR EXISTS(SELECT FROM public.custody_holds h WHERE h.target_id=t.target_id AND h.released_at IS NULL)) THEN
        RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
    END IF;
    -- The owning effect marker is a final admission, not the earlier guard
    -- verdict. Recheck live issuer/receiver and exact source after target waits.
    PERFORM custody_admission.writer();
    IF NOT custody_admission.process_current(call_row.issuer_process)
       OR NOT custody_admission.process_current(call_row.destination_process)
       OR call_row.expires_at<=pg_catalog.clock_timestamp()
       OR call_row.challenge_expires_at<=pg_catalog.clock_timestamp()
       OR NOT EXISTS(SELECT FROM custody_admission.sources s
           WHERE s.source_ref=mark_provider_start.source_ref
             AND s.expires_at>pg_catalog.clock_timestamp()) THEN
        RAISE EXCEPTION 'custody expired' USING ERRCODE='42501';
    END IF;
    PERFORM custody_admission.require_origin_current(call_row.source_ref);
    effect_namespace:=call_row.operation->>'effect_namespace';
    effect_id:=(call_row.operation->>'effect_id')::uuid;
    IF effect_namespace !~ '^[a-z][a-z0-9_]{0,63}$' OR effect_id IS NULL
       OR call_row.operation->>'content_digest' !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'custody invalid' USING ERRCODE='22023';
    END IF;
    effect_binding:=custody_admission.binding_digest(pg_catalog.jsonb_build_object(
        'method',call_row.operation->'method','arguments',call_row.operation->'arguments',
        'targets',call_row.operation->'target_set','content_digest',call_row.operation->'content_digest'));
    INSERT INTO custody_admission.effects(actor,namespace,effect_id,source_ref,binding_digest,started_at)
    VALUES(process.logical_actor,effect_namespace,effect_id,source_ref,effect_binding,pg_catalog.clock_timestamp())
    ON CONFLICT(actor,namespace,effect_id) DO NOTHING RETURNING * INTO effect;
    IF NOT FOUND THEN
        SELECT * INTO effect FROM custody_admission.effects e
          WHERE e.actor=process.logical_actor AND e.namespace=effect_namespace AND e.effect_id=mark_provider_start.effect_id;
        IF effect.binding_digest<>effect_binding THEN RAISE EXCEPTION 'custody conflict' USING ERRCODE='40001'; END IF;
        RETURN pg_catalog.jsonb_build_object('may_start',false,'status','already_possible_start');
    END IF;
    RETURN pg_catalog.jsonb_build_object('may_start',true,'effect_id',effect_id,'started_at',effect.started_at);
END;
$custody_start$;

-- Exact bounded SQL ABIs. The two MCP operations are registered in the owning
-- service; these wrappers do not turn arbitrary JSON into a public dispatcher.
CREATE OR REPLACE FUNCTION public.custody_anchor_begin() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('anchor_begin','{}'::jsonb)
$$;
CREATE OR REPLACE FUNCTION public.custody_anchor_renew() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('anchor_renew','{}'::jsonb)
$$;
CREATE OR REPLACE FUNCTION public.custody_connection_begin() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('connection_begin','{}'::jsonb)
$$;
CREATE OR REPLACE FUNCTION public.custody_bind_connection(writer_nonce text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('bind_connection',pg_catalog.jsonb_build_object('writer_nonce',writer_nonce))
$$;
CREATE OR REPLACE FUNCTION public.custody_connection_finish(writer_nonce text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('connection_finish',pg_catalog.jsonb_build_object('writer_nonce',writer_nonce))
$$;
CREATE OR REPLACE FUNCTION public.custody_connection_unbind(acquisition_generation bigint) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('connection_unbind',pg_catalog.jsonb_build_object('acquisition_generation',acquisition_generation))
$$;
CREATE OR REPLACE FUNCTION public.custody_source_register(source_family text,projection jsonb) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('source_register',pg_catalog.jsonb_build_object('source_family',source_family,'projection',projection))
$$;
CREATE OR REPLACE FUNCTION public.custody_mint(source_ref uuid,operation jsonb,destination_actor text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('mint',pg_catalog.jsonb_build_object('source_ref',source_ref,'operation',operation,'destination_actor',destination_actor))
$$;
CREATE OR REPLACE FUNCTION public.custody_challenge(call_ref uuid,operation_digest text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('challenge',pg_catalog.jsonb_build_object('call_ref',call_ref,'operation_digest',operation_digest))
$$;
CREATE OR REPLACE FUNCTION public.custody_respond(call_ref uuid,challenge_ref text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('respond',pg_catalog.jsonb_build_object('call_ref',call_ref,'challenge_ref',challenge_ref))
$$;
CREATE OR REPLACE FUNCTION public.custody_verify(call_ref uuid,challenge_ref text,operation_digest text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('verify',pg_catalog.jsonb_build_object('call_ref',call_ref,'challenge_ref',challenge_ref,'operation_digest',operation_digest))
$$;
CREATE OR REPLACE FUNCTION public.custody_commit_command(command_id uuid,call_ref uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.commit_command(command_id,call_ref)
$$;
CREATE OR REPLACE FUNCTION public.custody_admit_write(source_ref uuid,operation text,target_set jsonb) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.admit_write(source_ref,operation,target_set)
$$;
CREATE OR REPLACE FUNCTION public.custody_result_read(command_id uuid,call_ref uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.protocol('result_read',pg_catalog.jsonb_build_object('command_id',command_id,'call_ref',call_ref))
$$;
CREATE OR REPLACE FUNCTION public.custody_mark_provider_start(source_ref uuid,call_ref uuid,operation_digest text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.mark_provider_start(source_ref,call_ref,operation_digest)
$$;
CREATE OR REPLACE FUNCTION custody_admission.endpoint_lock_prepare(source_ref uuid,selection jsonb) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
 SELECT custody_admission.prepare('endpoint_lock','hold','{}'::jsonb,selection,source_ref)
$$;
CREATE OR REPLACE FUNCTION custody_admission.security_answer_prepare(
    question_id uuid,callback_source_ref uuid,answer text,token_digest text
) RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_answer_prepare$
DECLARE question record; selection jsonb;
BEGIN
    -- The engine guard verified the actual source/call. Tokens bind one verb,
    -- recipient/event/source/target generation and cannot act as approval keys.
    PERFORM FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
    PERFORM custody_admission.current_control();
    SELECT * INTO question FROM custody_admission.questions q WHERE q.question_id=security_answer_prepare.question_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'custody refused' USING ERRCODE='42501'; END IF;
    selection:=pg_catalog.jsonb_build_object('target_set',question.target_set,'target_set_version',question.target_set_version,
        'reason','disowned_access');
    RETURN custody_admission.prepare('security_answer',answer,pg_catalog.jsonb_build_object(
        'question_id',question_id,'callback_source_ref',callback_source_ref,'answer',answer,'token_digest',token_digest
    ),selection,callback_source_ref);
END;
$custody_answer_prepare$;

CREATE OR REPLACE FUNCTION custody_admission.finalize_interface() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_finalize$
DECLARE role record; function record; migration_role name; signature text;
BEGIN
    SELECT connecting_role INTO migration_role FROM custody_admission.bootstrap_configuration WHERE singleton;
    FOR function IN SELECT p.oid,n.nspname,p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid) args
        FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='custody_admission' OR n.nspname IN ('public','dashboard_auth') AND p.proname LIKE 'custody_%'
    LOOP
        signature:=pg_catalog.format('%I.%I(%s)',function.nspname,function.proname,function.args);
        EXECUTE 'REVOKE ALL ON FUNCTION '||signature||' FROM PUBLIC';
        FOR role IN SELECT rolname FROM pg_catalog.pg_roles WHERE oid<>current_user::regrole::oid LOOP
            EXECUTE pg_catalog.format('REVOKE ALL ON FUNCTION %s FROM %I',signature,role.rolname);
        END LOOP;
        IF function.nspname='public' THEN
            FOR role IN SELECT rolname FROM pg_catalog.pg_roles
                WHERE rolname ~ '^butler_[A-Za-z_][A-Za-z0-9_]*_rw$' OR rolname IN ('connector_writer','dashboard_auth_api') LOOP
                EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION %s TO %I',signature,role.rolname);
            END LOOP;
        ELSIF function.nspname='dashboard_auth' AND function.proname='custody_prepare' THEN
            EXECUTE 'GRANT EXECUTE ON FUNCTION '||signature||' TO dashboard_auth_api';
        ELSIF function.proname IN ('host_enroll','host_revoke','host_revoke_control',
                'install_interface','prove_interface','rollback_interface','custody_host_prepare','accepted_work') THEN
            EXECUTE pg_catalog.format('GRANT EXECUTE ON FUNCTION %s TO %I',signature,migration_role);
        END IF;
    END LOOP;
    REVOKE ALL ON SCHEMA custody_admission FROM PUBLIC;
    REVOKE ALL ON ALL TABLES IN SCHEMA custody_admission FROM PUBLIC;
    FOR role IN SELECT rolname FROM pg_catalog.pg_roles WHERE oid<>current_user::regrole::oid LOOP
        EXECUTE pg_catalog.format('REVOKE ALL ON SCHEMA custody_admission FROM %I',role.rolname);
        EXECUTE pg_catalog.format('REVOKE ALL ON ALL TABLES IN SCHEMA custody_admission FROM %I',role.rolname);
    END LOOP;
    EXECUTE pg_catalog.format('GRANT USAGE ON SCHEMA custody_admission TO %I',migration_role);
    -- Public bootstrap widens table privileges deliberately. Forced RLS is the
    -- hold mutation boundary; only content-blind eligibility is an interface.
    IF pg_catalog.to_regclass('public.custody_holds') IS NOT NULL THEN
        REVOKE ALL ON public.custody_holds FROM PUBLIC;
        FOR role IN SELECT rolname FROM pg_catalog.pg_roles WHERE oid<>current_user::regrole::oid LOOP
            EXECUTE pg_catalog.format('REVOKE ALL ON public.custody_holds FROM %I',role.rolname);
        END LOOP;
    END IF;
END;
$custody_finalize$;

-- Fixed feature-owned schema identity, captured only by first trusted
-- provisioning. No caller-selected schema, table, query or replacement proof.
CREATE OR REPLACE FUNCTION custody_admission.schema_identity() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_schema$
    SELECT COALESCE(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
        'schema',n.nspname,'table',r.relname,'kind',r.relkind::text,
        'persistence',r.relpersistence::text,'owner_oid',r.relowner,
        'row_security',r.relrowsecurity,'force_row_security',r.relforcerowsecurity,
        'policies',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(p) ORDER BY p.policyname)
            FROM pg_catalog.pg_policies p WHERE p.schemaname=n.nspname AND p.tablename=r.relname),'[]'::jsonb),
        'columns',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'number',a.attnum,'name',a.attname,'type',pg_catalog.format_type(a.atttypid,a.atttypmod),
            'not_null',a.attnotnull,'identity',a.attidentity::text,'generated',a.attgenerated::text,
            'collation_oid',a.attcollation,'dropped',a.attisdropped,
            'default',pg_catalog.pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
            FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d
              ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=r.oid AND a.attnum>0),'[]'::jsonb),
        'constraints',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'name',c.conname,'type',c.contype::text,'definition',pg_catalog.pg_get_constraintdef(c.oid),
            'deferrable',c.condeferrable,'deferred',c.condeferred,'validated',c.convalidated,
            'local',c.conislocal,'inheritance',c.coninhcount,'no_inherit',c.connoinherit)
            ORDER BY c.conname) FROM pg_catalog.pg_constraint c WHERE c.conrelid=r.oid),'[]'::jsonb),
        'indexes',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'name',i.relname,'owner_oid',i.relowner,'definition',pg_catalog.pg_get_indexdef(i.oid),
            'unique',x.indisunique,'primary',x.indisprimary,'exclusion',x.indisexclusion,
            'valid',x.indisvalid,'ready',x.indisready,'live',x.indislive,
            'immediate',x.indimmediate,'replica_identity',x.indisreplident)
            ORDER BY i.relname) FROM pg_catalog.pg_index x
            JOIN pg_catalog.pg_class i ON i.oid=x.indexrelid WHERE x.indrelid=r.oid),'[]'::jsonb),
        'triggers',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
            'name',t.tgname,'enabled',t.tgenabled::text,'definition',pg_catalog.pg_get_triggerdef(t.oid))
            ORDER BY t.tgname) FROM pg_catalog.pg_trigger t
            WHERE t.tgrelid=r.oid AND NOT t.tgisinternal),'[]'::jsonb),
        'rules',COALESCE((SELECT pg_catalog.jsonb_agg(pg_catalog.pg_get_ruledef(w.oid) ORDER BY w.rulename)
            FROM pg_catalog.pg_rewrite w WHERE w.ev_class=r.oid),'[]'::jsonb)
        ) ORDER BY n.nspname,r.relname),'[]'::jsonb)
    FROM pg_catalog.pg_class r JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
    WHERE n.nspname='custody_admission' AND r.relkind IN ('r','p','v','m','f')
       OR n.nspname='public' AND r.relname='custody_holds'
$custody_schema$;

CREATE OR REPLACE FUNCTION custody_admission.prove_interface() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_prove$
DECLARE owner_oid oid; owner_name name; migration_role name;
    function record; count_functions integer; function_manifest jsonb;
BEGIN
    PERFORM custody_admission.host_only();
    SELECT bootstrap_owner_oid,connecting_role INTO owner_oid,migration_role
      FROM custody_admission.bootstrap_configuration WHERE singleton;
    SELECT rolname INTO owner_name FROM pg_catalog.pg_roles WHERE oid=owner_oid;
    IF owner_oid IS NULL OR NOT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE oid=owner_oid AND rolsuper)
       OR EXISTS(SELECT FROM pg_catalog.pg_namespace WHERE nspname='custody_admission' AND nspowner<>owner_oid)
       OR EXISTS(SELECT FROM pg_catalog.pg_class r JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
          WHERE n.nspname='custody_admission' AND r.relkind IN ('r','p') AND r.relowner<>owner_oid) THEN
        RAISE EXCEPTION 'custody invalid installed identity' USING ERRCODE='42501';
    END IF;
    IF EXISTS(SELECT FROM pg_catalog.pg_namespace n
        CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(n.nspacl,
            pg_catalog.acldefault('n',n.nspowner))) a
        LEFT JOIN pg_catalog.pg_roles recipient ON recipient.oid=a.grantee
        WHERE n.nspname='custody_admission' AND a.grantee<>owner_oid
          AND (recipient.rolname IS DISTINCT FROM migration_role
               OR a.privilege_type<>'USAGE' OR a.is_grantable))
       OR EXISTS(SELECT FROM pg_catalog.pg_class r
        JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
        CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(r.relacl,
            pg_catalog.acldefault('r',r.relowner))) a
        WHERE (n.nspname='custody_admission' AND r.relkind IN ('r','p')
               OR n.nspname='public' AND r.relname='custody_holds')
          AND a.grantee<>owner_oid) THEN
        RAISE EXCEPTION 'custody invalid installed grants' USING ERRCODE='42501';
    END IF;
    IF EXISTS(WITH RECURSIVE runtime_parents(runtime_oid,parent_oid) AS (
        SELECT r.oid,r.oid FROM pg_catalog.pg_roles r
          WHERE r.rolname ~ '^butler_[A-Za-z_][A-Za-z0-9_]*_rw$'
             OR r.rolname IN ('connector_writer','dashboard_auth_api')
        UNION
        SELECT chain.runtime_oid,m.roleid FROM runtime_parents chain
          JOIN pg_catalog.pg_auth_members m ON m.member=chain.parent_oid
        ) SELECT FROM runtime_parents chain JOIN pg_catalog.pg_roles parent
          ON parent.oid=chain.parent_oid
          WHERE parent.rolsuper OR parent.rolbypassrls OR parent.rolcreaterole
             OR parent.rolreplication OR parent.rolcreatedb
             OR parent.oid=owner_oid OR parent.rolname=migration_role
             OR parent.oid=(SELECT datdba FROM pg_catalog.pg_database
                 WHERE datname=pg_catalog.current_database())) THEN
        RAISE EXCEPTION 'custody invalid runtime role authority' USING ERRCODE='42501';
    END IF;
    -- Explicit ACL entries alone miss effective inherited/predefined access
    -- and column-only grants. Check the actual fixed protected relations for
    -- every existing runtime role; no arbitrary catalog selector is accepted.
    IF EXISTS(SELECT FROM pg_catalog.pg_roles runtime
        WHERE (runtime.rolname ~ '^butler_[A-Za-z_][A-Za-z0-9_]*_rw$'
               OR runtime.rolname IN ('connector_writer','dashboard_auth_api'))
          AND (runtime.rolcanlogin
               OR pg_catalog.has_schema_privilege(runtime.oid,'custody_admission','USAGE,CREATE')
               OR EXISTS(SELECT FROM pg_catalog.pg_class relation
                   JOIN pg_catalog.pg_namespace namespace ON namespace.oid=relation.relnamespace
                   WHERE (namespace.nspname='custody_admission' AND relation.relkind IN ('r','p')
                          OR namespace.nspname='public' AND relation.relname='custody_holds')
                     AND (pg_catalog.has_table_privilege(runtime.oid,relation.oid,
                              'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                          OR EXISTS(SELECT FROM pg_catalog.pg_attribute column_info
                              WHERE column_info.attrelid=relation.oid AND column_info.attnum>0
                                AND NOT column_info.attisdropped
                                AND pg_catalog.has_column_privilege(runtime.oid,relation.oid,
                                    column_info.attnum,'SELECT,INSERT,UPDATE,REFERENCES')))))) THEN
        RAISE EXCEPTION 'custody invalid effective runtime access' USING ERRCODE='42501';
    END IF;
    IF NOT EXISTS(SELECT FROM custody_admission.bootstrap_configuration b
        WHERE b.singleton AND b.schema_identity IS NOT NULL
          AND b.schema_identity=custody_admission.schema_identity()) THEN
        RAISE EXCEPTION 'custody installed schema drift' USING ERRCODE='42501';
    END IF;
    count_functions:=0;
    FOR function IN SELECT p.* FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname='custody_admission' OR n.nspname IN ('public','dashboard_auth') AND p.proname LIKE 'custody_%' LOOP
        count_functions:=count_functions+1;
        IF function.proowner<>owner_oid
           OR NOT ('search_path=pg_catalog, pg_temp'=ANY(COALESCE(function.proconfig,ARRAY[]::text[])))
           OR EXISTS(SELECT FROM pg_catalog.aclexplode(COALESCE(function.proacl,
               pg_catalog.acldefault('f',function.proowner))) a
               LEFT JOIN pg_catalog.pg_roles recipient ON recipient.oid=a.grantee
               WHERE a.grantee<>owner_oid AND (
                   a.is_grantable OR a.privilege_type<>'EXECUTE' OR NOT (
                     function.pronamespace='public'::regnamespace::oid AND (
                       recipient.rolname ~ '^butler_[A-Za-z_][A-Za-z0-9_]*_rw$'
                       OR recipient.rolname IN ('connector_writer','dashboard_auth_api'))
                     OR function.pronamespace='dashboard_auth'::regnamespace::oid
                        AND function.proname='custody_prepare' AND recipient.rolname='dashboard_auth_api'
                     OR function.proname IN ('host_enroll','host_revoke','host_revoke_control',
                           'install_interface','prove_interface','rollback_interface','custody_host_prepare','accepted_work')
                        AND recipient.rolname=migration_role)
                   OR recipient.oid IS NULL)) THEN
            RAISE EXCEPTION 'custody invalid installed function' USING ERRCODE='42501';
        END IF;
        IF EXISTS(SELECT FROM pg_catalog.pg_roles recipient
            WHERE (
              function.pronamespace='public'::regnamespace::oid AND (
                recipient.rolname ~ '^butler_[A-Za-z_][A-Za-z0-9_]*_rw$'
                OR recipient.rolname IN ('connector_writer','dashboard_auth_api'))
              OR function.pronamespace='dashboard_auth'::regnamespace::oid
                AND function.proname='custody_prepare' AND recipient.rolname='dashboard_auth_api'
              OR function.proname IN ('host_enroll','host_revoke','host_revoke_control',
                    'install_interface','prove_interface','rollback_interface','custody_host_prepare','accepted_work')
                AND recipient.rolname=migration_role)
              AND NOT EXISTS(SELECT FROM pg_catalog.aclexplode(COALESCE(function.proacl,
                    pg_catalog.acldefault('f',function.proowner))) a
                  WHERE a.grantee=recipient.oid AND a.privilege_type='EXECUTE' AND NOT a.is_grantable)
        ) THEN
            RAISE EXCEPTION 'custody missing installed capability' USING ERRCODE='42501';
        END IF;
    END LOOP;
    IF count_functions<30 OR NOT EXISTS(SELECT FROM pg_catalog.pg_class r
       JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public'
       AND r.relname='custody_holds' AND r.relowner=owner_oid AND r.relrowsecurity AND r.relforcerowsecurity)
       OR NOT EXISTS(SELECT FROM pg_catalog.pg_index WHERE indrelid='public.custody_holds'::regclass
                     AND indisunique AND indisvalid AND indpred IS NOT NULL)
       OR EXISTS(SELECT FROM pg_catalog.pg_policies WHERE schemaname='public' AND tablename='custody_holds'
          AND policyname<>'custody_bootstrap_engine')
       OR NOT EXISTS(SELECT FROM pg_catalog.pg_policies
          WHERE schemaname='public' AND tablename='custody_holds'
            AND policyname='custody_bootstrap_engine' AND permissive='PERMISSIVE'
            AND cmd='ALL' AND roles=ARRAY['public']::name[]
            AND qual=pg_catalog.format('(CURRENT_USER = %L::name)',owner_name)
            AND with_check=pg_catalog.format('(CURRENT_USER = %L::name)',owner_name)) THEN
        RAISE EXCEPTION 'custody invalid installed authority' USING ERRCODE='42501';
    END IF;
    IF pg_catalog.to_regclass('switchboard.message_inbox') IS NOT NULL THEN
        IF EXISTS(SELECT FROM pg_catalog.pg_partition_tree(
            'switchboard.message_inbox'::pg_catalog.regclass) part_node
            CROSS JOIN (VALUES('custody_accepted_birth',5),('custody_accepted_retire',25)) expected(name,kind)
            LEFT JOIN pg_catalog.pg_trigger installed_trigger ON installed_trigger.tgrelid=part_node.relid
              AND installed_trigger.tgname=expected.name
            WHERE installed_trigger.oid IS NULL OR installed_trigger.tgtype<>expected.kind
              OR installed_trigger.tgenabled<>'O'
              OR installed_trigger.tgfoid<>'custody_admission.accepted_birth()'::pg_catalog.regprocedure
              OR installed_trigger.tgnargs<>0 OR installed_trigger.tgqual IS NOT NULL
              OR installed_trigger.tgisinternal) THEN
            RAISE EXCEPTION 'custody invalid accepted birth trigger' USING ERRCODE='42501';
        END IF;
    END IF;
    -- Return only this feature's fixed source manifest to trusted startup.
    -- Its checked-in counterpart compares exact bodies, argument/return ABI,
    -- language, volatility, defaults and configuration before enrollment.
    -- This is not a public catalog discovery or caller-selected shadow query.
    SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object(
        'signature',n.nspname||'.'||p.proname||'('||
          COALESCE((SELECT pg_catalog.string_agg(pg_catalog.format_type(t,NULL),',' ORDER BY ordinal)
            FROM pg_catalog.unnest(p.proargtypes) WITH ORDINALITY a(t,ordinal)),'')||')',
        'body_sha256',pg_catalog.encode(public.digest(pg_catalog.convert_to(p.prosrc,'UTF8'),'sha256'),'hex'),
        'argument_names',COALESCE(pg_catalog.to_jsonb(p.proargnames),'[]'::jsonb),
        'return_type',pg_catalog.format_type(p.prorettype,NULL),
        'language',l.lanname,'security_definer',p.prosecdef,'volatility',p.provolatile::text,
        'strict',p.proisstrict,'leakproof',p.proleakproof,'parallel',p.proparallel::text,
        'returns_set',p.proretset,'argument_modes',pg_catalog.to_jsonb(p.proargmodes),
        'defaults',CASE WHEN p.proargdefaults IS NULL THEN NULL
                        ELSE pg_catalog.pg_get_expr(p.proargdefaults,0) END,
        'configuration',pg_catalog.to_jsonb(p.proconfig)) ORDER BY n.nspname,p.proname,p.proargtypes)
      INTO function_manifest
      FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
      JOIN pg_catalog.pg_language l ON l.oid=p.prolang
      WHERE n.nspname='custody_admission'
         OR n.nspname IN ('public','dashboard_auth') AND p.proname LIKE 'custody_%';
    RETURN pg_catalog.jsonb_build_object('version',1,'bootstrap_owner_oid',owner_oid,
        'core_revision','core_260','functions',count_functions,'function_manifest',function_manifest);
END;
$custody_prove$;

CREATE OR REPLACE FUNCTION custody_admission.rollback_interface() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_rollback$
BEGIN
    PERFORM custody_admission.host_only();
    PERFORM custody_admission.current_control();
    IF EXISTS(SELECT FROM public.custody_holds) OR EXISTS(SELECT FROM custody_admission.sources)
       OR EXISTS(SELECT FROM custody_admission.commands) OR EXISTS(SELECT FROM custody_admission.processes) THEN
        RAISE EXCEPTION 'custody populated rollback refused' USING ERRCODE='42501';
    END IF;
    -- Retain installation/evidence rather than silently drop trust enforcement.
    UPDATE custody_admission.control SET admission_state='unavailable',control_epoch=control_epoch+1 WHERE singleton;
END;
$custody_rollback$;

-- Install before defining dashboard wrappers only when its owning schema exists.
-- Fresh bootstrap precedes core_240, so core_260 installer creates them later.
CREATE OR REPLACE FUNCTION custody_admission.install_dashboard_interface() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $custody_dashboard_install$
BEGIN
    IF pg_catalog.to_regnamespace('dashboard_auth') IS NULL THEN RETURN; END IF;
    EXECUTE $dashboard_wrapper$
        CREATE OR REPLACE FUNCTION dashboard_auth.custody_prepare(operation text,proof jsonb,selection jsonb)
        RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
        DECLARE instance record; complete_proof jsonb; result jsonb;
        BEGIN
            -- Epochs come from the locked server singleton, never HTTP/model
            -- claims. Raw cookie/CSRF never enter this interface or any result.
            PERFORM custody_admission.closed(proof,ARRAY[
                'session_digest','csrf_digest','origin','rp_id','key_generation'
            ]);
            SELECT * INTO instance FROM dashboard_auth.instance WHERE singleton FOR UPDATE;
            IF NOT FOUND THEN RAISE EXCEPTION 'custody unavailable' USING ERRCODE='42501'; END IF;
            complete_proof:=proof||pg_catalog.jsonb_build_object(
                'credential_epoch',instance.credential_epoch,'session_epoch',instance.session_epoch);
            result:=custody_admission.prepare('browser',operation,complete_proof,selection);
            -- Safe source-producer inputs are the immutable selection/version
            -- and original private command expiry, not an authorization verdict.
            RETURN result||pg_catalog.jsonb_build_object(
                'selection_digest',custody_admission.binding_digest(selection));
        END;
        $body$;
        CREATE OR REPLACE FUNCTION dashboard_auth.custody_host_prepare(operation text,exact_selection jsonb)
        RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
            SELECT custody_admission.prepare('host',operation,'{}'::jsonb,exact_selection)
        $body$;
        CREATE OR REPLACE FUNCTION dashboard_auth.custody_check_locked(command_id uuid,expected_operation_digest text)
        RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $body$
        DECLARE result jsonb;
        BEGIN
            result:=custody_admission.command_current(command_id);
            IF result->>'selection_digest'<>expected_operation_digest THEN
                RAISE EXCEPTION 'custody refused' USING ERRCODE='42501';
            END IF;
            RETURN result;
        END;
        $body$;
    $dashboard_wrapper$;
END;
$custody_dashboard_install$;
SELECT custody_admission.finalize_interface();
RESET ROLE;
