"""dashboard_conversations: create shared.dashboard_conversations and shared.dashboard_messages

Revision ID: core_039
Revises: core_038
Create Date: 2026-03-25 00:00:00.000000

Creates two tables in the shared schema to support dashboard conversational input:

  - shared.dashboard_conversations — one row per chat session between a user and a butler.
    Tracks status, aggregate token/duration stats, and message count.

  - shared.dashboard_messages — individual user/assistant message rows linked to a
    conversation via FK CASCADE.  Stores model metadata, token counts, tool calls (JSONB),
    and optional error/request lineage fields.

Indexes:
  - conversations: composite (butler_name, status, updated_at DESC) and
    (butler_name, updated_at DESC) for filtered list queries.
  - messages: composite (conversation_id, created_at ASC) for ordered message retrieval.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_039"
down_revision = "core_038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Ensure the shared schema exists (idempotent guard).
    # -------------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS shared")

    # -------------------------------------------------------------------------
    # 2. Create shared.dashboard_conversations.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.dashboard_conversations (
            id UUID PRIMARY KEY,
            butler_name TEXT NOT NULL,
            title TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            message_count INTEGER NOT NULL DEFAULT 0,
            total_input_tokens BIGINT NOT NULL DEFAULT 0,
            total_output_tokens BIGINT NOT NULL DEFAULT 0,
            total_duration_ms BIGINT NOT NULL DEFAULT 0
        )
    """)

    # -------------------------------------------------------------------------
    # 3. Create composite indexes on shared.dashboard_conversations.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dashboard_conversations_butler_status_updated
            ON shared.dashboard_conversations (butler_name, status, updated_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dashboard_conversations_butler_updated
            ON shared.dashboard_conversations (butler_name, updated_at DESC)
    """)

    # -------------------------------------------------------------------------
    # 4. Create shared.dashboard_messages.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS shared.dashboard_messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES shared.dashboard_conversations(id)
                ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            session_id UUID,
            model_name TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            duration_ms INTEGER,
            tool_calls JSONB,
            error TEXT,
            request_id UUID
        )
    """)

    # -------------------------------------------------------------------------
    # 5. Create composite index on shared.dashboard_messages.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dashboard_messages_conversation_created
            ON shared.dashboard_messages (conversation_id, created_at ASC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS shared.idx_dashboard_messages_conversation_created")
    op.execute("DROP TABLE IF EXISTS shared.dashboard_messages")
    op.execute("DROP INDEX IF EXISTS shared.idx_dashboard_conversations_butler_updated")
    op.execute("DROP INDEX IF EXISTS shared.idx_dashboard_conversations_butler_status_updated")
    op.execute("DROP TABLE IF EXISTS shared.dashboard_conversations")
