"""fix_partial_unique_deleted_at — mem_021

Widen the partial unique index on shared.entities to also exclude
soft-deleted entities (metadata->>'deleted_at' IS NOT NULL).

Migration 018 only excluded merged entities (metadata->>'merged_into'),
but delete_entity sets metadata->>'deleted_at' instead. This caused
"already exists" errors when recreating an entity whose predecessor
was soft-deleted rather than merged.

Revision ID: mem_021
Revises: mem_020
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_021"
down_revision = "mem_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old partial index (may exclude only merged_into)
    op.execute("""
        DROP INDEX IF EXISTS shared.uq_entities_tenant_canonical_type_live
    """)

    # Recreate with both tombstone conditions excluded
    op.execute("""
        CREATE UNIQUE INDEX uq_entities_tenant_canonical_type_live
        ON shared.entities (tenant_id, canonical_name, entity_type)
        WHERE (metadata->>'merged_into') IS NULL
          AND (metadata->>'deleted_at') IS NULL
    """)


def downgrade() -> None:
    # Revert to the 018 version (only merged_into excluded)
    op.execute("""
        DROP INDEX IF EXISTS shared.uq_entities_tenant_canonical_type_live
    """)

    op.execute("""
        CREATE UNIQUE INDEX uq_entities_tenant_canonical_type_live
        ON shared.entities (tenant_id, canonical_name, entity_type)
        WHERE (metadata->>'merged_into') IS NULL
    """)
