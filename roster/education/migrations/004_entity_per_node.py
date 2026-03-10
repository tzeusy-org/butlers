"""entity_per_node

Add entity_id UUID FK column to education.mind_map_nodes referencing
shared.entities(id), with index, and backfill existing nodes by creating
a shared.entities row for each (canonical_name = '<MapTitle> > <NodeLabel>').

Revision ID: education_004
Revises: education_003
Create Date: 2026-03-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_004"
down_revision = "education_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add nullable entity_id column with FK to shared.entities.
    op.execute("""
        ALTER TABLE education.mind_map_nodes
            ADD COLUMN IF NOT EXISTS entity_id UUID
                REFERENCES shared.entities(id)
    """)

    # 2. Create index on the new column.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mmn_entity_id
            ON education.mind_map_nodes (entity_id)
    """)

    # 3. Backfill: for each node where entity_id IS NULL, create a
    #    shared.entities row and update the node to point at it.
    #    canonical_name = '<map_title> > <node_label>'
    #    entity_type    = 'other'
    #    tenant_id      = 'shared'
    #    metadata       = {"source_butler": "education", "source_scope": "education"}
    #
    #    Uses INSERT … ON CONFLICT DO NOTHING so re-running is idempotent.
    #    After insert we fetch the id via a SELECT (which handles both the
    #    fresh-insert and the pre-existing-row cases uniformly).
    op.execute("""
        DO $$
        DECLARE
            r RECORD;
            v_canonical_name TEXT;
            v_entity_id UUID;
        BEGIN
            FOR r IN
                SELECT
                    n.id                AS node_id,
                    m.title             AS map_title,
                    n.label             AS node_label
                FROM education.mind_map_nodes n
                JOIN education.mind_maps m ON m.id = n.mind_map_id
                WHERE n.entity_id IS NULL
            LOOP
                v_canonical_name := r.map_title || ' > ' || r.node_label;

                -- Insert entity, ignore conflict on (tenant_id, canonical_name, entity_type)
                -- among live (non-tombstoned) entities.
                INSERT INTO shared.entities (
                    tenant_id,
                    canonical_name,
                    entity_type,
                    metadata
                )
                VALUES (
                    'shared',
                    v_canonical_name,
                    'other',
                    '{"source_butler": "education", "source_scope": "education"}'::jsonb
                )
                ON CONFLICT DO NOTHING;

                -- Fetch the entity id (handles both new and pre-existing rows).
                SELECT id INTO v_entity_id
                FROM shared.entities
                WHERE tenant_id      = 'shared'
                  AND canonical_name = v_canonical_name
                  AND entity_type    = 'other'
                  AND (metadata->>'merged_into') IS NULL
                LIMIT 1;

                -- Update the node.
                IF v_entity_id IS NOT NULL THEN
                    UPDATE education.mind_map_nodes
                       SET entity_id  = v_entity_id,
                           updated_at = now()
                     WHERE id = r.node_id;
                END IF;
            END LOOP;
        END
        $$;
    """)


def downgrade() -> None:
    # Drop the index first, then the column.
    # Orphaned shared.entities rows created during upgrade are intentionally left
    # in place (the issue spec says "leaves orphaned entities").
    op.execute("DROP INDEX IF EXISTS education.idx_mmn_entity_id")
    op.execute("""
        ALTER TABLE education.mind_map_nodes
            DROP COLUMN IF EXISTS entity_id
    """)
