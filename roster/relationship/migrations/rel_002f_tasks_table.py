"""tasks_table

Revision ID: rel_002f
Revises: rel_002e
Create Date: 2025-01-01 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_002f"
down_revision = "rel_002e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            title VARCHAR NOT NULL,
            description TEXT,
            completed BOOLEAN DEFAULT false,
            completed_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_contact_id ON tasks (contact_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tasks_completed ON tasks (completed)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tasks")
