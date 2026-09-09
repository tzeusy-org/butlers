"""Add the conservative tool_exposure_policy column to runtime_config.

Revision ID: core_223
Revises: core_222
Create Date: 2026-09-09 00:00:00.000000

bu-ondtw.2 (RFC 0027 Option B): every butler's ``runtime_config`` row gains a
``tool_exposure_policy`` column constrained to ``eager_filtered`` or ``auto``,
defaulting to ``eager_filtered`` so accepting or deploying this migration does
not activate native tool discovery on any existing or fresh schema. The
runtime-config accessor and dashboard API read/write this column as the hot
per-invocation exposure policy; no session planner or native-admission
behavior is introduced here.
"""

from __future__ import annotations

from alembic import op

revision = "core_223"
down_revision = "core_222"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE runtime_config
        ADD COLUMN IF NOT EXISTS tool_exposure_policy text NOT NULL DEFAULT 'eager_filtered'
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        DROP CONSTRAINT IF EXISTS ck_runtime_config_tool_exposure_policy
        """
    )
    op.execute(
        """
        ALTER TABLE runtime_config
        ADD CONSTRAINT ck_runtime_config_tool_exposure_policy
        CHECK (tool_exposure_policy IN ('eager_filtered', 'auto'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE runtime_config DROP COLUMN IF EXISTS tool_exposure_policy")
