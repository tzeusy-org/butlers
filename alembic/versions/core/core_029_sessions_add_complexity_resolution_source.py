"""sessions: add complexity and resolution_source columns for cost attribution.

Revision ID: core_029
Revises: core_028
Create Date: 2026-03-12 00:00:00.000000

Adds two new TEXT columns to the sessions table:
- complexity: the complexity tier used to select the model (e.g. 'medium', 'high')
- resolution_source: how the model was resolved (e.g. 'catalog', 'toml_fallback')

Both columns default to their most common values so that existing rows and
sessions that do not pass explicit values remain consistent.

This migration runs once per butler schema context; the unqualified sessions
table resolves to the schema-specific table via the active search_path.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "core_029"
down_revision = "core_028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("complexity", sa.Text(), nullable=True, server_default="medium"),
    )
    op.add_column(
        "sessions",
        sa.Column("resolution_source", sa.Text(), nullable=True, server_default="toml_fallback"),
    )


def downgrade() -> None:
    op.drop_column("sessions", "resolution_source")
    op.drop_column("sessions", "complexity")
