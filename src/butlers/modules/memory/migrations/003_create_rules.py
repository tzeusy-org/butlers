"""create_rules

Revision ID: mem_003
Revises: mem_002
Create Date: 2026-02-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_003"
down_revision = "mem_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
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

    # Composite index on scope + maturity for filtered queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_scope_maturity
            ON rules (scope, maturity)
    """)

    # GIN index on search_vector for full-text search
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_search
            ON rules USING gin(search_vector)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rules CASCADE")
