"""groups_add_type

Add a ``type`` column to the ``groups`` table so that group-type-based
salience scoring in contact_resolve works correctly.

Revision ID: rel_006
Revises: rel_005
Create Date: 2026-04-06 00:00:00.000000

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_006"
down_revision = "rel_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE groups ADD COLUMN IF NOT EXISTS type TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS type")
