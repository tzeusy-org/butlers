"""pending_actions: ensure why/evidence columns exist.

Revision ID: approvals_002
Revises: approvals_001
Create Date: 2026-05-25 00:00:00.000000

The core chain added these columns to already-existing pending_actions tables
in core_097, but daemon startup runs core migrations before module migrations.
Schemas that first enabled the approvals module after core_097 therefore
created pending_actions from approvals_001 without the dossier columns.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "approvals_002"
down_revision = "approvals_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE pending_actions
            ADD COLUMN IF NOT EXISTS why TEXT,
            ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '[]'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE pending_actions
            DROP COLUMN IF EXISTS why,
            DROP COLUMN IF EXISTS evidence
    """)
