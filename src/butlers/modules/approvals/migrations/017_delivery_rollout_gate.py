"""Add the default-off approval delivery rollout gate.

Revision ID: approvals_017
Revises: approvals_016
Create Date: 2026-09-14 00:00:00.000000

The singleton row is server-held and schema-local. Writer admission and worker
startup are deliberately separate so an operator can stop new admissions
before allowing a compatible worker to drain already-admitted presentations.
"""

from __future__ import annotations

from alembic import op

revision = "approvals_017"
down_revision = "approvals_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE approval_delivery_rollout (
            singleton boolean PRIMARY KEY DEFAULT true,
            admission_enabled boolean NOT NULL DEFAULT false,
            worker_enabled boolean NOT NULL DEFAULT false,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CONSTRAINT approval_delivery_rollout_singleton_check CHECK (singleton)
        )
        """
    )
    op.execute(
        """
        INSERT INTO approval_delivery_rollout (
            singleton, admission_enabled, worker_enabled
        ) VALUES (true, false, false)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE approval_delivery_rollout")
