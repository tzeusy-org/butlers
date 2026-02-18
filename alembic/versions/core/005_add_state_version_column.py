"""add version column to state table for compare-and-set support

Revision ID: core_005
Revises: core_004
Create Date: 2026-02-18 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_005"
down_revision = "core_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add version column — existing rows get version=1 (backwards-compatible)
    op.execute("""
        ALTER TABLE state
        ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE state
        DROP COLUMN IF EXISTS version
    """)
