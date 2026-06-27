"""Add entity_id column to education.mind_map_nodes.

Revision ID: education_003
Revises: education_002
Create Date: 2026-06-27 00:00:00.000000

``mind_map_node_create`` (roster/education/tools/mind_map_nodes.py) creates a
shared ``public.entities`` row per node and then runs::

    UPDATE education.mind_map_nodes SET entity_id = $1 WHERE id = $2

but the original 001 migration never created the ``entity_id`` column, so any
real node-create call fails at runtime with ``UndefinedColumnError``. Mocked-pool
unit tests don't bind the SQL to a backend and so never caught it.

This migration adds the nullable ``entity_id UUID`` column, a conditional FK to
``public.entities(id)`` (guarded by ``to_regclass`` so it is a no-op when the
core identity table is absent), a lookup index, and backfills existing nodes by
creating/resolving their shared entity (mirroring the runtime write path).

See openspec/specs/module-education-mind-map/spec.md, "Entity-per-node
anchoring": the column SHALL be a nullable UUID FK and a migration SHALL backfill
existing nodes.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_003"
down_revision = "education_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Nullable column (nullable for backward compatibility with legacy nodes).
    op.execute("""
        ALTER TABLE education.mind_map_nodes
        ADD COLUMN IF NOT EXISTS entity_id UUID
    """)

    # 2. Conditional FK to public.entities. Guarded by to_regclass so the
    #    migration is a no-op when the core identity table is not present
    #    (e.g. a butler schema without the core identity chain). ON DELETE SET
    #    NULL keeps the node row when its shared entity is removed.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('public.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   JOIN pg_namespace n ON n.oid = t.relnamespace
                   WHERE c.conname = 'mind_map_nodes_entity_id_fkey'
                     AND t.relname = 'mind_map_nodes'
                     AND n.nspname = 'education'
               )
            THEN
                ALTER TABLE education.mind_map_nodes
                    ADD CONSTRAINT mind_map_nodes_entity_id_fkey
                    FOREIGN KEY (entity_id)
                    REFERENCES public.entities(id)
                    ON DELETE SET NULL;
            END IF;
        END
        $$;
    """)

    # 3. Lookup index for entity -> node joins.
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mmn_entity_id
            ON education.mind_map_nodes (entity_id)
            WHERE entity_id IS NOT NULL
    """)

    # 4. Backfill existing nodes by creating/resolving their shared entity.
    #    Mirrors mind_map_node_create: canonical_name = '<map_title> > <label>',
    #    entity_type 'other', metadata tags the education source. Guarded by
    #    to_regclass so it is skipped when public.entities is absent.
    op.execute("""
        DO $$
        DECLARE
            n   RECORD;
            eid UUID;
            cname TEXT;
        BEGIN
            IF to_regclass('public.entities') IS NULL THEN
                RETURN;
            END IF;

            FOR n IN
                SELECT mmn.id AS node_id, mmn.mind_map_id, mmn.label, mm.title
                FROM education.mind_map_nodes mmn
                JOIN education.mind_maps mm ON mm.id = mmn.mind_map_id
                WHERE mmn.entity_id IS NULL
            LOOP
                cname := n.title || ' > ' || n.label;

                INSERT INTO public.entities (canonical_name, entity_type, aliases, metadata)
                VALUES (
                    cname,
                    'other',
                    '{}',
                    jsonb_build_object(
                        'source_butler', 'education',
                        'source_scope', 'education',
                        'mind_map_id', n.mind_map_id::text
                    )
                )
                ON CONFLICT DO NOTHING;

                SELECT id INTO eid
                FROM public.entities
                WHERE canonical_name = cname
                  AND entity_type = 'other'
                  AND (metadata->>'merged_into') IS NULL
                LIMIT 1;

                IF eid IS NOT NULL THEN
                    UPDATE education.mind_map_nodes
                    SET entity_id = eid
                    WHERE id = n.node_id;
                END IF;
            END LOOP;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS education.idx_mmn_entity_id")
    op.execute("""
        ALTER TABLE education.mind_map_nodes
        DROP CONSTRAINT IF EXISTS mind_map_nodes_entity_id_fkey
    """)
    op.execute("""
        ALTER TABLE education.mind_map_nodes
        DROP COLUMN IF EXISTS entity_id
    """)
