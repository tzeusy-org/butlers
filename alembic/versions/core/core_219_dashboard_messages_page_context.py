"""dashboard_messages: add page_context jsonb + captured_at for chip persistence.

Revision ID: core_219
Revises: core_217
Create Date: 2026-09-05 00:00:00.000000

bu-0ynlk.4 (page-context v2). The dashboard chat widget's removable
ContextChip needs the exact snapshot that was sent with a user message to
persist alongside it, both so ``message_list`` can render what was attached
and so a retry (``message_create_idempotent``) reuses the original stored
snapshot rather than re-capturing a possibly-different one from the retry
request body. ``page_context`` is ``NULL`` on every pre-existing row, every
assistant-role row, and any user row sent without a page context (chip
removed, or the route's ``contextPolicy`` is ``"none"``). ``captured_at`` is
``NULL`` whenever ``page_context`` is ``NULL``.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_219"
down_revision = "core_217"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE public.dashboard_messages ADD COLUMN IF NOT EXISTS page_context JSONB")
    op.execute(
        "ALTER TABLE public.dashboard_messages ADD COLUMN IF NOT EXISTS captured_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE public.dashboard_messages DROP COLUMN IF EXISTS captured_at")
    op.execute("ALTER TABLE public.dashboard_messages DROP COLUMN IF EXISTS page_context")
