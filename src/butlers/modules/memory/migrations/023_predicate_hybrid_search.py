"""predicate_hybrid_search — mem_023

Upgrade predicate_registry for hybrid search (trigram + full-text + semantic).

Changes:
  1. CREATE EXTENSION IF NOT EXISTS pg_trgm
  2. Add search_vector tsvector column with auto-update trigger
  3. Add description_embedding vector(384) column
  4. Add usage_count INTEGER and last_used_at TIMESTAMPTZ columns
  5. GIN index on name via gin_trgm_ops (trigram)
  6. GIN index on search_vector (full-text)
  7. Backfill search_vector for existing rows

Note: description_embedding is populated at runtime by the embedding engine
when predicates are created/updated.  It starts NULL for existing rows and
is populated lazily on first access or via a backfill script.

Revision ID: mem_023
Revises: mem_022
Create Date: 2026-03-20 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_023"
down_revision = "mem_022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. pg_trgm extension for trigram-based fuzzy name matching
    # -------------------------------------------------------------------------
    # pg_trgm must live in public so all schemas can use gin_trgm_ops.
    # If a prior migration installed it in a butler schema, relocate it first.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_extension e
                JOIN pg_namespace n ON e.extnamespace = n.oid
                WHERE e.extname = 'pg_trgm' AND n.nspname != 'public'
            ) THEN
                ALTER EXTENSION pg_trgm SET SCHEMA public;
            END IF;
        END;
        $$
    """)
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm SCHEMA public")

    # -------------------------------------------------------------------------
    # 2. Add search_vector tsvector column
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE predicate_registry
            ADD COLUMN IF NOT EXISTS search_vector tsvector
    """)

    # -------------------------------------------------------------------------
    # 3. Add description_embedding vector(384) for semantic similarity
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE predicate_registry
            ADD COLUMN IF NOT EXISTS description_embedding vector(384)
    """)

    # -------------------------------------------------------------------------
    # 4. Add usage tracking columns
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE predicate_registry
            ADD COLUMN IF NOT EXISTS usage_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_used_at TIMESTAMPTZ
    """)

    # -------------------------------------------------------------------------
    # 5. Trigger: keep search_vector in sync on INSERT / UPDATE
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE OR REPLACE FUNCTION predicate_registry_search_vector_trigger()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.name, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.description, '')), 'B');
            RETURN NEW;
        END;
        $$
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS trg_predicate_registry_search_vector
            ON predicate_registry
    """)

    op.execute("""
        CREATE TRIGGER trg_predicate_registry_search_vector
        BEFORE INSERT OR UPDATE OF name, description
        ON predicate_registry
        FOR EACH ROW
        EXECUTE FUNCTION predicate_registry_search_vector_trigger()
    """)

    # -------------------------------------------------------------------------
    # 6. Backfill search_vector for existing rows
    # -------------------------------------------------------------------------
    op.execute("""
        UPDATE predicate_registry
        SET search_vector =
            setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(description, '')), 'B')
        WHERE search_vector IS NULL
    """)

    # -------------------------------------------------------------------------
    # 7. GIN index on name (trigram) for fuzzy name matching
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_name_trgm
        ON predicate_registry
        USING GIN (name gin_trgm_ops)
    """)

    # -------------------------------------------------------------------------
    # 8. GIN index on search_vector (full-text)
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_predicate_registry_search_vector
        ON predicate_registry
        USING GIN (search_vector)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_predicate_registry_search_vector")
    op.execute("DROP INDEX IF EXISTS idx_predicate_registry_name_trgm")
    op.execute("""
        DROP TRIGGER IF EXISTS trg_predicate_registry_search_vector
            ON predicate_registry
    """)
    op.execute("DROP FUNCTION IF EXISTS predicate_registry_search_vector_trigger()")
    op.execute("""
        ALTER TABLE predicate_registry
            DROP COLUMN IF EXISTS last_used_at,
            DROP COLUMN IF EXISTS usage_count,
            DROP COLUMN IF EXISTS description_embedding,
            DROP COLUMN IF EXISTS search_vector
    """)
