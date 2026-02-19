"""fix_rules_missing_columns

Add reference_count, last_referenced_at, and last_confirmed_at columns
that were missing from the original 003_create_rules migration. Also fix
decay_rate default from 0.01 to 0.008 to match the spec.

Revision ID: mem_007
Revises: mem_006
Create Date: 2026-02-19 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_007"
down_revision = "mem_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add missing columns (idempotent — skip if 003 was applied fresh)
    op.execute("""
        ALTER TABLE rules
        ADD COLUMN IF NOT EXISTS reference_count INTEGER NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS last_referenced_at TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS last_confirmed_at TIMESTAMPTZ
    """)

    # Fix decay_rate default from 0.01 to 0.008
    op.execute("""
        ALTER TABLE rules
        ALTER COLUMN decay_rate SET DEFAULT 0.008
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE rules
        DROP COLUMN IF EXISTS reference_count,
        DROP COLUMN IF EXISTS last_referenced_at,
        DROP COLUMN IF EXISTS last_confirmed_at
    """)

    op.execute("""
        ALTER TABLE rules
        ALTER COLUMN decay_rate SET DEFAULT 0.01
    """)
