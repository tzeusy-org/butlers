"""Supply Concierge's required scheduler budget column.

Revision ID: concierge_003
Revises: concierge_002
Create Date: 2026-11-04

The historical core_050 schema list predates Concierge. Core at head therefore
left this column absent here, although startup schedule synchronization reads
it even for an empty TOML schedule list. Match the nullable INTEGER contract
already supplied to the other butlers, without changing scheduler fallbacks,
receiver authority, existing migrations or other schemas.
"""

from __future__ import annotations

from alembic import op

revision = "concierge_003"
down_revision = "concierge_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE concierge.scheduled_tasks
        ADD COLUMN IF NOT EXISTS max_token_budget INTEGER
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE concierge.scheduled_tasks
        DROP COLUMN IF EXISTS max_token_budget
    """)
