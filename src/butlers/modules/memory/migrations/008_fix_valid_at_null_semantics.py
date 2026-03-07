"""fix_valid_at_null_semantics

Makes facts.valid_at nullable with DEFAULT NULL, aligning with the OpenSpec
contract in module-memory/spec.md.

The spec defines:
  - valid_at IS NULL  → property fact (non-temporal, supersedes same key)
  - valid_at IS NOT NULL → temporal fact (coexists with other active facts)

The previous migration (mem_007) added valid_at as NOT NULL DEFAULT now(),
which means property facts could never truly be non-temporal. This migration
fixes that by:

1. Altering facts.valid_at to TIMESTAMPTZ nullable, DEFAULT NULL.
2. Setting existing rows that hold the current-timestamp default to NULL
   (they were property facts stored before any temporal predicate support;
   their stored "now()" is an artifact, not a meaningful point in time).
   NOTE: Only rows where valid_at == created_at (within 1 second) are reset,
   because those were written by the old "default to now()" codepath.
   Rows with an explicitly supplied valid_at (temporal facts) are left alone.
3. Dropping and recreating the three partial uniqueness indexes so they
   enforce uniqueness only among property facts (valid_at IS NULL).
   Temporal facts (valid_at IS NOT NULL) have NO DB-level uniqueness
   constraint — the new indexes all carry AND valid_at IS NULL and therefore
   do not cover temporal rows at all. Duplicate temporal facts (same key,
   same valid_at) must be prevented entirely at the application layer.

Revision ID: mem_008
Revises: mem_007
Create Date: 2026-03-08 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_008"
down_revision = "mem_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop the valid_at-inclusive uniqueness indexes from mem_007.
    op.execute("DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_edge_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active")

    # 2. Alter valid_at to be nullable with DEFAULT NULL.
    op.execute("""
        ALTER TABLE facts
        ALTER COLUMN valid_at DROP NOT NULL,
        ALTER COLUMN valid_at SET DEFAULT NULL
    """)

    # 3. Reset rows where valid_at ≈ created_at (old DEFAULT now() artifact).
    #    These were property facts; their valid_at was never meaningful.
    op.execute("""
        UPDATE facts
        SET valid_at = NULL
        WHERE valid_at IS NOT NULL
          AND ABS(EXTRACT(EPOCH FROM (valid_at - created_at))) < 1
    """)

    # 4. Recreate uniqueness indexes scoped to property facts (valid_at IS NULL).
    #    Property-fact uniqueness: (entity_id, scope, predicate) when entity set.
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate)
        WHERE entity_id IS NOT NULL
          AND object_entity_id IS NULL
          AND validity = 'active'
          AND valid_at IS NULL
    """)

    #    Edge-fact uniqueness: (entity_id, object_entity_id, scope, predicate).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_edge_scope_predicate_active
        ON facts (entity_id, object_entity_id, scope, predicate)
        WHERE object_entity_id IS NOT NULL
          AND validity = 'active'
          AND valid_at IS NULL
    """)

    #    Subject-keyed uniqueness (no entity_id).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_no_entity_subject_predicate_active
        ON facts (scope, subject, predicate)
        WHERE entity_id IS NULL
          AND validity = 'active'
          AND valid_at IS NULL
    """)


def downgrade() -> None:
    # 1. Drop the NULL-scoped indexes.
    op.execute("DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_edge_scope_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active")

    # 2. Restore NOT NULL by back-filling NULL → created_at and adding the constraint.
    op.execute("""
        UPDATE facts SET valid_at = created_at WHERE valid_at IS NULL
    """)
    op.execute("""
        ALTER TABLE facts
        ALTER COLUMN valid_at SET NOT NULL,
        ALTER COLUMN valid_at SET DEFAULT now()
    """)

    # 3. Recreate mem_007-style indexes (with valid_at in the key).
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate, valid_at)
        WHERE entity_id IS NOT NULL
          AND object_entity_id IS NULL
          AND validity = 'active'
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_edge_scope_predicate_active
        ON facts (entity_id, object_entity_id, scope, predicate, valid_at)
        WHERE object_entity_id IS NOT NULL
          AND validity = 'active'
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_no_entity_subject_predicate_active
        ON facts (scope, subject, predicate, valid_at)
        WHERE entity_id IS NULL
          AND validity = 'active'
    """)
