"""entity_info: create shared.entity_info table for entity-level metadata

Revision ID: core_017
Revises: core_016
Create Date: 2026-03-06 00:00:00.000000

Creates shared.entity_info to store entity-level metadata and credentials
(e.g. Telegram API hash, HA token, Google OAuth refresh) that were previously
conflated with contact-channel identifiers on shared.contact_info.

Schema:
  id UUID PK DEFAULT gen_random_uuid()
  entity_id UUID NOT NULL FK shared.entities(id) ON DELETE CASCADE
  type VARCHAR NOT NULL
  value TEXT NOT NULL
  label VARCHAR
  is_primary BOOLEAN DEFAULT false
  secured BOOLEAN NOT NULL DEFAULT false
  created_at TIMESTAMPTZ DEFAULT now()
  UNIQUE (entity_id, type)

Design notes:
  - Mirrors shared.contact_info structure but keyed to entities instead of contacts.
  - The UNIQUE(entity_id, type) constraint means one value per type per entity.
  - secured=true marks credential entries whose values should be masked in API
    responses (same pattern as contact_info).
  - ON DELETE CASCADE: if an entity is removed, its info rows are cleaned up.
  - All DDL is guarded with IF (NOT) EXISTS for idempotency.
  - All butler roles receive SELECT, INSERT, UPDATE, DELETE on the new table.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_017"
down_revision = "core_016"
branch_labels = None
depends_on = None

# All butler roles that need access to shared.entity_info.
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

_ENTITY_INFO_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
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


def _grant_schema_usage_if_exists(schema: str, role: str) -> None:
    """GRANT USAGE ON SCHEMA only when schema and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = '{schema}'
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
            THEN
                EXECUTE 'GRANT USAGE ON SCHEMA {_quote_ident(schema)} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
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
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
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


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Ensure the shared schema exists (idempotent guard).
    # -------------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS shared")

    # -------------------------------------------------------------------------
    # 2. Create shared.entity_info table.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.entity_info (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL
                REFERENCES shared.entities(id) ON DELETE CASCADE,
            type VARCHAR NOT NULL,
            value TEXT NOT NULL,
            label VARCHAR,
            is_primary BOOLEAN DEFAULT false,
            secured BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT uq_shared_entity_info_entity_type
                UNIQUE (entity_id, type)
        )
    """)

    # -------------------------------------------------------------------------
    # 3. Create indexes on shared.entity_info.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entity_info_entity_id
            ON shared.entity_info (entity_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entity_info_type
            ON shared.entity_info (type)
    """)

    # -------------------------------------------------------------------------
    # 4. Grant access to all butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("shared.entity_info", _ENTITY_INFO_TABLE_PRIVILEGES, role)
        _grant_schema_usage_if_exists("shared", role)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # 4. Revoke privileges from butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _revoke_if_table_exists("shared.entity_info", _ENTITY_INFO_TABLE_PRIVILEGES, role)

    # -------------------------------------------------------------------------
    # 3 + 2. Drop indexes and table.
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.idx_shared_entity_info_type")
    op.execute("DROP INDEX IF EXISTS shared.idx_shared_entity_info_entity_id")
    op.execute("DROP TABLE IF EXISTS shared.entity_info CASCADE")
