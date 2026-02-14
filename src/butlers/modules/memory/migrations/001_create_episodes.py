"""create_episodes

Revision ID: mem_001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_001"
down_revision = None
branch_labels = ("memory",)
depends_on = None


def upgrade() -> None:
    # Enable pgvector extension for embedding storage
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

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
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_referenced_at TIMESTAMPTZ,
            expires_at TIMESTAMPTZ DEFAULT now() + interval '7 days',
            metadata JSONB DEFAULT '{}'::jsonb
        )
    """)

    # Composite index for butler-scoped time-ordered queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_butler_created
        ON episodes (butler, created_at DESC)
    """)

    # Partial index for expiration sweeps (only rows that can expire)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_expires
        ON episodes (expires_at) WHERE expires_at IS NOT NULL
    """)

    # Partial index for unconsolidated episode retrieval
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_unconsolidated
        ON episodes (butler, created_at) WHERE NOT consolidated
    """)

    # GIN index for full-text search
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_search
        ON episodes USING gin(search_vector)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS episodes CASCADE")
