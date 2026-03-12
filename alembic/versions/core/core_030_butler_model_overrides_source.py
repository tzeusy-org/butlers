"""butler_model_overrides: add source column for tagging override origin.

Revision ID: core_030
Revises: core_029
Create Date: 2026-03-12 00:00:00.000000

Adds a nullable TEXT column ``source`` to ``shared.butler_model_overrides``
to record the origin of each override row.  Used by the benchmark harness to
tag test overrides with ``source='e2e-benchmark'`` so that crash-left rows
are identifiable and can be cleaned up manually without touching production
overrides.

Examples:
  - ``source='e2e-benchmark'`` — inserted by the E2E benchmark harness
  - ``source='api'``          — inserted via the model settings API
  - NULL                       — legacy rows without source tracking
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_030"
down_revision = "core_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "butler_model_overrides",
        sa.Column("source", sa.Text(), nullable=True),
        schema="shared",
    )


def downgrade() -> None:
    op.drop_column("butler_model_overrides", "source", schema="shared")
