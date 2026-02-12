"""enrich_interactions

Revision ID: rel_002a
Revises: rel_001
Create Date: 2026-02-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_002a"
down_revision = "rel_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE interactions
            ADD COLUMN IF NOT EXISTS direction VARCHAR(10),
            ADD COLUMN IF NOT EXISTS duration_minutes INTEGER,
            ADD COLUMN IF NOT EXISTS metadata JSONB
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE interactions DROP COLUMN IF EXISTS metadata")
    op.execute("ALTER TABLE interactions DROP COLUMN IF EXISTS duration_minutes")
    op.execute("ALTER TABLE interactions DROP COLUMN IF EXISTS direction")
