"""add route_inbox table for async route dispatch (Section 4.4)

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
    # Create route_inbox table for accept-then-process async dispatch
    op.execute("""
        CREATE TABLE IF NOT EXISTS route_inbox (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            route_envelope JSONB NOT NULL,
            lifecycle_state TEXT NOT NULL DEFAULT 'accepted',
            processed_at TIMESTAMPTZ,
            session_id UUID,
            error TEXT
        )
    """)

    # Index for querying unprocessed rows (crash recovery scanner)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_route_inbox_lifecycle_state
        ON route_inbox (lifecycle_state, received_at)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_route_inbox_lifecycle_state")
    op.execute("DROP TABLE IF EXISTS route_inbox")
