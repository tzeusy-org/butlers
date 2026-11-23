"""connector_oauth_scope_surface: additive OAuth scope columns on connector_registry.

Revision ID: core_114
Revises: core_113
Create Date: 2026-06-02 00:00:00.000000

Adds four nullable columns to ``switchboard.connector_registry`` (and/or
``public.connector_registry`` depending on the deployment schema) to support
the connector-oauth-scope-surface capability.

Spec: openspec/changes/add-connector-oauth-scope-surface/specs/connector-oauth-scope-surface/spec.md
      §Requirement: Observed-scope storage on connector_registry

Columns added
-------------
observed_scopes          TEXT[]          NULL  — last-known granted scopes; NULL = never probed
observed_scopes_fetched_at TIMESTAMPTZ   NULL  — wall-clock timestamp of last successful observation
required_scopes_version  SMALLINT        NULL  — manifest version at most recent reauth completion
auth_status              VARCHAR(32)     NULL  — precomputed rollup:
                                                 ok | degraded | expired | rotation-needed |
                                                 unsupported | unconfigured

Design decisions
----------------
- All four columns are nullable with no DEFAULT. Existing rows (pre-migration)
  have NULL in all four columns. NULL ``observed_scopes`` means "never probed"
  and the API returns auth_status = 'unconfigured' for OAuth connectors and
  'unsupported' for non-OAuth connectors.
- No data backfill is required. The daemon will populate these columns on the
  next successful token refresh for each OAuth connector.
- The migration runs against the ``switchboard`` schema (where connector_registry
  lives) but guards against the table being absent on fresh installs where
  the switchboard butler has not yet run its own schema setup.
- ``auth_status`` column is VARCHAR(32) to match the six-value enum but remain
  pure SQL without enum type creation (avoids enum migration complexity on
  pg upgrades).

Grants
------
No additional grants are needed — all roles that can SELECT/UPDATE
``connector_registry`` automatically gain access to the new columns.
"""

from __future__ import annotations

from alembic import op


def _table_exists(schema: str, table: str) -> str:
    """Return a PL/pgSQL boolean expression that is TRUE when the table exists."""
    return (
        f"EXISTS (SELECT 1 FROM pg_tables "
        f"WHERE schemaname = '{schema}' AND tablename = '{table}')"
    )


def _column_exists(schema: str, table: str, column: str) -> str:
    """Return a PL/pgSQL boolean expression that is TRUE when the column exists."""
    return (
        f"EXISTS (SELECT 1 FROM information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
        f"AND column_name = '{column}')"
    )


revision = "core_114"
down_revision = "core_113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # connector_registry lives in the switchboard schema.  Guard against
    # fresh installs where the switchboard butler has not yet run its schema
    # setup (e.g. test environments that only migrate core_* migrations).
    for schema in ("switchboard", "public"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF {_table_exists(schema, "connector_registry")} THEN
                    IF NOT {_column_exists(schema, "connector_registry", "observed_scopes")} THEN
                        EXECUTE 'ALTER TABLE {schema}.connector_registry
                                 ADD COLUMN observed_scopes TEXT[] NULL';
                    END IF;
                    IF NOT {_column_exists(schema, "connector_registry", "observed_scopes_fetched_at")} THEN
                        EXECUTE 'ALTER TABLE {schema}.connector_registry
                                 ADD COLUMN observed_scopes_fetched_at TIMESTAMPTZ NULL';
                    END IF;
                    IF NOT {_column_exists(schema, "connector_registry", "required_scopes_version")} THEN
                        EXECUTE 'ALTER TABLE {schema}.connector_registry
                                 ADD COLUMN required_scopes_version SMALLINT NULL';
                    END IF;
                    IF NOT {_column_exists(schema, "connector_registry", "auth_status")} THEN
                        EXECUTE 'ALTER TABLE {schema}.connector_registry
                                 ADD COLUMN auth_status VARCHAR(32) NULL';
                    END IF;
                END IF;
            END
            $$;
            """
        )


def downgrade() -> None:
    for schema in ("switchboard", "public"):
        op.execute(
            f"""
            DO $$
            BEGIN
                IF {_table_exists(schema, "connector_registry")} THEN
                    EXECUTE 'ALTER TABLE {schema}.connector_registry
                             DROP COLUMN IF EXISTS observed_scopes,
                             DROP COLUMN IF EXISTS observed_scopes_fetched_at,
                             DROP COLUMN IF EXISTS required_scopes_version,
                             DROP COLUMN IF EXISTS auth_status';
                END IF;
            END
            $$;
            """
        )
