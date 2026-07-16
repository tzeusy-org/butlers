"""Drop unwired dashboard conversation accounting aggregates.

Revision ID: core_175
Revises: core_174
Create Date: 2026-07-16 00:00:00.000000

Conversation replies persist before their routed sessions have final model,
token, or duration accounting. The aggregate columns on
``dashboard_conversations`` therefore never received authoritative values.
Per-message accounting remains in ``dashboard_messages``; this migration
removes the unwritable conversation-level aggregates.
"""

from __future__ import annotations

from alembic import op

revision = "core_175"
down_revision = "core_174"
branch_labels = None
depends_on = None

_DEAD_COLUMNS = """
    DROP COLUMN IF EXISTS total_input_tokens,
    DROP COLUMN IF EXISTS total_output_tokens,
    DROP COLUMN IF EXISTS total_duration_ms
"""


def upgrade() -> None:
    op.execute(
        f"""
        ALTER TABLE public.dashboard_conversations
        {_DEAD_COLUMNS}
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            ADD COLUMN IF NOT EXISTS total_input_tokens BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS total_output_tokens BIGINT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS total_duration_ms BIGINT NOT NULL DEFAULT 0
        """
    )
