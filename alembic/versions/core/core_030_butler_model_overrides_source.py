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

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_030"
down_revision = "core_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS because this targets a shared-schema table
    # and the migration runs once per butler — subsequent runs must be idempotent.
    op.execute(
        "ALTER TABLE shared.butler_model_overrides"
        " ADD COLUMN IF NOT EXISTS source TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE shared.butler_model_overrides"
        " DROP COLUMN IF EXISTS source"
    )
