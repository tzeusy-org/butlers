"""entities_to_shared: move entities to shared schema, add roles column

Revision ID: core_014
Revises: core_013
Create Date: 2026-03-04 00:00:00.000000

Migrates the entities table from butler-local schemas into the shared schema so
that all butlers can reference entity identity without cross-schema resolution.
Adds a roles TEXT[] column to shared.entities (replacing contacts.roles as the
source of truth for identity roles).

Changes applied in upgrade():

  1. CREATE TABLE shared.entities (if not exists) matching the mem_002 schema
     plus new roles column.  If a butler-local entities table exists with data,
     copy rows into shared.entities.
  2. Add roles TEXT[] NOT NULL DEFAULT '{}' to shared.entities.
  3. Migrate roles data from shared.contacts to shared.entities (via entity_id link).
  4. Create owner entity if owner contact exists without entity link.
  5. Create owner singleton partial unique index on shared.entities.
  6. Re-create FK from facts.entity_id to shared.entities (for memory butler schema).
  7. Grant access to all butler roles.

downgrade() reverses all of the above in reverse order.

Design notes:
  - All DDL is guarded with IF (NOT) EXISTS / DO blocks for idempotency.
  - contacts.roles column was kept for backward compatibility during the
    transition period.  core_016 drops it.
  - The general butler has its own unrelated entities table (collection items);
    search_path ordering (general, shared, public) ensures general.entities
    resolves first for the general butler, so no collision occurs.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_014"
down_revision = "core_013"
branch_labels = None
depends_on = None

# All butler roles that need access to shared.entities.
# NOTE: education, finance, home, and travel were added after this migration was written.
# core_015 grants those roles the same privileges on shared.entities.
_ALL_BUTLER_ROLES = (
    "butler_switchboard_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_relationship_rw",
    "butler_messenger_rw",
    # Added in core_015 (missing from initial implementation):
    # "butler_education_rw",
    # "butler_finance_rw",
    # "butler_home_rw",
    # "butler_travel_rw",
)

_ENTITIES_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

# Butler schemas that may have a memory-module entities table to migrate from.
_MEMORY_BUTLER_SCHEMAS = (
    "education",
    "finance",
    "general",
    "health",
    "home",
    "messenger",
    "relationship",
    "switchboard",
    "travel",
)


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


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create shared.entities table (if it does not exist).
    #    Matches the mem_002 schema plus the new roles column.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.entities') IS NULL THEN
                CREATE TABLE shared.entities (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    tenant_id TEXT NOT NULL,
                    canonical_name VARCHAR NOT NULL,
                    entity_type VARCHAR NOT NULL DEFAULT 'other',
                    aliases TEXT[] NOT NULL DEFAULT '{}',
                    metadata JSONB DEFAULT '{}'::jsonb,
                    roles TEXT[] NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT chk_shared_entities_entity_type CHECK (
                        entity_type IN ('person', 'organization', 'place', 'other')
                    ),
                    CONSTRAINT uq_shared_entities_tenant_canonical_type
                        UNIQUE (tenant_id, canonical_name, entity_type)
                );
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 1b. Add roles column if shared.entities already exists but lacks it.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'entities'
                     AND column_name = 'roles'
               )
            THEN
                ALTER TABLE shared.entities
                    ADD COLUMN roles TEXT[] NOT NULL DEFAULT '{}';
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 1c. Create indexes on shared.entities.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entities_tenant_canonical
        ON shared.entities (tenant_id, canonical_name)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entities_aliases
        ON shared.entities USING gin(aliases)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_shared_entities_metadata
        ON shared.entities USING gin(metadata)
    """)

    # -------------------------------------------------------------------------
    # 1d. Copy data from butler-local entities tables into shared.entities.
    #     For each butler schema, if a local entities table exists AND has rows
    #     not yet in shared.entities, copy them over.
    # -------------------------------------------------------------------------
    for schema in _MEMORY_BUTLER_SCHEMAS:
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('{schema}.entities') IS NOT NULL
                   AND to_regclass('shared.entities') IS NOT NULL
                THEN
                    INSERT INTO shared.entities (
                        id, tenant_id, canonical_name, entity_type,
                        aliases, metadata, created_at, updated_at
                    )
                    SELECT id, tenant_id, canonical_name, entity_type,
                           aliases, metadata, created_at, updated_at
                    FROM {schema}.entities src
                    WHERE NOT EXISTS (
                        SELECT 1 FROM shared.entities dst WHERE dst.id = src.id
                    );
                END IF;
            END
            $$;
        """)

    # -------------------------------------------------------------------------
    # 2. Migrate roles data from shared.contacts to shared.entities.
    #    For contacts that have roles AND are linked to an entity, copy roles
    #    to the entity.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.entities') IS NOT NULL
               AND to_regclass('shared.contacts') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contacts'
                     AND column_name = 'roles'
               )
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'entities'
                     AND column_name = 'roles'
               )
            THEN
                UPDATE shared.entities e
                SET roles = c.roles
                FROM shared.contacts c
                WHERE c.entity_id = e.id
                  AND c.roles != '{}';
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 3. Create owner entity if owner contact exists without entity link.
    #    This ensures the owner always has an entity after migration.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        DECLARE
            v_owner_contact_id UUID;
            v_new_entity_id UUID;
        BEGIN
            IF to_regclass('shared.entities') IS NULL
               OR to_regclass('shared.contacts') IS NULL
            THEN
                RETURN;
            END IF;

            -- Guard: roles column may already have been dropped (idempotency)
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'shared'
                  AND table_name = 'contacts'
                  AND column_name = 'roles'
            ) THEN
                RETURN;
            END IF;

            -- Check for owner contact without entity_id
            SELECT id INTO v_owner_contact_id
            FROM shared.contacts
            WHERE 'owner' = ANY(COALESCE(roles, '{}'))
              AND entity_id IS NULL
            LIMIT 1;

            IF v_owner_contact_id IS NULL THEN
                RETURN;
            END IF;

            -- Create owner entity
            INSERT INTO shared.entities (
                tenant_id, canonical_name, entity_type, roles
            )
            VALUES ('shared', 'Owner', 'person', ARRAY['owner'])
            ON CONFLICT (tenant_id, canonical_name, entity_type) DO NOTHING
            RETURNING id INTO v_new_entity_id;

            -- If entity already existed (ON CONFLICT), fetch its id
            IF v_new_entity_id IS NULL THEN
                SELECT id INTO v_new_entity_id
                FROM shared.entities
                WHERE tenant_id = 'shared'
                  AND canonical_name = 'Owner'
                  AND entity_type = 'person';
            END IF;

            -- Link contact to entity
            IF v_new_entity_id IS NOT NULL THEN
                UPDATE shared.contacts
                SET entity_id = v_new_entity_id
                WHERE id = v_owner_contact_id
                  AND entity_id IS NULL;

                -- Ensure entity has owner role
                UPDATE shared.entities
                SET roles = ARRAY['owner']
                WHERE id = v_new_entity_id
                  AND NOT ('owner' = ANY(roles));
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 4. Create owner singleton partial unique index on shared.entities.
    #    Enforces at most one entity with 'owner' in roles.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_class idx
                   JOIN pg_namespace n ON n.oid = idx.relnamespace
                   WHERE idx.relname = 'ix_entities_owner_singleton'
               )
            THEN
                EXECUTE
                    'CREATE UNIQUE INDEX ix_entities_owner_singleton '
                    'ON shared.entities ((true)) '
                    'WHERE ''owner'' = ANY(roles)';
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 5. Re-create FK from facts.entity_id to shared.entities.
    #    The original FK (from mem_002) points to the butler-local entities
    #    table via search_path.  We need to drop it and re-create pointing to
    #    shared.entities explicitly.
    # -------------------------------------------------------------------------
    for schema in _MEMORY_BUTLER_SCHEMAS:
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('{schema}.facts') IS NOT NULL
                   AND to_regclass('shared.entities') IS NOT NULL
                THEN
                    -- Drop existing FK if it exists
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE c.conname = 'facts_entity_id_fkey'
                          AND n.nspname = '{schema}'
                          AND t.relname = 'facts'
                    ) THEN
                        ALTER TABLE {schema}.facts
                            DROP CONSTRAINT facts_entity_id_fkey;
                    END IF;

                    -- Re-create FK pointing to shared.entities
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = '{schema}'
                          AND table_name = 'facts'
                          AND column_name = 'entity_id'
                    ) AND NOT EXISTS (
                        SELECT 1 FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE c.conname = 'facts_entity_id_shared_fkey'
                          AND n.nspname = '{schema}'
                          AND t.relname = 'facts'
                    ) THEN
                        ALTER TABLE {schema}.facts
                            ADD CONSTRAINT facts_entity_id_shared_fkey
                            FOREIGN KEY (entity_id)
                            REFERENCES shared.entities(id)
                            ON DELETE RESTRICT
                            NOT VALID;
                        ALTER TABLE {schema}.facts
                            VALIDATE CONSTRAINT facts_entity_id_shared_fkey;
                    END IF;
                END IF;
            END
            $$;
        """)

    # -------------------------------------------------------------------------
    # 5b. Re-create FK from shared.contacts.entity_id to shared.entities.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND to_regclass('shared.entities') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'shared'
                     AND table_name = 'contacts'
                     AND column_name = 'entity_id'
               )
            THEN
                -- Drop any existing FK on contacts.entity_id
                IF EXISTS (
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE c.conname = 'contacts_entity_id_fkey'
                      AND n.nspname = 'shared'
                      AND t.relname = 'contacts'
                ) THEN
                    ALTER TABLE shared.contacts
                        DROP CONSTRAINT contacts_entity_id_fkey;
                END IF;

                -- Nullify orphaned entity_id references before adding FK
                UPDATE shared.contacts
                SET entity_id = NULL
                WHERE entity_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM shared.entities
                      WHERE id = shared.contacts.entity_id
                  );

                -- Create FK to shared.entities
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint c
                    JOIN pg_class t ON t.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE c.conname = 'contacts_entity_id_shared_fkey'
                      AND n.nspname = 'shared'
                      AND t.relname = 'contacts'
                ) THEN
                    ALTER TABLE shared.contacts
                        ADD CONSTRAINT contacts_entity_id_shared_fkey
                        FOREIGN KEY (entity_id)
                        REFERENCES shared.entities(id)
                        ON DELETE SET NULL
                        NOT VALID;
                    ALTER TABLE shared.contacts
                        VALIDATE CONSTRAINT contacts_entity_id_shared_fkey;
                END IF;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 6. Grant access to all butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("shared.entities", _ENTITIES_TABLE_PRIVILEGES, role)
        _grant_schema_usage_if_exists("shared", role)

    # -------------------------------------------------------------------------
    # 7. (no-op here) Drop old owner singleton index from contacts.
    #    ix_contacts_owner_singleton is dropped by core_016 together with the
    #    contacts.roles column.  Keeping this comment for cross-reference.
    # -------------------------------------------------------------------------


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # 7. (no-op; index was not dropped in upgrade)
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # 6. Revoke privileges from butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        op.execute(
            f"""
            DO $$
            BEGIN
                IF to_regclass('shared.entities') IS NOT NULL
                   AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}')
                THEN
                    EXECUTE 'REVOKE {_ENTITIES_TABLE_PRIVILEGES} '
                            'ON TABLE shared.entities '
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
    # 5b. Drop contacts entity_id FK to shared.entities.
    # -------------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.contacts') IS NOT NULL
               AND EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                   WHERE c.conname = 'contacts_entity_id_shared_fkey'
                     AND n.nspname = 'shared'
                     AND t.relname = 'contacts'
               )
            THEN
                ALTER TABLE shared.contacts
                    DROP CONSTRAINT contacts_entity_id_shared_fkey;
            END IF;
        END
        $$;
    """)

    # -------------------------------------------------------------------------
    # 5. Drop facts FK to shared.entities and restore to local.
    # -------------------------------------------------------------------------
    for schema in _MEMORY_BUTLER_SCHEMAS:
        op.execute(f"""
            DO $$
            BEGIN
                IF to_regclass('{schema}.facts') IS NOT NULL THEN
                    IF EXISTS (
                        SELECT 1 FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_namespace n ON n.oid = t.relnamespace
                        WHERE c.conname = 'facts_entity_id_shared_fkey'
                          AND n.nspname = '{schema}'
                          AND t.relname = 'facts'
                    ) THEN
                        ALTER TABLE {schema}.facts
                            DROP CONSTRAINT facts_entity_id_shared_fkey;
                    END IF;
                END IF;
            END
            $$;
        """)

    # -------------------------------------------------------------------------
    # 4. Drop owner singleton index on shared.entities.
    # -------------------------------------------------------------------------
    op.execute("DROP INDEX IF EXISTS shared.ix_entities_owner_singleton")

    # -------------------------------------------------------------------------
    # 3. (Data migration is not reversed; roles stay on contacts.)
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # 2. (Roles data migration not reversed.)
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # 1. Drop shared.entities table.
    #    WARNING: This loses all entity data. Only safe in development.
    # -------------------------------------------------------------------------
    op.execute("DROP TABLE IF EXISTS shared.entities CASCADE")
