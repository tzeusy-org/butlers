"""Add isolated owner authentication and host-only authority transitions.

Revision ID: core_239
Revises: core_238
"""

from pathlib import Path

from alembic import op

revision = "core_239"
down_revision = "core_238"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text())


def downgrade() -> None:
    raise RuntimeError("Owner auth downgrade requires a separately reviewed host revocation plan")
