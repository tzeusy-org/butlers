"""memory_object_entity_id

Adds object_entity_id column to facts table for knowledge graph edge facts.
When set, a fact represents a directed edge from entity_id (subject) to
object_entity_id (object).  When NULL, the fact remains a property fact.

Revision ID: mem_004
Revises: mem_003
Create Date: 2026-03-06 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_004"
down_revision = "mem_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add nullable object_entity_id column with FK to shared.entities.
    op.execute("""
        ALTER TABLE facts
        ADD COLUMN IF NOT EXISTS object_entity_id UUID
    """)

    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   WHERE c.conname = 'facts_object_entity_id_shared_fkey'
                     AND t.relname = 'facts'
               )
            THEN
                ALTER TABLE facts
                    ADD CONSTRAINT facts_object_entity_id_shared_fkey
                    FOREIGN KEY (object_entity_id)
                    REFERENCES shared.entities(id)
                    ON DELETE RESTRICT;
            END IF;
        END
        $$;
    """)

    # 2. Partial index on object_entity_id for efficient edge lookups.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_object_entity_id
        ON facts (object_entity_id)
        WHERE object_entity_id IS NOT NULL
    """)

    # 3. Update property-fact uniqueness index to exclude edge facts.
    #    The original idx_facts_entity_scope_predicate_active (from mem_002)
    #    used WHERE entity_id IS NOT NULL AND validity = 'active'.
    #    Now we add AND object_entity_id IS NULL so property facts and edge
    #    facts on the same entity/scope/predicate don't conflict.
    op.execute("DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active")

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate)
        WHERE entity_id IS NOT NULL
          AND object_entity_id IS NULL
          AND validity = 'active'
    """)

    # 4. Edge-fact uniqueness: one active edge per (entity, object, scope, predicate).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_edge_scope_predicate_active
        ON facts (entity_id, object_entity_id, scope, predicate)
        WHERE object_entity_id IS NOT NULL
          AND validity = 'active'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_facts_edge_scope_predicate_active")

    # Restore original property-fact uniqueness index (without object_entity_id clause).
    op.execute("DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate)
        WHERE entity_id IS NOT NULL AND validity = 'active'
    """)

    op.execute("DROP INDEX IF EXISTS idx_facts_object_entity_id")

    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE c.conname = 'facts_object_entity_id_shared_fkey'
                  AND t.relname = 'facts'
            ) THEN
                ALTER TABLE facts
                    DROP CONSTRAINT facts_object_entity_id_shared_fkey;
            END IF;
        END
        $$;
    """)

    op.execute("ALTER TABLE facts DROP COLUMN IF EXISTS object_entity_id")
