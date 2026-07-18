"""Durable reservations for deterministic approval-request pushes.

Revision ID: approvals_006
Revises: approvals_005
Create Date: 2026-07-18 00:00:00.000000

The approval gate may be reached by retried tool calls and concurrent runtime
sessions.  A one-row-per-action reservation makes outbound owner prompts
idempotent without coupling control-plane delivery to the insight broker.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_006"
down_revision = "approvals_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS approval_push_emissions (
            action_id UUID PRIMARY KEY REFERENCES pending_actions(id) ON DELETE CASCADE,
            emission_kind TEXT NOT NULL CHECK (
                emission_kind IN ('single', 'burst_digest', 'collapsed')
            ),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_approval_push_emissions_kind_created
            ON approval_push_emissions (emission_kind, created_at DESC)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS approval_push_emissions")
