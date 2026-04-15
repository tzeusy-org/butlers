"""Normalize WhatsApp filtered-event connector_type to runtime identity.

Revision ID: core_075
Revises: core_074
Create Date: 2026-04-15 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

revision = "core_075"
down_revision = "core_074"
branch_labels = None
depends_on = None


def _execute_best_effort(statement: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE {statement!r};
        EXCEPTION
            WHEN undefined_table THEN NULL;
            WHEN undefined_column THEN NULL;
            WHEN insufficient_privilege THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    _execute_best_effort(
        """
        UPDATE connectors.filtered_events
        SET connector_type = 'whatsapp_user_client'
        WHERE connector_type = 'whatsapp'
          AND source_channel = 'whatsapp_user_client'
        """
    )


def downgrade() -> None:
    _execute_best_effort(
        """
        UPDATE connectors.filtered_events
        SET connector_type = 'whatsapp'
        WHERE connector_type = 'whatsapp_user_client'
          AND source_channel = 'whatsapp_user_client'
        """
    )
