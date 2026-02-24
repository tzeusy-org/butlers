"""contact_info_shared

Revision ID: contacts_002
Revises: contacts_001
Create Date: 2026-02-24 00:00:00.000000

Moves the contact_info table to the shared schema so that all butlers running
the contacts module can read and write contact email/phone/url/username records
without depending on the relationship butler's schema.

Background:
  The contacts module backfill pipeline (backfill.py) queries and writes
  contact_info rows for email/phone resolution and contact enrichment. Previously,
  contact_info lived only in the relationship butler's per-schema migration
  (rel_002c). Any butler enabling the contacts module (general, health) would
  hit UndefinedTableError on every backfill apply and sync poll cycle.

Design:
  - Creates shared.contact_info if it does not already exist.
  - Migrates any existing per-schema contact_info rows to shared.contact_info.
  - Grants INSERT, UPDATE, DELETE on shared.contact_info to the butler runtime
    roles that run the contacts module (general, health, relationship).
  - The relationship butler migration rel_002c retains its CREATE TABLE IF NOT
    EXISTS statement; on fresh installs, that table will be empty and skipped
    by this migration's data copy step.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "contacts_002"
down_revision = "contacts_001"
branch_labels = None
depends_on = None

# Butler roles that have the contacts module enabled and need write access to
# shared.contact_info.  Add new roles here when new butlers enable contacts.
_CONTACTS_MODULE_ROLES = (
    "butler_general_rw",
    "butler_health_rw",
    "butler_relationship_rw",
)

_CONTACT_INFO_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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
                {statement};
            END IF;
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
    # 2. Create shared.contact_info if it doesn't already exist.
    #    Schema-qualified DDL so this runs correctly regardless of the current
    #    search_path set by the Alembic environment.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.contact_info (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL,
            type VARCHAR NOT NULL,
            value TEXT NOT NULL,
            label VARCHAR,
            is_primary BOOLEAN DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_contact_info_type_value
            ON shared.contact_info (type, value)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_contact_info_contact_id
            ON shared.contact_info (contact_id)
    """)

    # -------------------------------------------------------------------------
    # 3. Migrate existing data from current-schema contact_info to shared.
    #    Uses INSERT ... ON CONFLICT DO NOTHING so it is safe to re-run.
    #    Only copies rows when a per-schema contact_info table exists (i.e. the
    #    relationship butler schema where rel_002c ran).
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass(format('%I.contact_info', current_schema())) IS NOT NULL
               AND to_regclass('shared.contact_info') IS NOT NULL
            THEN
                INSERT INTO shared.contact_info
                    (id, contact_id, type, value, label, is_primary, created_at)
                SELECT id, contact_id, type, value, label, is_primary, created_at
                FROM contact_info
                ON CONFLICT (id) DO NOTHING;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 4. Grant write privileges on shared.contact_info to contacts module roles.
    #    Butler roles normally only have SELECT on shared tables (set by
    #    core_001). The contacts module requires INSERT/UPDATE/DELETE here.
    # -------------------------------------------------------------------------
    quoted_shared = _quote_ident("shared")
    quoted_table = f"{quoted_shared}.contact_info"

    for role in _CONTACTS_MODULE_ROLES:
        quoted_role = _quote_ident(role)
        _execute_best_effort(
            f"GRANT {_CONTACT_INFO_TABLE_PRIVILEGES} ON TABLE {quoted_table} TO {quoted_role}",
            role_name=role,
        )
        # Grant usage on the shared schema itself (may already exist, best-effort).
        _execute_best_effort(
            f"GRANT USAGE ON SCHEMA {quoted_shared} TO {quoted_role}",
            role_name=role,
        )


def downgrade() -> None:
    quoted_shared = _quote_ident("shared")
    quoted_table = f"{quoted_shared}.contact_info"

    # Revoke elevated privileges from contacts module roles.
    for role in _CONTACTS_MODULE_ROLES:
        quoted_role = _quote_ident(role)
        _execute_best_effort(
            f"REVOKE INSERT, UPDATE, DELETE ON TABLE {quoted_table} FROM {quoted_role}",
            role_name=role,
        )

    op.execute("DROP INDEX IF EXISTS shared.idx_shared_contact_info_contact_id")
    op.execute("DROP INDEX IF EXISTS shared.idx_shared_contact_info_type_value")
    op.execute("DROP TABLE IF EXISTS shared.contact_info")
