"""drop_shadow_entities

Drop per-butler entities tables that shadow shared.entities.  After this
migration the only entities table is shared.entities; per-butler facts keep
an FK to shared.entities(id).

Revision ID: mem_006
Revises: mem_005
Create Date: 2026-03-06 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_006"
down_revision = "mem_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop local FK facts.entity_id -> entities(id) (auto-generated name).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE c.conname = 'facts_entity_id_fkey'
                  AND t.relname = 'facts'
            ) THEN
                ALTER TABLE facts DROP CONSTRAINT facts_entity_id_fkey;
            END IF;
        END
        $$;
    """)

    # 2. Drop the per-butler entities table (shadow of shared.entities).
    #    Use CASCADE to also drop indexes created by mem_002.
    op.execute("DROP TABLE IF EXISTS entities CASCADE")

    # 3. Re-add FK from facts.entity_id to shared.entities.
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('shared.entities') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1 FROM pg_constraint c
                   JOIN pg_class t ON t.oid = c.conrelid
                   WHERE c.conname = 'facts_entity_id_shared_fkey'
                     AND t.relname = 'facts'
               )
            THEN
                ALTER TABLE facts
                    ADD CONSTRAINT facts_entity_id_shared_fkey
                    FOREIGN KEY (entity_id)
                    REFERENCES shared.entities(id)
                    ON DELETE RESTRICT;
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    # Drop the shared FK.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON t.oid = c.conrelid
                WHERE c.conname = 'facts_entity_id_shared_fkey'
                  AND t.relname = 'facts'
            ) THEN
                ALTER TABLE facts DROP CONSTRAINT facts_entity_id_shared_fkey;
            END IF;
        END
        $$;
    """)

    # Re-create the per-butler entities table (matching mem_002 schema).
    op.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id TEXT NOT NULL,
            canonical_name VARCHAR NOT NULL,
            entity_type VARCHAR NOT NULL DEFAULT 'other',
            aliases TEXT[] NOT NULL DEFAULT '{}',
            metadata JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_entities_entity_type CHECK (
                entity_type IN ('person', 'organization', 'place', 'other')
            ),
            CONSTRAINT uq_entities_tenant_canonical_type
                UNIQUE (tenant_id, canonical_name, entity_type)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entities_tenant_canonical
        ON entities (tenant_id, canonical_name)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entities_aliases
        ON entities USING gin(aliases)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entities_metadata
        ON entities USING gin(metadata)
    """)

    # Re-add local FK.
    op.execute("""
        ALTER TABLE facts
            ADD CONSTRAINT facts_entity_id_fkey
            FOREIGN KEY (entity_id)
            REFERENCES entities(id)
            ON DELETE RESTRICT
    """)
