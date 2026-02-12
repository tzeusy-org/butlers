"""rel_004_notes_rework

Revision ID: rel_004
Revises: rel_003
Create Date: 2026-02-12 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "rel_004"
down_revision = "rel_003"
branch_labels = ("relationship",)
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS title VARCHAR")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS body TEXT")
    op.execute("UPDATE notes SET body = COALESCE(body, content)")


def downgrade() -> None:
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS body")
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS title")
