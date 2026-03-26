"""lifestyle_tables

Revision ID: lifestyle_001
Revises:
Create Date: 2026-03-26 00:00:00.000000

The lifestyle butler has no custom domain tables for v1.
The schema itself is created by the Alembic env.py (CREATE SCHEMA IF NOT EXISTS)
when migrations are run with ``butlers.target_schema=lifestyle``.

"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "lifestyle_001"
down_revision = None
branch_labels = ("lifestyle",)
depends_on = None


def upgrade() -> None:
    # No domain tables in v1 — schema is created by env.py.
    pass


def downgrade() -> None:
    pass
