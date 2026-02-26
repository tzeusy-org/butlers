"""Create routing_instructions table for owner-defined routing directives.

Revision ID: sw_024
Revises: sw_023
Create Date: 2026-02-26 00:00:00.000000

Stores free-text routing instructions from the owner that get injected into
the switchboard butler's system prompt at runtime.  Instructions are sorted
by priority (ascending) to give stable token-cache-friendly ordering.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_024"
down_revision = "sw_023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS routing_instructions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instruction TEXT NOT NULL,
            priority INT NOT NULL DEFAULT 100,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT NOT NULL DEFAULT 'dashboard',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_routing_instructions_active
            ON routing_instructions (priority ASC, created_at ASC)
            WHERE enabled = TRUE AND deleted_at IS NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_routing_instructions_active")
    op.execute("DROP TABLE IF EXISTS routing_instructions")
