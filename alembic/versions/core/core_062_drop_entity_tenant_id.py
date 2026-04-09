"""Drop tenant_id column from public.entities.

Revision ID: core_062
Revises: core_061
Create Date: 2026-04-08 00:00:00.000000

Entities are shared across all butlers in a single namespace.  The tenant_id
column was vestigial — RFC 0004 mandated "shared" for all identity tables,
and the actual isolation boundary is schema-based (per-butler schemas).
Inconsistent tenant_id values ("shared" vs "relationship") between code paths
caused duplicate entities that the unique index could not prevent.

This migration:
  1. Consolidates any cross-tenant duplicates (keeps oldest, repoints contacts).
  2. Drops the old indexes that included tenant_id.
  3. Creates new indexes on (canonical_name, entity_type).
  4. Drops the tenant_id column.
"""

from __future__ import annotations

from alembic import op

revision: str = "core_062"
down_revision: str = "core_061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Step 1: Consolidate cross-tenant duplicates (keep oldest) -----------
    # For each (canonical_name, entity_type) group with multiple live rows,
    # tombstone the newer duplicates by marking them as merged_into the oldest.
    op.execute("""
        WITH ranked AS (
            SELECT id, canonical_name, entity_type,
                   ROW_NUMBER() OVER (
                       PARTITION BY canonical_name, entity_type
                       ORDER BY created_at ASC
                   ) AS rn,
                   FIRST_VALUE(id) OVER (
                       PARTITION BY canonical_name, entity_type
                       ORDER BY created_at ASC
                   ) AS survivor_id
            FROM public.entities
            WHERE (metadata->>'merged_into') IS NULL
              AND (metadata->>'deleted_at') IS NULL
        ),
        dupes AS (
            SELECT id, survivor_id FROM ranked WHERE rn > 1
        )
        UPDATE public.entities e
        SET metadata = jsonb_set(
            COALESCE(e.metadata, '{}'::jsonb),
            '{merged_into}',
            to_jsonb(d.survivor_id::text)
        ),
        updated_at = now()
        FROM dupes d
        WHERE e.id = d.id
    """)

    # Repoint contacts from tombstoned duplicates to the survivor entity.
    op.execute("""
        UPDATE public.contacts c
        SET entity_id = (e.metadata->>'merged_into')::uuid,
            updated_at = now()
        FROM public.entities e
        WHERE c.entity_id = e.id
          AND (e.metadata->>'merged_into') IS NOT NULL
    """)

    # -- Step 2: Drop old indexes ------------------------------------------
    # Cover all possible names: original (core_002), renamed (core_047), and
    # already-updated (modified core_002 in this changeset).
    op.execute("DROP INDEX IF EXISTS public.uq_entities_tenant_canonical_type_live")
    op.execute("DROP INDEX IF EXISTS public.uq_entities_canonical_type_live")
    op.execute("DROP INDEX IF EXISTS public.idx_shared_entities_tenant_canonical")
    op.execute("DROP INDEX IF EXISTS public.idx_entities_tenant_canonical")
    op.execute("DROP INDEX IF EXISTS public.idx_entities_canonical")

    # -- Step 3: Create new indexes -----------------------------------------
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_entities_canonical_type_live
        ON public.entities (canonical_name, entity_type)
        WHERE (metadata->>'merged_into') IS NULL
          AND (metadata->>'deleted_at') IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entities_canonical
        ON public.entities (canonical_name)
    """)

    # -- Step 4: Drop the column -------------------------------------------
    op.execute("ALTER TABLE public.entities DROP COLUMN IF EXISTS tenant_id")


def downgrade() -> None:
    # Re-add tenant_id with default 'shared'.
    op.execute("""
        ALTER TABLE public.entities
        ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT 'shared'
    """)

    # Restore tenant-scoped indexes.
    op.execute("DROP INDEX IF EXISTS public.uq_entities_canonical_type_live")
    op.execute("DROP INDEX IF EXISTS public.idx_entities_canonical")
    op.execute("""
        CREATE UNIQUE INDEX uq_entities_tenant_canonical_type_live
        ON public.entities (tenant_id, canonical_name, entity_type)
        WHERE (metadata->>'merged_into') IS NULL
          AND (metadata->>'deleted_at') IS NULL
    """)
    op.execute("""
        CREATE INDEX idx_shared_entities_tenant_canonical
        ON public.entities (tenant_id, canonical_name)
    """)
