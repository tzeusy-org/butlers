"""Split durable conversation identity from per-message reply targeting.

Revision ID: core_209
Revises: core_208
Create Date: 2026-09-03 00:00:00.000000

Existing Telegram bot anchors used ``chat_id:message_id`` as their unique key,
which created one zero-message conversation per inbound message. The upgrade
backs those rows up, collapses each chat to the row carrying the newest
provider handle, re-links any dashboard messages, and installs the stable
conversation-key index. The backup remains until downgrade so the operation is
reversible rather than merely schema-reversible.
"""

from __future__ import annotations

from alembic import op

revision = "core_209"
down_revision = "core_208"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            ADD COLUMN IF NOT EXISTS external_conversation_id TEXT NULL
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.core_209_telegram_anchor_backup (
            id UUID PRIMARY KEY,
            butler_name TEXT NOT NULL,
            title TEXT,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            message_count INTEGER NOT NULL,
            routed_butler TEXT,
            source_channel TEXT NOT NULL,
            source_thread_identity TEXT,
            provider_session_id TEXT,
            provider_runtime_type TEXT,
            provider_session_updated_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.core_209_telegram_message_backup (
            message_id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
        )
        """
    )
    op.execute(
        """
        INSERT INTO public.core_209_telegram_anchor_backup (
            id, butler_name, title, status, created_at, updated_at,
            message_count, routed_butler, source_channel, source_thread_identity,
            provider_session_id, provider_runtime_type, provider_session_updated_at
        )
        SELECT id, butler_name, title, status, created_at, updated_at,
               message_count, routed_butler, source_channel, source_thread_identity,
               provider_session_id, provider_runtime_type, provider_session_updated_at
        FROM public.dashboard_conversations
        WHERE source_channel IN ('telegram', 'telegram_bot')
          AND source_thread_identity ~ '^-?[0-9]+:[0-9]+$'
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO public.core_209_telegram_message_backup (message_id, conversation_id)
        SELECT m.id, m.conversation_id
        FROM public.dashboard_messages AS m
        JOIN public.core_209_telegram_anchor_backup AS b
          ON b.id = m.conversation_id
        ON CONFLICT (message_id) DO NOTHING
        """
    )
    op.execute(
        """
        WITH candidates AS (
            SELECT c.id,
                   'telegram:' || split_part(c.source_thread_identity, ':', 1)
                       AS stable_id,
                   first_value(c.id) OVER (
                       PARTITION BY c.butler_name, c.source_channel,
                                    split_part(c.source_thread_identity, ':', 1)
                       ORDER BY (c.provider_session_id IS NOT NULL) DESC,
                                c.provider_session_updated_at DESC NULLS LAST,
                                c.updated_at DESC, c.created_at DESC, c.id DESC
                   ) AS survivor_id
            FROM public.dashboard_conversations AS c
            JOIN public.core_209_telegram_anchor_backup AS b ON b.id = c.id
        )
        UPDATE public.dashboard_messages AS m
        SET conversation_id = candidates.survivor_id
        FROM candidates
        WHERE m.conversation_id = candidates.id
          AND candidates.id <> candidates.survivor_id
        """
    )
    op.execute(
        """
        WITH candidates AS (
            SELECT c.id,
                   'telegram:' || split_part(c.source_thread_identity, ':', 1)
                       AS stable_id,
                   first_value(c.id) OVER (
                       PARTITION BY c.butler_name, c.source_channel,
                                    split_part(c.source_thread_identity, ':', 1)
                       ORDER BY (c.provider_session_id IS NOT NULL) DESC,
                                c.provider_session_updated_at DESC NULLS LAST,
                                c.updated_at DESC, c.created_at DESC, c.id DESC
                   ) AS survivor_id
            FROM public.dashboard_conversations AS c
            JOIN public.core_209_telegram_anchor_backup AS b ON b.id = c.id
        )
        DELETE FROM public.dashboard_conversations AS c
        USING candidates
        WHERE c.id = candidates.id AND candidates.id <> candidates.survivor_id
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_conversations AS c
        SET external_conversation_id = 'telegram:' || split_part(
                c.source_thread_identity, ':', 1
            ),
            source_thread_identity = 'telegram:' || split_part(
                c.source_thread_identity, ':', 1
            ),
            message_count = (
                SELECT count(*) FROM public.dashboard_messages AS m
                WHERE m.conversation_id = c.id
            )
        WHERE c.id IN (SELECT id FROM public.core_209_telegram_anchor_backup)
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_conversations
        SET external_conversation_id = source_thread_identity
        WHERE external_conversation_id IS NULL
          AND source_thread_identity IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dashboard_conversations_external_conversation
            ON public.dashboard_conversations (
                butler_name, source_channel, external_conversation_id
            )
            WHERE external_conversation_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS public.uq_dashboard_conversations_external_conversation")
    op.execute(
        """
        INSERT INTO public.dashboard_conversations (
            id, butler_name, title, status, created_at, updated_at, message_count,
            routed_butler, source_channel, source_thread_identity,
            provider_session_id, provider_runtime_type, provider_session_updated_at
        )
        SELECT id, butler_name, title, status, created_at, updated_at, message_count,
               routed_butler, source_channel, source_thread_identity,
               provider_session_id, provider_runtime_type, provider_session_updated_at
        FROM public.core_209_telegram_anchor_backup
        ON CONFLICT (id) DO UPDATE SET
            butler_name = EXCLUDED.butler_name,
            title = EXCLUDED.title,
            status = EXCLUDED.status,
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at,
            message_count = EXCLUDED.message_count,
            routed_butler = EXCLUDED.routed_butler,
            source_channel = EXCLUDED.source_channel,
            source_thread_identity = EXCLUDED.source_thread_identity,
            provider_session_id = EXCLUDED.provider_session_id,
            provider_runtime_type = EXCLUDED.provider_runtime_type,
            provider_session_updated_at = EXCLUDED.provider_session_updated_at
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_messages AS m
        SET conversation_id = b.conversation_id
        FROM public.core_209_telegram_message_backup AS b
        WHERE m.id = b.message_id
        """
    )
    op.execute("DROP TABLE IF EXISTS public.core_209_telegram_message_backup")
    op.execute("DROP TABLE IF EXISTS public.core_209_telegram_anchor_backup")
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            DROP COLUMN IF EXISTS external_conversation_id
        """
    )
