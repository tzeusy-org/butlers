"""dashboard_messages: add sources jsonb for answer-lane citations.

Revision ID: core_213
Revises: core_212
Create Date: 2026-09-04 00:00:00.000000

bu-0ynlk.2 (question lane). The dashboard chat widget's new answer lane
requires a routed butler's ``conversation_reply`` to cite what it consulted
rather than answer from unattributed memory. ``sources`` holds that citation
list (a JSON array of strings — tool/read names or record identifiers) on the
assistant-role reply row; ``NULL`` on every pre-existing row and on any reply
outside the answer lane (confirm-loop statements, action proposals, bug-report
acknowledgements), mirroring the existing nullable ``tool_calls`` column.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_213"
down_revision = "core_212"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.dashboard_messages ADD COLUMN IF NOT EXISTS sources JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE public.dashboard_messages DROP COLUMN IF EXISTS sources")
