"""contacts_to_shared: move relationship.contacts to shared schema

Revision ID: core_007
Revises: core_006
Create Date: 2026-02-24 00:00:00.000000

Migrates the contacts table from the relationship butler schema into the shared
schema so that all butlers can reference contact identity without a cross-schema
dependency chain.

Changes applied in upgrade():

  1. ALTER TABLE relationship.contacts SET SCHEMA shared
  2. Add roles TEXT[] NOT NULL DEFAULT '{}' column to shared.contacts
  3. Add secured BOOLEAN NOT NULL DEFAULT false column to shared.contact_info
  4. Replace idx_shared_contact_info_type_value non-unique index with
     UNIQUE(type, value) constraint on shared.contact_info
  5. Add FK shared.contact_info(contact_id) REFERENCES shared.contacts(id)
     ON DELETE CASCADE
  6. Re-create all FK constraints from relationship-schema tables that previously
     referenced relationship.contacts(id) to now reference shared.contacts(id)
  7. Grant SELECT, INSERT, UPDATE, DELETE on shared.contacts to all butler roles
  8. Create partial unique index for owner singleton:
     CREATE UNIQUE INDEX ix_contacts_owner_singleton ON shared.contacts ((true))
     WHERE 'owner' = ANY(roles)

downgrade() reverses all of the above in reverse order.

Design notes:
  - All DDL is guarded with IF (NOT) EXISTS checks for idempotency.
  - FK drop/re-create uses DO blocks that check pg_constraint to be safe when
    the FK has already been migrated to the new reference target.
  - Steps 3-8 guard on shared.contact_info / shared.contacts existing, so the
    migration is safe on fresh installs that have not yet run the contacts module
    chain or the relationship butler chain.
  - Grants are wrapped in to_regclass guards because shared.contacts may not
    yet exist when running incremental upgrade tests (e.g. stopping at core_002).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_007"
down_revision = "core_006"
branch_labels = None
depends_on = None

# All butler roles that need access to shared.contacts.
_ALL_BUTLER_ROLES = (
    "butler_switchboard_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_relationship_rw",
)

_CONTACTS_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

# Relationship-schema tables and their FK metadata.
# Format: (table, constraint_name, column, on_delete_clause)
_REL_CONTACT_FKS = [
    ("relationships", "relationships_contact_a_fkey", "contact_a", "CASCADE"),
    ("relationships", "relationships_contact_b_fkey", "contact_b", "CASCADE"),
    ("important_dates", "important_dates_contact_id_fkey", "contact_id", "CASCADE"),
    ("notes", "notes_contact_id_fkey", "contact_id", "CASCADE"),
    ("interactions", "interactions_contact_id_fkey", "contact_id", "CASCADE"),
    ("reminders", "reminders_contact_id_fkey", "contact_id", "CASCADE"),
    ("gifts", "gifts_contact_id_fkey", "contact_id", "CASCADE"),
    ("loans", "loans_contact_id_fkey", "contact_id", "CASCADE"),
    ("group_members", "group_members_contact_id_fkey", "contact_id", "CASCADE"),
    ("contact_labels", "contact_labels_contact_id_fkey", "contact_id", "CASCADE"),
    ("quick_facts", "quick_facts_contact_id_fkey", "contact_id", "CASCADE"),
    ("activity_feed", "activity_feed_contact_id_fkey", "contact_id", "CASCADE"),
    ("contact_info", "contact_info_contact_id_fkey", "contact_id", "CASCADE"),
    ("addresses", "addresses_contact_id_fkey", "contact_id", "CASCADE"),
    ("life_events", "life_events_contact_id_fkey", "contact_id", "CASCADE"),
    ("tasks", "tasks_contact_id_fkey", "contact_id", "CASCADE"),
    ("loans", "loans_lender_contact_id_fkey", "lender_contact_id", "SET NULL"),
    ("loans", "loans_borrower_contact_id_fkey", "borrower_contact_id", "SET NULL"),
]


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table exists and role exists.

    Guards against undefined_table / invalid_schema_name errors when the table
    has not yet been created (e.g. fresh installs running migrations in steps).
    Also tolerates insufficient_privilege and undefined_object (missing role).
    """
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


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Move relationship.contacts to shared schema.
    #    Guard: only when relationship.contacts exists and shared.contacts does not.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('relationship.contacts') IS NOT NULL
               AND to_regclass('shared.contacts') IS NULL
            THEN
                ALTER TABLE relationship.contacts SET SCHEMA shared;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 2. Add roles TEXT[] NOT NULL DEFAULT '{}' to shared.contacts.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contacts'
                     AND column_name = 'roles'
               )
            THEN
                ALTER TABLE shared.contacts
                    ADD COLUMN roles TEXT[] NOT NULL DEFAULT '{}';
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Add secured BOOLEAN NOT NULL DEFAULT false to shared.contact_info.
    #    Guard: contact_info may not exist yet on fresh installs (it is created
    #    by the contacts_002 module migration).
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contact_info'
                     AND column_name = 'secured'
               )
            THEN
                ALTER TABLE shared.contact_info
                    ADD COLUMN secured BOOLEAN NOT NULL DEFAULT false;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 4a. Drop non-unique index idx_shared_contact_info_type_value
    #     (replaced by UNIQUE constraint in 4b).
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.idx_shared_contact_info_type_value")

    # -------------------------------------------------------------------------
    # 4b. Add UNIQUE(type, value) constraint on shared.contact_info.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                   WHERE c.conname = 'uq_shared_contact_info_type_value'
                     AND n.nspname = 'shared'
                     AND t.relname = 'contact_info'
               )
            THEN
                ALTER TABLE shared.contact_info
                    ADD CONSTRAINT uq_shared_contact_info_type_value
                    UNIQUE (type, value);
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 5. Add FK shared.contact_info(contact_id) REFERENCES shared.contacts(id).
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL
               AND to_regclass('shared.contacts') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                   WHERE c.conname = 'shared_contact_info_contact_id_fkey'
                     AND n.nspname = 'shared'
                     AND t.relname = 'contact_info'
               )
            THEN
                ALTER TABLE shared.contact_info
                    ADD CONSTRAINT shared_contact_info_contact_id_fkey
                    FOREIGN KEY (contact_id)
                    REFERENCES shared.contacts(id)
                    ON DELETE CASCADE;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 6. Re-create relationship-schema FK constraints to reference shared.contacts.
    #    For each relationship-schema table: drop the old FK (if it exists) and
    #    add a new FK pointing at shared.contacts(id).
    # -------------------------------------------------------------------------
    for table, constraint, column, on_delete in _REL_CONTACT_FKS:
        op.execute(
            f"""
            DO $$
            DECLARE
                v_table_oid OID;
            BEGIN
                -- Skip if table does not exist in relationship schema.
                IF to_regclass('relationship.{table}') IS NULL THEN
                    RETURN;
                END IF;

                v_table_oid := to_regclass('relationship.{table}');

                -- Drop old FK if it exists (regardless of what it references).
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{constraint}'
                      AND conrelid = v_table_oid
                ) THEN
                    EXECUTE format(
                        'ALTER TABLE relationship.%I DROP CONSTRAINT %I',
                        '{table}',
                        '{constraint}'
                    );
                END IF;

                -- Add new FK pointing at shared.contacts.
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{constraint}'
                      AND conrelid = v_table_oid
                ) THEN
                    EXECUTE format(
                        'ALTER TABLE relationship.%I '
                        'ADD CONSTRAINT %I '
                        'FOREIGN KEY (%I) '
                        'REFERENCES shared.contacts(id) '
                        'ON DELETE {on_delete}',
                        '{table}',
                        '{constraint}',
                        '{column}'
                    );
                END IF;
            END
            $$;
            """
        )

    # -------------------------------------------------------------------------
    # 7. Grant SELECT, INSERT, UPDATE, DELETE on shared.contacts to all butler
    #    roles.  Each grant is guarded so it is safe when shared.contacts or the
    #    role does not yet exist (e.g. incremental migration test environments).
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("shared.contacts", _CONTACTS_TABLE_PRIVILEGES, role)
        _grant_schema_usage_if_exists("shared", role)

    # -------------------------------------------------------------------------
    # 8. Partial unique index for owner singleton: enforces at most one contact
    #    with 'owner' in roles.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_class idx
                   JOIN pg_namespace n ON n.oid = idx.relnamespace
                   WHERE idx.relname = 'ix_contacts_owner_singleton'
               )
            THEN
                EXECUTE
                    'CREATE UNIQUE INDEX ix_contacts_owner_singleton '
                    'ON shared.contacts ((true)) '
                    'WHERE ''owner'' = ANY(roles)';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # 8. Drop owner singleton partial unique index.
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.ix_contacts_owner_singleton")

    # -------------------------------------------------------------------------
    # 7. Revoke write privileges from butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('shared.contacts') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
                THEN
                    EXECUTE 'REVOKE INSERT, UPDATE, DELETE '
                            'ON TABLE shared.contacts '
                            'FROM {_quote_ident(role)}';
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

    # -------------------------------------------------------------------------
    # 6. Drop the shared-pointing FK constraints from relationship tables.
    #    (The relationship chain's own migration history will re-create them if
    #    re-run from scratch.)
    # -------------------------------------------------------------------------
    for table, constraint, _column, _on_delete in _REL_CONTACT_FKS:
        op.execute(
            f"""
            DO $$
            DECLARE
                v_table_oid OID;
            BEGIN
                IF to_regclass('relationship.{table}') IS NULL THEN
                    RETURN;
                END IF;
                v_table_oid := to_regclass('relationship.{table}');
                IF EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = '{constraint}'
                      AND conrelid = v_table_oid
                ) THEN
                    EXECUTE format(
                        'ALTER TABLE relationship.%I DROP CONSTRAINT %I',
                        '{table}',
                        '{constraint}'
                    );
                END IF;
            END
            $$;
            """
        )

    # -------------------------------------------------------------------------
    # 5. Drop FK from shared.contact_info to shared.contacts.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                   WHERE c.conname = 'shared_contact_info_contact_id_fkey'
                     AND n.nspname = 'shared'
                     AND t.relname = 'contact_info'
               )
            THEN
                ALTER TABLE shared.contact_info
                    DROP CONSTRAINT shared_contact_info_contact_id_fkey;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 4b. Drop UNIQUE constraint.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                   WHERE c.conname = 'uq_shared_contact_info_type_value'
                     AND n.nspname = 'shared'
                     AND t.relname = 'contact_info'
               )
            THEN
                ALTER TABLE shared.contact_info
                    DROP CONSTRAINT uq_shared_contact_info_type_value;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 4a. Restore non-unique index.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL THEN
                EXECUTE
                    'CREATE INDEX IF NOT EXISTS idx_shared_contact_info_type_value '
                    'ON shared.contact_info (type, value)';
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Drop secured column from shared.contact_info.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contact_info') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contact_info'
                     AND column_name = 'secured'
               )
            THEN
                ALTER TABLE shared.contact_info DROP COLUMN secured;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 2. Drop roles column from shared.contacts.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contacts'
                     AND column_name = 'roles'
               )
            THEN
                ALTER TABLE shared.contacts DROP COLUMN roles;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 1. Move shared.contacts back to relationship schema.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND to_regclass('relationship.contacts') IS NULL
            THEN
                ALTER TABLE shared.contacts SET SCHEMA relationship;
            END IF;
        END
        $$;
    """)
