"""Add agent_type column to butler_registry for staffer routing exclusion.

Revision ID: sw_005
Revises: sw_004
Create Date: 2026-04-04 00:00:00.000000

Adds an ``agent_type`` column to ``butler_registry`` so the switchboard can
distinguish butler-typed agents (eligible for user-message routing) from
staffer-typed agents (excluded from user-message routing but reachable for
butler-to-staffer routing).

Default is ``'butler'`` for backward compatibility with all existing rows.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "sw_005"
down_revision = "sw_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE butler_registry
        ADD COLUMN IF NOT EXISTS agent_type TEXT NOT NULL DEFAULT 'butler'
        """
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'ck_butler_registry_agent_type'
            ) THEN
                ALTER TABLE butler_registry
                ADD CONSTRAINT ck_butler_registry_agent_type
                CHECK (agent_type IN ('butler', 'staffer'));
            END IF;
        END
        $$
        """
    )

    op.execute(
        """
        COMMENT ON COLUMN butler_registry.agent_type IS
        'Agent type: butler (eligible for user-message routing) or '
        'staffer (excluded from user-message routing, reachable via notify()).'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE butler_registry DROP COLUMN IF EXISTS agent_type")
