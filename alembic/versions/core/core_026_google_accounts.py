"""google_accounts: create shared.google_accounts table and migrate single-account credentials

Revision ID: core_026
Revises: core_025
Create Date: 2026-03-11 00:00:00.000000

Creates the shared.google_accounts table for multi-account Google identity support.

Changes applied in upgrade():

  1. CREATE TABLE shared.google_accounts with all columns and constraints.
  2. Create UNIQUE index on email and partial unique index for primary singleton.
  3. Data migration: detect existing google_oauth_refresh on owner entity →
     create companion entity (roles=['google_account']) → create google_accounts
     row (is_primary=true) → re-point entity_info.entity_id to companion entity.
  4. Grant SELECT, INSERT, UPDATE, DELETE on shared.google_accounts to all butler roles.

downgrade() reverses DDL (data migration is not reversed).

Design notes:
  - Each Google account has a companion entity in shared.entities with
    entity_type='other' and roles=['google_account'].  The companion entity
    anchors the refresh token in shared.entity_info.
  - The partial unique index ix_google_accounts_primary_singleton enforces
    at most one primary account at the DB level.
  - Data migration is best-effort: if the existing owner entity has a
    google_oauth_refresh row in entity_info, a companion entity is created
    and the entity_info row is re-pointed to it.
  - All DDL is guarded with IF (NOT) EXISTS for idempotency.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_026"
down_revision = "core_025"
branch_labels = None
depends_on = None

# All butler roles that need access to shared.google_accounts.
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
    # 2. Create shared.google_accounts table.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.google_accounts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL
                REFERENCES shared.entities(id) ON DELETE CASCADE,
            email VARCHAR UNIQUE,
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
    # 3. Create indexes on shared.google_accounts.
    # -------------------------------------------------------------------------

    # Standard unique index on email (for lookup by email).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ix_google_accounts_email
            ON shared.google_accounts (email)
    """)

    # Partial unique index: at most one primary account.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class idx
                JOIN pg_namespace n ON n.oid = idx.relnamespace
                WHERE idx.relname = 'ix_google_accounts_primary_singleton'
                  AND n.nspname = 'shared'
            ) THEN
                CREATE UNIQUE INDEX ix_google_accounts_primary_singleton
                    ON shared.google_accounts ((true))
                    WHERE is_primary = true;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 4. Data migration: promote existing single-account credentials.
    #
    #    If the owner entity has a google_oauth_refresh row in entity_info:
    #      a. Resolve the owner email from entity_info (type='email', if any).
    #         Fall back to a placeholder canonical name if no email found.
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
            IF to_regclass('shared.entities') IS NULL
               OR to_regclass('shared.entity_info') IS NULL
               OR to_regclass('shared.google_accounts') IS NULL
            THEN
                RETURN;
            END IF;

            -- Resolve owner entity.
            SELECT e.id INTO v_owner_entity_id
            FROM shared.entities e
            WHERE 'owner' = ANY(e.roles)
            LIMIT 1;

            IF v_owner_entity_id IS NULL THEN
                RETURN;
            END IF;

            -- Check for existing google_oauth_refresh on the owner entity.
            SELECT ei.value INTO v_refresh_token_value
            FROM shared.entity_info ei
            WHERE ei.entity_id = v_owner_entity_id
              AND ei.type = 'google_oauth_refresh'
            LIMIT 1;

            -- No existing credential → nothing to migrate.
            IF v_refresh_token_value IS NULL THEN
                RETURN;
            END IF;

            -- Skip if a google_accounts row already exists (idempotent).
            IF EXISTS (
                SELECT 1 FROM shared.google_accounts WHERE is_primary = true
            ) THEN
                RETURN;
            END IF;

            -- Resolve email from owner entity_info, if available.
            SELECT ei.value INTO v_email
            FROM shared.entity_info ei
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
            INSERT INTO shared.entities (
                tenant_id, canonical_name, entity_type, roles
            )
            VALUES ('shared', v_canonical_name, 'other', ARRAY['google_account'])
            ON CONFLICT (tenant_id, canonical_name, entity_type) DO NOTHING
            RETURNING id INTO v_companion_entity_id;

            -- Fetch id if it already existed.
            IF v_companion_entity_id IS NULL THEN
                SELECT id INTO v_companion_entity_id
                FROM shared.entities
                WHERE tenant_id = 'shared'
                  AND canonical_name = v_canonical_name
                  AND entity_type = 'other';
            END IF;

            IF v_companion_entity_id IS NULL THEN
                RETURN;
            END IF;

            -- Insert google_accounts row for the promoted credential.
            INSERT INTO shared.google_accounts (
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
                SELECT 1 FROM shared.entity_info
                WHERE entity_id = v_companion_entity_id
                  AND type = 'google_oauth_refresh'
            ) THEN
                UPDATE shared.entity_info
                SET entity_id = v_companion_entity_id
                WHERE entity_id = v_owner_entity_id
                  AND type = 'google_oauth_refresh';
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 5. Grant access to all butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("shared.google_accounts", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # 5. Revoke privileges from butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _revoke_if_table_exists("shared.google_accounts", _TABLE_PRIVILEGES, role)

    # -------------------------------------------------------------------------
    # 3 + 2. Drop indexes and table.
    #   Note: data migration is NOT reversed (companion entities and re-pointed
    #   entity_info rows are kept to avoid credential loss).
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.ix_google_accounts_primary_singleton")
    op.execute("DROP INDEX IF EXISTS shared.ix_google_accounts_email")
    op.execute("DROP TABLE IF EXISTS shared.google_accounts CASCADE")
