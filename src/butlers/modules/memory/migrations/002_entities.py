"""memory_entities

Revision ID: mem_002
Revises: mem_001
Create Date: 2026-02-23 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_002"
down_revision = "mem_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
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

    op.execute("""
        ALTER TABLE facts
        ADD COLUMN IF NOT EXISTS entity_id UUID
            REFERENCES entities(id) ON DELETE RESTRICT
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_entity_scope_predicate_active
        ON facts (entity_id, scope, predicate)
        WHERE entity_id IS NOT NULL AND validity = 'active'
    """)

    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_no_entity_subject_predicate_active
        ON facts (scope, subject, predicate)
        WHERE entity_id IS NULL AND validity = 'active'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_facts_no_entity_subject_predicate_active")
    op.execute("DROP INDEX IF EXISTS idx_facts_entity_scope_predicate_active")
    op.execute("ALTER TABLE facts DROP COLUMN IF EXISTS entity_id")
    op.execute("DROP TABLE IF EXISTS entities CASCADE")
