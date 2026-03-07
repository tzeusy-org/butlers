"""drop_meals_table

Revision ID: health_002
Revises: health_001
Create Date: 2026-03-07 00:00:00.000000

The meals table was superseded by temporal facts (meal_* predicates in the
memory module's facts table). All meal tooling now reads/writes facts directly.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "health_002"
down_revision = "health_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_meals_eaten_at")
    op.execute("DROP TABLE IF EXISTS meals")


def downgrade() -> None:
    # Historical meal data lived in facts; restoring the empty table is not
    # worth the complexity and would not recover any data.
    pass
