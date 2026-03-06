"""bitemporal_facts

Adds valid_at TIMESTAMPTZ column to facts table and is_temporal flag to
predicate_registry for temporal/bitemporal fact support. Updates fact
uniqueness indexes to include valid_at, allowing multiple facts with the same
entity/predicate but different valid_at to coexist (temporal supersession).

Revision ID: mem_007
Revises: mem_006
Create Date: 2026-03-06 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_007"
down_revision = "mem_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add valid_at TIMESTAMPTZ column to facts table.
    op.execute("""
        ALTER TABLE facts
        ADD COLUMN IF NOT EXISTS valid_at TIMESTAMPTZ NOT NULL DEFAULT now()
    """)

    # 2. Add is_temporal BOOLEAN column to predicate_registry.
    op.execute("""
        ALTER TABLE predicate_registry
        ADD COLUMN IF NOT EXISTS is_temporal BOOLEAN NOT NULL DEFAULT false
    """)

    # 3. Drop and recreate uniqueness indexes to include valid_at.
    #    This allows temporal facts (same entity/predicate, different valid_at)
    #    to coexist without violating uniqueness constraints.

    # 3a. Drop existing unique indexes.
    op.execute("DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_edge_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active")

    # 3b. Recreate with valid_at included.
    #     Property facts (entity_id set, object_entity_id NULL):
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate, valid_at)
        WHERE entity_id IS NOT NULL
          AND object_entity_id IS NULL
          AND validity = 'active'
    """)

    #     Edge facts (object_entity_id set):
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_edge_scope_predicate_active
        ON facts (entity_id, object_entity_id, scope, predicate, valid_at)
        WHERE object_entity_id IS NOT NULL
          AND validity = 'active'
    """)

    #     Facts without entity (free-form subject):
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_no_entity_subject_predicate_active
        ON facts (scope, subject, predicate, valid_at)
        WHERE entity_id IS NULL
          AND validity = 'active'
    """)

    # 4. Seed meal predicates into predicate_registry with is_temporal=true.
    op.execute(
        "INSERT INTO predicate_registry"
        " (name, expected_subject_type, is_temporal, description) VALUES"
        " ('meal_breakfast', 'person', true,"
        "  'Breakfast meal eaten at specific time'),"
        " ('meal_lunch', 'person', true,"
        "  'Lunch meal eaten at specific time'),"
        " ('meal_dinner', 'person', true,"
        "  'Dinner meal eaten at specific time'),"
        " ('meal_snack', 'person', true,"
        "  'Snack eaten at specific time')"
        " ON CONFLICT (name) DO UPDATE SET"
        " is_temporal = EXCLUDED.is_temporal,"
        " expected_subject_type = EXCLUDED.expected_subject_type"
    )


def downgrade() -> None:
    # 1. Drop the new unique indexes.
    op.execute("DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_edge_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active")

    # 2. Recreate the old indexes (without valid_at).
    #    Property facts:
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate)
        WHERE entity_id IS NOT NULL
          AND object_entity_id IS NULL
          AND validity = 'active'
    """)

    #    Edge facts:
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_edge_scope_predicate_active
        ON facts (entity_id, object_entity_id, scope, predicate)
        WHERE object_entity_id IS NOT NULL
          AND validity = 'active'
    """)

    #    Facts without entity:
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_no_entity_subject_predicate_active
        ON facts (scope, subject, predicate)
        WHERE entity_id IS NULL
          AND validity = 'active'
    """)

    # 3. Remove is_temporal column from predicate_registry.
    op.execute("""
        ALTER TABLE predicate_registry
        DROP COLUMN IF EXISTS is_temporal
    """)

    # 4. Remove valid_at column from facts.
    op.execute("""
        ALTER TABLE facts
        DROP COLUMN IF EXISTS valid_at
    """)
