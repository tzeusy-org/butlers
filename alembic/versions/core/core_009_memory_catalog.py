"""memory_catalog: cross-butler searchable discovery index in the public schema

Revision ID: core_009
Revises: core_008
Create Date: 2026-03-26 00:00:00.000000

Collapsed from: core_023_shared_memory_catalog, core_024_memory_catalog_spec_columns

Creates ``public.memory_catalog`` as a discovery index (NOT a canonical store).
Butler roles receive narrow SELECT, INSERT, UPDATE grants only -- catalog entries
are written by the owning butler when facts/rules are created.  The catalog stores
a searchable summary plus provenance pointers back to the owning butler schema.
Cross-butler search queries this table; full recall routes back to the canonical
owning schema.

Table design:
  - UNIQUE on (source_schema, source_table, source_id) -- one catalog row per
    canonical memory item.
  - pgvector IVFFlat index on ``embedding`` for approximate nearest-neighbour
    search across butlers.
  - GIN index on ``search_vector`` for full-text search.
  - B-tree index on (tenant_id, source_schema) for tenant-scoped filtering.
  - B-tree index on entity_id for entity-anchored lookups.
  - Spec-required columns from core_024: title, predicate, scope, valid_at,
    invalid_at, confidence, importance, retention_class, sensitivity,
    object_entity_id.
  - Composite index on (tenant_id, scope, predicate) for scope/predicate filtering.
  - Partial index on object_entity_id and sensitivity.

Grant model:
  All butler roles receive SELECT, INSERT, UPDATE on public.memory_catalog and
  USAGE on the public schema.  DELETE is intentionally withheld; catalog GC is
  handled by the owning butler or a periodic sweep.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_009"
down_revision = "core_008"
branch_labels = None
depends_on = None

# All butler roles that need catalog write-behind access.
_ALL_BUTLER_ROLES = (
    "butler_switchboard_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_relationship_rw",
    "butler_messenger_rw",
    "butler_education_rw",
    "butler_finance_rw",
    "butler_home_rw",
    "butler_travel_rw",
)

# Narrow privileges: INSERT and UPDATE (catalog is eventually consistent;
# owning butler writes its own entries).  SELECT granted for read-back.
# DELETE withheld -- GC is centralised.
_CATALOG_PRIVILEGES = "SELECT, INSERT, UPDATE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    safe_table_fqn = table_fqn.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{safe_table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
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
    safe_schema = schema.replace("'", "''")
    safe_role = role.replace("'", "''")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.schemata
                WHERE schema_name = '{safe_schema}'
            ) AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{safe_role}')
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
    # 1. Ensure pgvector extension is available.
    # -------------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA public")

    # -------------------------------------------------------------------------
    # 2. Create public.memory_catalog table (core_023 + core_024 columns merged).
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.memory_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),

            -- Provenance -- where to find the canonical memory item.
            source_schema   TEXT NOT NULL,
            source_table    TEXT NOT NULL,
            source_id       UUID NOT NULL,

            -- Owning butler identity.
            source_butler   TEXT,

            -- Multi-tenant isolation (mirrors the value on the source row).
            tenant_id       TEXT NOT NULL DEFAULT 'owner',

            -- Optional entity link for entity-anchored catalog lookups.
            entity_id       UUID,

            -- Searchable summary (not the full content).
            summary         TEXT NOT NULL DEFAULT '',

            -- Semantic search vector (384-d, matching memory module embeddings).
            embedding       vector(384),

            -- Full-text search vector.
            search_vector   tsvector,

            -- Memory type discriminator: 'fact' | 'rule'.
            memory_type     TEXT NOT NULL DEFAULT 'fact',

            -- Timestamps.
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

            -- Spec-required columns (from core_024, all nullable for
            -- backward-compatibility with existing catalog rows).
            title            TEXT,
            predicate        TEXT,
            scope            TEXT,
            valid_at         TIMESTAMPTZ,
            invalid_at       TIMESTAMPTZ,
            confidence       DOUBLE PRECISION,
            importance       DOUBLE PRECISION,
            retention_class  TEXT,
            sensitivity      TEXT,
            object_entity_id UUID
                REFERENCES public.entities(id) ON DELETE SET NULL,

            -- One catalog row per canonical memory item.
            CONSTRAINT uq_memory_catalog_source
                UNIQUE (source_schema, source_table, source_id)
        )
    """)

    # -------------------------------------------------------------------------
    # 3. Create indexes (from core_023).
    # -------------------------------------------------------------------------

    # IVFFlat approximate nearest-neighbour index on embedding.
    # lists=100 is a reasonable default for up to ~1M rows.
    # Cosine distance operator class (vector_cosine_ops).
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_class idx
                JOIN pg_namespace n ON n.oid = idx.relnamespace
                WHERE idx.relname = 'idx_memory_catalog_embedding'
                  AND n.nspname = 'public'
            ) THEN
                CREATE INDEX idx_memory_catalog_embedding
                ON public.memory_catalog
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
            END IF;
        END
        $$;
    """)

    # GIN index on search_vector for full-text search.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_search_vector
        ON public.memory_catalog USING gin(search_vector)
    """)

    # Tenant + source_schema B-tree index for tenant-scoped filtering.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_tenant_schema
        ON public.memory_catalog (tenant_id, source_schema)
    """)

    # Entity_id B-tree index for entity-anchored lookups.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_entity_id
        ON public.memory_catalog (entity_id)
        WHERE entity_id IS NOT NULL
    """)

    # -------------------------------------------------------------------------
    # 4. Create indexes (from core_024).
    # -------------------------------------------------------------------------

    # Composite index for scope + predicate filtering within a tenant.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_tenant_scope_predicate
        ON public.memory_catalog (tenant_id, scope, predicate)
        WHERE scope IS NOT NULL OR predicate IS NOT NULL
    """)

    # Index for object_entity_id (edge-fact lookups).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_object_entity_id
        ON public.memory_catalog (object_entity_id)
        WHERE object_entity_id IS NOT NULL
    """)

    # Partial index for sensitivity-based filtering.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_sensitivity
        ON public.memory_catalog (sensitivity)
        WHERE sensitivity IS NOT NULL
    """)

    # -------------------------------------------------------------------------
    # 5. Grant narrow access to all butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("public.memory_catalog", _CATALOG_PRIVILEGES, role)
        _grant_schema_usage_if_exists("public", role)


def downgrade() -> None:
    # Drop indexes (core_024), then indexes (core_023), then the table.
    op.execute("DROP INDEX IF EXISTS public.idx_memory_catalog_sensitivity")
    op.execute("DROP INDEX IF EXISTS public.idx_memory_catalog_object_entity_id")
    op.execute("DROP INDEX IF EXISTS public.idx_memory_catalog_tenant_scope_predicate")
    op.execute("DROP INDEX IF EXISTS public.idx_memory_catalog_entity_id")
    op.execute("DROP INDEX IF EXISTS public.idx_memory_catalog_tenant_schema")
    op.execute("DROP INDEX IF EXISTS public.idx_memory_catalog_search_vector")
    op.execute("DROP INDEX IF EXISTS public.idx_memory_catalog_embedding")
    op.execute("DROP TABLE IF EXISTS public.memory_catalog CASCADE")
