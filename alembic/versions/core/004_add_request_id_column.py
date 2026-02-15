"""add request_id column to sessions table

Revision ID: core_004
Revises: core_003
Create Date: 2026-02-16 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_004"
down_revision = "core_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add request_id column (nullable TEXT)
    op.execute("""
        ALTER TABLE sessions
        ADD COLUMN IF NOT EXISTS request_id TEXT
    """)

    # Create index on request_id for filtering
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_request_id
        ON sessions (request_id)
    """)


def downgrade() -> None:
    # Drop index first
    op.execute("""
        DROP INDEX IF EXISTS idx_sessions_request_id
    """)

    # Drop column
    op.execute("""
        ALTER TABLE sessions
        DROP COLUMN IF EXISTS request_id
    """)
