"""external_accounts: create google_accounts, steam_accounts, steam_cursors, steam_play_history

Revision ID: core_008
Revises: core_007
Create Date: 2026-03-26 00:00:00.000000

Collapsed from: core_026_google_accounts, core_042_steam_accounts

PUBLIC schema:
  - public.google_accounts — multi-account Google identity support.
    Indexes: ix_google_accounts_email (UNIQUE), ix_google_accounts_primary_singleton (partial).
    Data migration: detects existing google_oauth_refresh on owner entity and promotes it.

  - public.steam_accounts — one row per connected Steam identity.
    Indexes: ix_steam_accounts_steam_id (UNIQUE), ix_steam_accounts_primary_singleton (partial).

CONNECTORS schema:
  - connectors.steam_cursors — per-account, per-data-type cursor persistence for polling loops.
  - connectors.steam_play_history — daily playtime aggregates per account/app.

All DDL is guarded with IF (NOT) EXISTS for idempotency.
Downgrade reverses DDL only; data migrations are not reversed.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_008"
down_revision = "core_007"
branch_labels = None
depends_on = None

# All butler roles that need access to account tables.
_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"
_CONNECTOR_ROLE = "connector_writer"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    """Quote a string as a SQL literal (single-quote escaping)."""
    return "'" + value.replace("'", "''") + "'"


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _revoke_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """REVOKE privilege ON table FROM role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'REVOKE {privilege} ON TABLE {table_fqn} FROM {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _execute_best_effort(statement: str, *, role_name: str | None = None) -> None:
    """Execute SQL while tolerating privilege/role availability differences."""
    condition = "TRUE"
    if role_name is not None:
        condition = f"EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role_name)})"

    op.execute(
        f"""
        DO $$
        BEGIN
            IF {condition} THEN
                EXECUTE {_quote_literal(statement)};
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # =========================================================================
    # GOOGLE ACCOUNTS (public schema)
    # =========================================================================

    # -------------------------------------------------------------------------
    # 1. Create public.google_accounts table.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.google_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL
                REFERENCES public.entities(id) ON DELETE CASCADE,
            email VARCHAR,
            display_name VARCHAR,
            is_primary BOOLEAN NOT NULL DEFAULT false,
            granted_scopes TEXT[] NOT NULL DEFAULT '{}',
            status VARCHAR NOT NULL DEFAULT 'active',
            connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_token_refresh_at TIMESTAMPTZ,
            metadata JSONB DEFAULT '{}'::jsonb,
            CONSTRAINT chk_google_accounts_status
                CHECK (status IN ('active', 'revoked', 'expired'))
        )
    """)

    # -------------------------------------------------------------------------
    # 2. Create indexes on public.google_accounts.
    # -------------------------------------------------------------------------

    # Standard unique index on email (for lookup by email).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_google_accounts_email
            ON public.google_accounts (email)
    """)

    # Partial unique index: at most one primary account.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class idx
                JOIN pg_namespace n ON n.oid = idx.relnamespace
                WHERE idx.relname = 'ix_google_accounts_primary_singleton'
                  AND n.nspname = 'public'
            ) THEN
                CREATE UNIQUE INDEX ix_google_accounts_primary_singleton
                    ON public.google_accounts ((true))
                    WHERE is_primary = true;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Data migration: promote existing single-account credentials.
    #
    #    If the owner entity has a google_oauth_refresh row in entity_info:
    #      a. Resolve the owner email from entity_info (type='email', if any).
    #      b. Create a companion entity (entity_type='other', roles=['google_account']).
    #      c. Insert a google_accounts row (is_primary=true) referencing the
    #         companion entity.
    #      d. Re-point the entity_info.entity_id (google_oauth_refresh row) from
    #         the owner entity to the companion entity.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        DECLARE
            v_owner_entity_id      UUID;
            v_refresh_token_value  TEXT;
            v_email                TEXT;
            v_companion_entity_id  UUID;
            v_canonical_name       TEXT;
        BEGIN
            -- Guard: skip if required tables don't exist.
            IF to_regclass('public.entities') IS NULL
               OR to_regclass('public.entity_info') IS NULL
               OR to_regclass('public.google_accounts') IS NULL
            THEN
                RETURN;
            END IF;

            -- Resolve owner entity.
            SELECT e.id INTO v_owner_entity_id
            FROM public.entities e
            WHERE 'owner' = ANY(e.roles)
            LIMIT 1;

            IF v_owner_entity_id IS NULL THEN
                RETURN;
            END IF;

            -- Check for existing google_oauth_refresh on the owner entity.
            SELECT ei.value INTO v_refresh_token_value
            FROM public.entity_info ei
            WHERE ei.entity_id = v_owner_entity_id
              AND ei.type = 'google_oauth_refresh'
            LIMIT 1;

            -- No existing credential -> nothing to migrate.
            IF v_refresh_token_value IS NULL THEN
                RETURN;
            END IF;

            -- Skip if a google_accounts row already exists (idempotent).
            IF EXISTS (
                SELECT 1 FROM public.google_accounts WHERE is_primary = true
            ) THEN
                RETURN;
            END IF;

            -- Resolve email from owner entity_info, if available.
            SELECT ei.value INTO v_email
            FROM public.entity_info ei
            WHERE ei.entity_id = v_owner_entity_id
              AND ei.type = 'email'
            LIMIT 1;

            -- Build canonical name for companion entity.
            IF v_email IS NOT NULL AND v_email != '' THEN
                v_canonical_name := 'google-account:' || v_email;
            ELSE
                v_canonical_name := 'google-account:primary';
            END IF;

            -- Create companion entity (roles=['google_account']).
            INSERT INTO public.entities (
                tenant_id, canonical_name, entity_type, roles
            )
            VALUES ('shared', v_canonical_name, 'other', ARRAY['google_account'])
            ON CONFLICT (tenant_id, canonical_name, entity_type) DO NOTHING
            RETURNING id INTO v_companion_entity_id;

            -- Fetch id if it already existed.
            IF v_companion_entity_id IS NULL THEN
                SELECT id INTO v_companion_entity_id
                FROM public.entities
                WHERE tenant_id = 'shared'
                  AND canonical_name = v_canonical_name
                  AND entity_type = 'other';
            END IF;

            IF v_companion_entity_id IS NULL THEN
                RETURN;
            END IF;

            -- Insert google_accounts row for the promoted credential.
            INSERT INTO public.google_accounts (
                entity_id, email, is_primary, granted_scopes, status
            )
            VALUES (
                v_companion_entity_id,
                v_email,
                true,
                '{}',
                'active'
            )
            ON CONFLICT DO NOTHING;

            -- Re-point entity_info google_oauth_refresh to the companion entity.
            -- Only move if the companion entity doesn't already have this row.
            IF NOT EXISTS (
                SELECT 1 FROM public.entity_info
                WHERE entity_id = v_companion_entity_id
                  AND type = 'google_oauth_refresh'
            ) THEN
                UPDATE public.entity_info
                SET entity_id = v_companion_entity_id
                WHERE entity_id = v_owner_entity_id
                  AND type = 'google_oauth_refresh';
            END IF;
        END
        $$;
    """)

    # =========================================================================
    # STEAM ACCOUNTS (public schema)
    # =========================================================================

    # -------------------------------------------------------------------------
    # 4. Create public.steam_accounts table.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.steam_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL
                REFERENCES public.entities(id) ON DELETE CASCADE,
            steam_id BIGINT UNIQUE NOT NULL,
            display_name VARCHAR,
            profile_url VARCHAR,
            avatar_url VARCHAR,
            is_primary BOOLEAN NOT NULL DEFAULT false,
            status VARCHAR NOT NULL DEFAULT 'active',
            connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_poll_at TIMESTAMPTZ,
            metadata JSONB DEFAULT '{}'::jsonb,
            CONSTRAINT chk_steam_accounts_status
                CHECK (status IN ('active', 'suspended', 'revoked'))
        )
    """)

    # -------------------------------------------------------------------------
    # 5. Create indexes on public.steam_accounts.
    # -------------------------------------------------------------------------

    # Unique index on steam_id (64-bit Steam identity lookup).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_steam_accounts_steam_id
            ON public.steam_accounts (steam_id)
    """)

    # Partial unique index: at most one primary account.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class idx
                JOIN pg_namespace n ON n.oid = idx.relnamespace
                WHERE idx.relname = 'ix_steam_accounts_primary_singleton'
                  AND n.nspname = 'public'
            ) THEN
                CREATE UNIQUE INDEX ix_steam_accounts_primary_singleton
                    ON public.steam_accounts ((true))
                    WHERE is_primary = true;
            END IF;
        END
        $$;
    """)

    # =========================================================================
    # STEAM CONNECTOR TABLES (connectors schema)
    # =========================================================================

    # -------------------------------------------------------------------------
    # 6. Ensure connectors schema exists.
    # -------------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS connectors")

    # -------------------------------------------------------------------------
    # 7. Create connectors.steam_cursors table.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS connectors.steam_cursors (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            endpoint_identity VARCHAR NOT NULL,
            data_type VARCHAR NOT NULL,
            last_poll_at TIMESTAMPTZ,
            state_hash VARCHAR,
            state_snapshot JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_steam_cursors_identity_type UNIQUE (endpoint_identity, data_type)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_steam_cursors_endpoint_identity
            ON connectors.steam_cursors (endpoint_identity)
    """)

    # -------------------------------------------------------------------------
    # 8. Create connectors.steam_play_history table.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS connectors.steam_play_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            steam_id BIGINT NOT NULL,
            app_id INTEGER NOT NULL,
            play_date DATE NOT NULL,
            playtime_minutes INTEGER NOT NULL DEFAULT 0,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_steam_play_history_account_app_date
                UNIQUE (steam_id, app_id, play_date)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_steam_play_history_steam_id
            ON connectors.steam_play_history (steam_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_steam_play_history_app_id
            ON connectors.steam_play_history (app_id)
    """)

    # =========================================================================
    # GRANTS
    # =========================================================================

    # -------------------------------------------------------------------------
    # 9. Grant access to butler roles for public account tables.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("public.google_accounts", _TABLE_PRIVILEGES, role)
        _grant_if_table_exists("public.steam_accounts", _TABLE_PRIVILEGES, role)

    # -------------------------------------------------------------------------
    # 10. Grant access to connector_writer for connectors tables.
    # -------------------------------------------------------------------------
    _execute_best_effort(
        f"GRANT USAGE, CREATE ON SCHEMA connectors TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE connectors.steam_cursors"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE connectors.steam_play_history"
        f" TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )
    _execute_best_effort(
        f"GRANT SELECT ON TABLE public.steam_accounts TO {_quote_ident(_CONNECTOR_ROLE)}",
        role_name=_CONNECTOR_ROLE,
    )


def downgrade() -> None:
    # =========================================================================
    # REVOKE & DROP STEAM CONNECTOR TABLES
    # =========================================================================
    op.execute("DROP TABLE IF EXISTS connectors.steam_play_history CASCADE")
    op.execute("DROP INDEX IF EXISTS connectors.ix_steam_cursors_endpoint_identity")
    op.execute("DROP TABLE IF EXISTS connectors.steam_cursors CASCADE")

    # =========================================================================
    # REVOKE & DROP STEAM ACCOUNTS
    # =========================================================================
    for role in _ALL_BUTLER_ROLES:
        _revoke_if_table_exists("public.steam_accounts", _TABLE_PRIVILEGES, role)

    op.execute("DROP INDEX IF EXISTS public.ix_steam_accounts_primary_singleton")
    op.execute("DROP INDEX IF EXISTS public.ix_steam_accounts_steam_id")
    op.execute("DROP TABLE IF EXISTS public.steam_accounts CASCADE")

    # =========================================================================
    # REVOKE & DROP GOOGLE ACCOUNTS
    # =========================================================================
    for role in _ALL_BUTLER_ROLES:
        _revoke_if_table_exists("public.google_accounts", _TABLE_PRIVILEGES, role)

    op.execute("DROP INDEX IF EXISTS public.ix_google_accounts_primary_singleton")
    op.execute("DROP INDEX IF EXISTS public.ix_google_accounts_email")
    op.execute("DROP TABLE IF EXISTS public.google_accounts CASCADE")
