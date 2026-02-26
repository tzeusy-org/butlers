"""curriculum_sequence

Add sequence column to mind_map_nodes for curriculum planning ordering.
Also adds metadata column to mind_maps for goal/additional context storage.

Revision ID: education_002
Revises: education_001
Create Date: 2026-02-26 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "education_002"
down_revision = "education_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sequence column to mind_map_nodes — NULL until curriculum_generate assigns it
    op.execute("""
        ALTER TABLE education.mind_map_nodes
            ADD COLUMN IF NOT EXISTS sequence INTEGER
    """)

    # Add metadata JSONB column to mind_maps — stores goal, etc.
    op.execute("""
        ALTER TABLE education.mind_maps
            ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'
    """)

    # Index for efficient next-node queries: ORDER BY sequence ASC on unmastered nodes
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mmn_sequence
            ON education.mind_map_nodes (mind_map_id, sequence ASC NULLS LAST)
            WHERE mastery_status IN ('unseen', 'diagnosed', 'learning')
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS education.idx_mmn_sequence")
    op.execute("ALTER TABLE education.mind_map_nodes DROP COLUMN IF EXISTS sequence")
    op.execute("ALTER TABLE education.mind_maps DROP COLUMN IF EXISTS metadata")
