"""add_consolidation_status_and_retry_metadata

Revision ID: mem_006
Revises: mem_005
Create Date: 2026-02-16 13:30:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_006"
down_revision = "mem_005"
depends_on = None


def upgrade() -> None:
    # Add new columns with defaults
    op.execute("""
        ALTER TABLE episodes
        ADD COLUMN consolidation_status VARCHAR(20) DEFAULT 'pending',
        ADD COLUMN retry_count INTEGER DEFAULT 0,
        ADD COLUMN last_error TEXT
    """)

    # Backfill consolidation_status based on existing consolidated boolean
    op.execute("""
        UPDATE episodes
        SET consolidation_status = CASE
            WHEN consolidated = true THEN 'consolidated'
            ELSE 'pending'
        END
    """)

    # Drop the old partial index
    op.execute("DROP INDEX IF EXISTS idx_episodes_unconsolidated")

    # Create new partial index on consolidation_status='pending'
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_unconsolidated
        ON episodes (butler, created_at) WHERE consolidation_status = 'pending'
    """)


def downgrade() -> None:
    # Drop the new partial index FIRST
    op.execute("DROP INDEX IF EXISTS idx_episodes_unconsolidated")

    # THEN recreate old partial index
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_episodes_unconsolidated
        ON episodes (butler, created_at) WHERE NOT consolidated
    """)

    # Drop new columns
    op.execute("""
        ALTER TABLE episodes
        DROP COLUMN IF EXISTS consolidation_status,
        DROP COLUMN IF EXISTS retry_count,
        DROP COLUMN IF EXISTS last_error
    """)
