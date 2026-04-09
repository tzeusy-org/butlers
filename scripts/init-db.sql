-- init-db.sql: PostgreSQL provisioning script for Butlers
--
-- Run this script as a superuser BEFORE running Alembic migrations on a
-- fresh database.  It installs required extensions and grants butler role
-- membership to the runtime connecting user so that SET ROLE enforcement
-- works at runtime.
--
-- Usage (as superuser, targeting default 'butlers' app user):
--   psql -h <host> -U postgres -d butlers -f scripts/init-db.sql
--
-- Targeting a different connecting user:
--   PGOPTIONS="-c butlers.connecting_user=myappuser" \
--     psql -h <host> -U postgres -d butlers -f scripts/init-db.sql
--
-- Why this file exists:
--   The core_001 migration creates butler runtime roles (butler_{schema}_rw,
--   connector_writer) and grants schema-level ACLs to each role.  However,
--   granting role *membership* (GRANT role TO user) for the runtime connecting
--   user requires superuser or an existing member of those roles.  The
--   migration user typically lacks this, so the grants must happen here as a
--   superuser, either before or after migrations run.
--
--   Role membership is required so that SET ROLE works at runtime:
--     SET ROLE butler_health_rw;   -- fails without membership
--
--   The core_065 migration also does GRANT role TO CURRENT_USER for the
--   migration-time user.  This script covers the runtime user, which may
--   differ.
--
-- Idempotency: safe to re-run.  Already-applied grants are silently skipped.
-- Ordering:    run AFTER the database and connecting user exist.  If run
--              before migrations (roles don't exist yet), re-run after
--              migrations complete so all role grants are applied.

-- ── Extensions ────────────────────────────────────────────────────────────────
-- Superuser-only; the migration user typically lacks CREATE EXTENSION.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";    -- pgvector: vector similarity search
CREATE EXTENSION IF NOT EXISTS "pg_trgm";   -- trigram indexes for fuzzy text search

-- ── Role membership ───────────────────────────────────────────────────────────
-- Grant each butler runtime role and connector_writer to the app user.
-- Default grantee: 'butlers' (matches POSTGRES_USER default in .env.example).
-- Override via GUC: PGOPTIONS="-c butlers.connecting_user=<name>" psql ...

DO $$
DECLARE
    _roles TEXT[] := ARRAY[
        'butler_education_rw',
        'butler_finance_rw',
        'butler_general_rw',
        'butler_health_rw',
        'butler_home_rw',
        'butler_lifestyle_rw',
        'butler_messenger_rw',
        'butler_relationship_rw',
        'butler_switchboard_rw',
        'butler_travel_rw',
        'connector_writer'
    ];
    -- Default to 'butlers'; override with PGOPTIONS="-c butlers.connecting_user=<name>"
    _grantee TEXT;
    _role TEXT;
BEGIN
    BEGIN
        _grantee := current_setting('butlers.connecting_user');
    EXCEPTION
        WHEN undefined_object THEN
            _grantee := 'butlers';
    END;

    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _grantee) THEN
        RAISE WARNING 'Connecting user "%" does not exist — skipping role grants. '
            'Create the user first or set butlers.connecting_user to an existing role.', _grantee;
        RETURN;
    END IF;

    FOREACH _role IN ARRAY _roles LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = _role) THEN
            RAISE NOTICE 'Role "%" does not exist yet; re-run after migrations complete', _role;
            CONTINUE;
        END IF;

        -- Skip if already a member
        IF pg_has_role(_grantee, _role, 'MEMBER') THEN
            RAISE NOTICE 'User "%" is already a member of "%" — skipping', _grantee, _role;
            CONTINUE;
        END IF;

        BEGIN
            EXECUTE format('GRANT %I TO %I', _role, _grantee);
            RAISE NOTICE 'Granted "%" to "%"', _role, _grantee;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE WARNING 'Insufficient privilege to GRANT "%" TO "%"; run as superuser', _role, _grantee;
        END;
    END LOOP;
END
$$;
