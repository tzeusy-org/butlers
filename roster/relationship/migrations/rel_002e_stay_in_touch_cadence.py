"""stay_in_touch_cadence

Revision ID: rel_002e
Revises: rel_002d
Create Date: 2026-02-10 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_002e"
down_revision = "rel_002d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE contacts ADD COLUMN IF NOT EXISTS stay_in_touch_days INTEGER
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE contacts DROP COLUMN IF EXISTS stay_in_touch_days
    """)
