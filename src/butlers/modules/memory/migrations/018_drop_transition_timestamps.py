"""Dedicated drop transition times; legacy timestamps remain unknown.

Revision ID: mem_018
Revises: mem_017
"""

from alembic import op

revision = "mem_018"
down_revision = "mem_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE facts ADD COLUMN dropped_at TIMESTAMPTZ")
    op.execute("ALTER TABLE rules ADD COLUMN forgotten_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE rules DROP COLUMN forgotten_at")
    op.execute("ALTER TABLE facts DROP COLUMN dropped_at")
