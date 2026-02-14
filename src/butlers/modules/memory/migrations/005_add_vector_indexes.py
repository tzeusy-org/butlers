"""add_vector_indexes

Revision ID: mem_005
Revises: mem_004
Create Date: 2026-02-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_005"
down_revision = "mem_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure extensions are available (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # IVFFlat indexes for approximate nearest neighbor search (cosine distance)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_embedding
        ON episodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_facts_embedding
        ON facts USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rules_embedding
        ON rules USING ivfflat (embedding vector_cosine_ops) WITH (lists = 20)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_rules_embedding")
    op.execute("DROP INDEX IF EXISTS idx_facts_embedding")
    op.execute("DROP INDEX IF EXISTS idx_episodes_embedding")
