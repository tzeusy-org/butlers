"""partial_unique_entities — mem_018

Replace the absolute unique constraint on (tenant_id, canonical_name, entity_type)
with a partial unique index that excludes tombstoned entities.

Entities can be tombstoned in two ways:
- Merged: metadata->>'merged_into' is set (via entity_merge)
- Soft-deleted: metadata->>'deleted_at' is set (via delete_entity API)

Previously, the unique constraint blocked recreation of an entity with the same
(tenant_id, canonical_name, entity_type) even when the original was tombstoned.

The new partial index allows recreating entities whose predecessor was merged
or deleted, while still preventing true duplicates among live entities.

Revision ID: mem_018
Revises: mem_017
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_018"
down_revision = "mem_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old absolute unique constraint (may not exist if table was
    # recreated by migration 006 without it — DROP IF EXISTS is safe).
    op.execute("""
        ALTER TABLE shared.entities
        DROP CONSTRAINT IF EXISTS uq_entities_tenant_canonical_type
    """)

    # Create a partial unique index excluding tombstoned entities
    # (both merged and soft-deleted)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_tenant_canonical_type_live
        ON shared.entities (tenant_id, canonical_name, entity_type)
        WHERE (metadata->>'merged_into') IS NULL
          AND (metadata->>'deleted_at') IS NULL
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX IF EXISTS shared.uq_entities_tenant_canonical_type_live
    """)

    op.execute("""
        ALTER TABLE shared.entities
        ADD CONSTRAINT uq_entities_tenant_canonical_type
        UNIQUE (tenant_id, canonical_name, entity_type)
    """)
