"""create_facts

Revision ID: mem_002
Revises: mem_001
Create Date: 2026-02-10 00:00:00.000000

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
        CREATE TABLE IF NOT EXISTS facts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            content TEXT NOT NULL,
            embedding vector(384),
            search_vector tsvector,
            importance FLOAT NOT NULL DEFAULT 5.0,
            confidence FLOAT NOT NULL DEFAULT 1.0,
            decay_rate FLOAT NOT NULL DEFAULT 0.008,
            permanence TEXT NOT NULL DEFAULT 'standard',
            source_butler TEXT,
            source_episode_id UUID REFERENCES episodes(id) ON DELETE SET NULL,
            supersedes_id UUID REFERENCES facts(id) ON DELETE SET NULL,
            validity TEXT NOT NULL DEFAULT 'active',
            scope TEXT NOT NULL DEFAULT 'global',
            reference_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_referenced_at TIMESTAMPTZ,
            last_confirmed_at TIMESTAMPTZ,
            tags JSONB DEFAULT '[]'::jsonb,
            metadata JSONB DEFAULT '{}'::jsonb
        )
    """)

    # Partial index on scope + validity for active facts
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_scope_validity
            ON facts (scope, validity)
            WHERE validity = 'active'
    """)

    # Composite index on subject + predicate for lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
            ON facts (subject, predicate)
    """)

    # GIN index on search_vector for full-text search
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_search
            ON facts USING gin(search_vector)
    """)

    # GIN index on tags for JSONB containment queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_tags
            ON facts USING gin(tags)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS facts CASCADE")
