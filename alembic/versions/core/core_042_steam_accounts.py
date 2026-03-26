"""steam_accounts: create public.steam_accounts, connectors.steam_cursors, steam_play_history

Revision ID: core_042
Revises: core_041
Create Date: 2026-03-26 00:00:00.000000

Creates the database foundation for the Steam integration:

  1. public.steam_accounts — one row per connected Steam identity.
     Mirrors the public.google_accounts pattern.  Indexes:
       - ix_steam_accounts_steam_id (UNIQUE on steam_id)
       - ix_steam_accounts_primary_singleton (partial UNIQUE WHERE is_primary = true)

  2. connectors.steam_cursors — per-account, per-data-type cursor persistence
     for the Steam connector polling loops.

  3. connectors.steam_play_history — daily playtime aggregates per account/app.

All DDL is guarded with IF (NOT) EXISTS for idempotency.
Downgrade reverses DDL only; data is not reversed.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_042"
down_revision = "core_041"
branch_labels = None
depends_on = None

# All butler roles that may need access to public.steam_accounts.
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
    # -------------------------------------------------------------------------
    # 1. Create public.steam_accounts table.
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
    # 2. Create indexes on public.steam_accounts.
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

    # -------------------------------------------------------------------------
    # 3. Ensure connectors schema exists.
    # -------------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS connectors")

    # -------------------------------------------------------------------------
    # 4. Create connectors.steam_cursors table.
    #    Per-account, per-data-type cursor persistence for polling loops.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS connectors.steam_cursors (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            steam_id BIGINT NOT NULL,
            data_type VARCHAR NOT NULL,
            cursor_value TEXT,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_steam_cursors_account_type UNIQUE (steam_id, data_type)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_steam_cursors_steam_id
            ON connectors.steam_cursors (steam_id)
    """)

    # -------------------------------------------------------------------------
    # 5. Create connectors.steam_play_history table.
    #    Daily playtime aggregates per account/app.
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

    # -------------------------------------------------------------------------
    # 6. Grant access to butler roles for public.steam_accounts.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("public.steam_accounts", _TABLE_PRIVILEGES, role)

    # -------------------------------------------------------------------------
    # 7. Grant access to connector_writer for connectors tables.
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
    # -------------------------------------------------------------------------
    # 7. Revoke connector_writer grants (best-effort, no-op if role absent).
    # -------------------------------------------------------------------------
    # No explicit REVOKE needed — tables are dropped below.

    # -------------------------------------------------------------------------
    # 6. Revoke butler role privileges on public.steam_accounts.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _revoke_if_table_exists("public.steam_accounts", _TABLE_PRIVILEGES, role)

    # -------------------------------------------------------------------------
    # 5 + 4. Drop connectors tables.
    # -------------------------------------------------------------------------
    op.execute("DROP TABLE IF EXISTS connectors.steam_play_history CASCADE")
    op.execute("DROP TABLE IF EXISTS connectors.steam_cursors CASCADE")

    # -------------------------------------------------------------------------
    # 2 + 1. Drop indexes and public.steam_accounts table.
    #   Note: data is NOT reversed.
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS public.ix_steam_accounts_primary_singleton")
    op.execute("DROP INDEX IF EXISTS public.ix_steam_accounts_steam_id")
    op.execute("DROP TABLE IF EXISTS public.steam_accounts CASCADE")
