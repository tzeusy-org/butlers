"""memory_baseline

Revision ID: mem_001
Revises:
Create Date: 2026-02-20 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_001"
down_revision = None
branch_labels = ("memory",)
depends_on = None


def upgrade() -> None:
    # Ensure required extensions for vectors and UUID generation are available.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    op.execute("""
        CREATE TABLE IF NOT EXISTS episodes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            butler TEXT NOT NULL,
            session_id UUID,
            content TEXT NOT NULL,
            embedding vector(384),
            search_vector tsvector,
            importance FLOAT NOT NULL DEFAULT 5.0,
            reference_count INTEGER NOT NULL DEFAULT 0,
            consolidated BOOLEAN NOT NULL DEFAULT false,
            consolidation_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_referenced_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ DEFAULT now() + interval '7 days',
            metadata JSONB DEFAULT '{}'::jsonb
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_butler_created
        ON episodes (butler, created_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_expires
        ON episodes (expires_at) WHERE expires_at IS NOT NULL
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_unconsolidated
        ON episodes (butler, created_at) WHERE consolidation_status = 'pending'
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_search
        ON episodes USING gin(search_vector)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_embedding
        ON episodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)

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

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_scope_validity
        ON facts (scope, validity) WHERE validity = 'active'
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate
        ON facts (subject, predicate)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_search
        ON facts USING gin(search_vector)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_tags
        ON facts USING gin(tags)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_embedding
        ON facts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            content TEXT NOT NULL,
            embedding vector(384),
            search_vector tsvector,
            scope TEXT NOT NULL DEFAULT 'global',
            maturity TEXT NOT NULL DEFAULT 'candidate',
            confidence FLOAT NOT NULL DEFAULT 0.5,
            decay_rate FLOAT NOT NULL DEFAULT 0.008,
            permanence TEXT NOT NULL DEFAULT 'standard',
            effectiveness_score FLOAT NOT NULL DEFAULT 0.0,
            applied_count INTEGER NOT NULL DEFAULT 0,
            success_count INTEGER NOT NULL DEFAULT 0,
            harmful_count INTEGER NOT NULL DEFAULT 0,
            source_episode_id UUID REFERENCES episodes(id) ON DELETE SET NULL,
            source_butler TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_applied_at TIMESTAMPTZ,
            last_evaluated_at TIMESTAMPTZ,
            last_confirmed_at TIMESTAMPTZ,
            reference_count INTEGER NOT NULL DEFAULT 0,
            last_referenced_at TIMESTAMPTZ,
            tags JSONB DEFAULT '[]'::jsonb,
            metadata JSONB DEFAULT '{}'::jsonb
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_scope_maturity
        ON rules (scope, maturity)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_search
        ON rules USING gin(search_vector)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_embedding
        ON rules USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_links (
            source_type TEXT NOT NULL,
            source_id UUID NOT NULL,
            target_type TEXT NOT NULL,
            target_id UUID NOT NULL,
            relation TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_type, source_id, target_type, target_id),
            CONSTRAINT chk_memory_links_relation CHECK (
                relation IN (
                    'derived_from',
                    'supports',
                    'contradicts',
                    'supersedes',
                    'related_to'
                )
            )
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_links_target
        ON memory_links (target_type, target_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_links CASCADE")
    op.execute("DROP TABLE IF EXISTS rules CASCADE")
    op.execute("DROP TABLE IF EXISTS facts CASCADE")
    op.execute("DROP TABLE IF EXISTS episodes CASCADE")
