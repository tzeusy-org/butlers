"""add actioned_at column to mailbox table

Revision ID: mailbox_002
Revises: mailbox_001
Create Date: 2026-06-27 00:00:00.000000

The mailbox status lifecycle (see openspec/specs/module-mailbox/spec.md) sets
``actioned_at`` when a message transitions to ``actioned``. The original
``mailbox_001`` create-table omitted the column, so the module guarded the write
behind a runtime column-existence check and silently dropped the timestamp.
``mailbox_001`` now owns the column for fresh installs; this migration repairs
schemas that were already created without it.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "mailbox_002"
down_revision = "mailbox_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE mailbox
            ADD COLUMN IF NOT EXISTS actioned_at TIMESTAMPTZ
    """)


def downgrade() -> None:
    """No-op: mailbox_001 now owns this column for fresh installs."""
