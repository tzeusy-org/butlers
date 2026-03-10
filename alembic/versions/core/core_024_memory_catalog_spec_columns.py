"""memory_catalog_spec_columns: add spec-required columns to shared.memory_catalog

Revision ID: core_024
Revises: core_023
Create Date: 2026-03-10 00:00:00.000000

Adds the spec-required columns that were missing from the core_023 baseline:
  title, predicate, scope, valid_at, invalid_at, confidence, importance,
  retention_class, sensitivity, object_entity_id.

Also adds a composite index on (tenant_id, scope, predicate) for efficient
scope/predicate filtering during cross-butler catalog search.

None of the new columns carry NOT NULL constraints — this migration is
additive and backward-compatible with existing catalog rows.

Spec reference: openspec/changes/memory-spec-alignment/specs/
                memory-discovery-catalog/spec.md
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_024"
down_revision = "core_023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Add spec-required columns to shared.memory_catalog.
    #    All new columns are nullable for backward-compatibility with existing rows.
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE shared.memory_catalog
            ADD COLUMN IF NOT EXISTS title            TEXT,
            ADD COLUMN IF NOT EXISTS predicate        TEXT,
            ADD COLUMN IF NOT EXISTS scope            TEXT,
            ADD COLUMN IF NOT EXISTS valid_at         TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS invalid_at       TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS confidence       DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS importance       DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS retention_class  TEXT,
            ADD COLUMN IF NOT EXISTS sensitivity      TEXT,
            ADD COLUMN IF NOT EXISTS object_entity_id UUID
                REFERENCES shared.entities(id) ON DELETE SET NULL
    """)

    # -------------------------------------------------------------------------
    # 2. Additional indexes for scope/predicate filtering and object_entity_id.
    # -------------------------------------------------------------------------

    # Composite index for scope + predicate filtering within a tenant.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_tenant_scope_predicate
        ON shared.memory_catalog (tenant_id, scope, predicate)
        WHERE scope IS NOT NULL OR predicate IS NOT NULL
    """)

    # Index for object_entity_id (edge-fact lookups).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_object_entity_id
        ON shared.memory_catalog (object_entity_id)
        WHERE object_entity_id IS NOT NULL
    """)

    # Partial index for sensitivity-based filtering (common access pattern:
    # exclude non-'normal' rows unless caller requests elevated sensitivity).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_catalog_sensitivity
        ON shared.memory_catalog (sensitivity)
        WHERE sensitivity IS NOT NULL
    """)


def downgrade() -> None:
    # Drop indexes first, then drop columns.
    op.execute("DROP INDEX IF EXISTS shared.idx_memory_catalog_sensitivity")
    op.execute("DROP INDEX IF EXISTS shared.idx_memory_catalog_object_entity_id")
    op.execute("DROP INDEX IF EXISTS shared.idx_memory_catalog_tenant_scope_predicate")

    op.execute("""
        ALTER TABLE shared.memory_catalog
            DROP COLUMN IF EXISTS object_entity_id,
            DROP COLUMN IF EXISTS sensitivity,
            DROP COLUMN IF EXISTS retention_class,
            DROP COLUMN IF EXISTS importance,
            DROP COLUMN IF EXISTS confidence,
            DROP COLUMN IF EXISTS invalid_at,
            DROP COLUMN IF EXISTS valid_at,
            DROP COLUMN IF EXISTS scope,
            DROP COLUMN IF EXISTS predicate,
            DROP COLUMN IF EXISTS title
    """)
