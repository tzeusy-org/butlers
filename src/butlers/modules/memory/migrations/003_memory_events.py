"""memory_events

Adds the append-only audit stream table for memory mutations and lifecycle transitions.

Revision ID: mem_003
Revises: mem_002
Create Date: 2026-02-24 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mem_003"
down_revision = "mem_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            event_type TEXT NOT NULL,
            actor TEXT,
            tenant_id TEXT,
            payload JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_events_event_type_created
        ON memory_events (event_type, created_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_events_tenant_created
        ON memory_events (tenant_id, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memory_events_tenant_created")
    op.execute("DROP INDEX IF EXISTS idx_memory_events_event_type_created")
    op.execute("DROP TABLE IF EXISTS memory_events CASCADE")
