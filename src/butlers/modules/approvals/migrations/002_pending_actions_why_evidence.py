"""pending_actions dossier fields for module-created approvals tables.

Revision ID: approvals_002
Revises: approvals_001
Create Date: 2026-05-25 00:00:00.000000

The core migration ``core_097`` added ``why`` and ``evidence`` to every
``pending_actions`` table that existed at the time it ran.  Core migrations run
before module migrations on daemon startup, so schemas that first create the
approvals module table afterward still need the module chain to own these
columns directly.
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
    """No-op: approvals_001 now owns these columns for fresh installs."""
