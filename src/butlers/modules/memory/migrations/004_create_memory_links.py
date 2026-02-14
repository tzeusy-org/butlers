"""create_memory_links

Revision ID: mem_004
Revises: mem_003
Create Date: 2026-02-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_004"
down_revision = "mem_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
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

    # Index on target side for reverse lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_links_target
        ON memory_links (target_type, target_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_links CASCADE")
