"""Add the server-held memory catalog read ceiling to runtime_config.

Revision ID: core_209
Revises: core_208
Create Date: 2026-09-03 00:00:00.000000

``internal`` is an authority tier mapping to the existing ``normal`` and
``pii`` stored sensitivity values; this migration does not change the memory
sensitivity vocabulary.
"""

from __future__ import annotations

from alembic import op

revision = "core_209"
down_revision = "core_208"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runtime_config
        ADD COLUMN IF NOT EXISTS catalog_read_sensitivity text NOT NULL DEFAULT 'normal'
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        DROP CONSTRAINT IF EXISTS ck_runtime_config_catalog_read_sensitivity
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        ADD CONSTRAINT ck_runtime_config_catalog_read_sensitivity
        CHECK (catalog_read_sensitivity IN ('normal', 'internal', 'confidential'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE runtime_config DROP COLUMN IF EXISTS catalog_read_sensitivity")
