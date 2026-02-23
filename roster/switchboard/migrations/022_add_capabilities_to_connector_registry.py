"""Add capabilities column to connector_registry table.

Revision ID: sw_022
Revises: sw_021
Create Date: 2026-02-23 00:00:00.000000

Migration notes:
- Adds a JSONB `capabilities` column to connector_registry.
- NULL means no capabilities declared; non-NULL holds a JSON object
  (e.g. {"backfill": true}).
- Downgrade drops the column.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_022"
down_revision = "sw_021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE connector_registry
        ADD COLUMN IF NOT EXISTS capabilities JSONB DEFAULT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE connector_registry
        DROP COLUMN IF EXISTS capabilities
        """
    )
