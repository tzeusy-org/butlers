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
    # JSON colons are not bind parameters; psycopg requires literal percent
    # signs escaped when SQLAlchemy passes its empty DBAPI parameter mapping.
    sql = Path(__file__).with_suffix(".sql").read_text()
    op.get_bind().exec_driver_sql(sql.replace("%", "%%"))


def downgrade() -> None:
    raise RuntimeError("Owner auth downgrade requires a separately reviewed host revocation plan")
