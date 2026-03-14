"""Add settings JSONB column to connector_registry.

Revision ID: sw_031
Revises: sw_030
Create Date: 2026-03-14 00:00:00.000000

Migration notes:
- Adds a JSONB `settings` column to connector_registry for runtime-configurable
  connector settings (e.g. discretion layer thresholds).
- NULL means no settings overrides; non-NULL holds a JSON object.
- Downgrade drops the column.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_031"
down_revision = "sw_030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE connector_registry
        ADD COLUMN IF NOT EXISTS settings JSONB DEFAULT NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE connector_registry
        DROP COLUMN IF EXISTS settings
        """
    )
