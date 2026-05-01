"""Run heartbeat partition maintenance with migration-owner privileges.

Revision ID: sw_008
Revises: sw_007
Create Date: 2026-05-01 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_008"
down_revision = "sw_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER FUNCTION switchboard_connector_heartbeat_log_ensure_partition(TIMESTAMPTZ)
        SECURITY DEFINER
        """
    )
    op.execute(
        """
        ALTER FUNCTION switchboard_connector_heartbeat_log_ensure_partition(TIMESTAMPTZ)
        SET search_path FROM CURRENT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER FUNCTION switchboard_connector_heartbeat_log_ensure_partition(TIMESTAMPTZ)
        SECURITY INVOKER
        """
    )
    op.execute(
        """
        ALTER FUNCTION switchboard_connector_heartbeat_log_ensure_partition(TIMESTAMPTZ)
        RESET search_path
        """
    )
