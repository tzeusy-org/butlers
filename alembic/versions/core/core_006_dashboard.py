"""dashboard: create public.dashboard_conversations and public.dashboard_messages

Revision ID: core_006
Revises: core_005
Create Date: 2026-03-26 00:00:00.000000

Collapsed from: core_039_dashboard_conversations

Creates two tables in the public schema to support dashboard conversational input:

  - public.dashboard_conversations — one row per chat session between a user and a butler.
    Tracks status, aggregate token/duration stats, and message count.

  - public.dashboard_messages — individual user/assistant message rows linked to a
    conversation via FK CASCADE.  Stores model metadata, token counts, tool calls (JSONB),
    and optional error/request lineage fields.

Indexes:
  - conversations: composite (butler_name, status, updated_at DESC) and
    (butler_name, updated_at DESC) for filtered list queries.
  - messages: composite (conversation_id, created_at ASC) for ordered message retrieval.

Grants SELECT, INSERT, UPDATE, DELETE on both tables to all butler roles.
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "core_006"
down_revision = "core_005"
branch_labels = None
depends_on = None

# All butler roles that need access to dashboard tables.
_ALL_BUTLER_ROLES = (
    "butler_education_rw",
    "butler_finance_rw",
    "butler_general_rw",
    "butler_health_rw",
    "butler_home_rw",
    "butler_messenger_rw",
    "butler_relationship_rw",
    "butler_switchboard_rw",
    "butler_travel_rw",
)

_TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _grant_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """GRANT privilege ON table TO role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'GRANT {privilege} ON TABLE {table_fqn} TO {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def _revoke_if_table_exists(table_fqn: str, privilege: str, role: str) -> None:
    """REVOKE privilege ON table FROM role only when table and role exist."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{table_fqn}') IS NOT NULL
               AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {_quote_literal(role)})
            THEN
                EXECUTE 'REVOKE {privilege} ON TABLE {table_fqn} FROM {_quote_ident(role)}';
            END IF;
        EXCEPTION
            WHEN insufficient_privilege THEN NULL;
            WHEN undefined_object THEN NULL;
            WHEN undefined_table THEN NULL;
            WHEN invalid_schema_name THEN NULL;
        END
        $$;
        """
    )


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Create public.dashboard_conversations.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.dashboard_conversations (
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
    # 2. Create composite indexes on public.dashboard_conversations.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dashboard_conversations_butler_status_updated
            ON public.dashboard_conversations (butler_name, status, updated_at DESC)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dashboard_conversations_butler_updated
            ON public.dashboard_conversations (butler_name, updated_at DESC)
    """)

    # -------------------------------------------------------------------------
    # 3. Create public.dashboard_messages.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS public.dashboard_messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES public.dashboard_conversations(id)
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
    # 4. Create composite index on public.dashboard_messages.
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_dashboard_messages_conversation_created
            ON public.dashboard_messages (conversation_id, created_at ASC)
    """)

    # -------------------------------------------------------------------------
    # 5. Grant access to all butler roles.
    # -------------------------------------------------------------------------
    for role in _ALL_BUTLER_ROLES:
        _grant_if_table_exists("public.dashboard_conversations", _TABLE_PRIVILEGES, role)
        _grant_if_table_exists("public.dashboard_messages", _TABLE_PRIVILEGES, role)


def downgrade() -> None:
    # Revoke privileges from butler roles.
    for role in _ALL_BUTLER_ROLES:
        _revoke_if_table_exists("public.dashboard_messages", _TABLE_PRIVILEGES, role)
        _revoke_if_table_exists("public.dashboard_conversations", _TABLE_PRIVILEGES, role)

    # Drop indexes and tables.
    op.execute("DROP INDEX IF EXISTS public.idx_dashboard_messages_conversation_created")
    op.execute("DROP TABLE IF EXISTS public.dashboard_messages")
    op.execute("DROP INDEX IF EXISTS public.idx_dashboard_conversations_butler_updated")
    op.execute("DROP INDEX IF EXISTS public.idx_dashboard_conversations_butler_status_updated")
    op.execute("DROP TABLE IF EXISTS public.dashboard_conversations")
