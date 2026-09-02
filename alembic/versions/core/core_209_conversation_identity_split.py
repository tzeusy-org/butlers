"""Split durable conversation identity from per-message reply targeting.

Revision ID: core_209
Revises: core_208
Create Date: 2026-09-03 00:00:00.000000

The upgrade preserves every affected anchor, message, and durable turn before
collapsing legacy per-message Telegram anchors. Persistent backups make the
data transform reversible; a compatibility trigger protects rolling deploys.
"""

from __future__ import annotations

from alembic import op

revision = "core_209"
down_revision = "core_208"
branch_labels = None
depends_on = None


def _create_backups() -> None:
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
        CREATE TABLE IF NOT EXISTS public.core_209_telegram_turn_backup (
            message_id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS public.core_209_source_identity_backup (
            conversation_id UUID PRIMARY KEY,
            source_thread_identity TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        DO $ownership$
        DECLARE
            target_owner TEXT;
            backup_table TEXT;
        BEGIN
            SELECT role.rolname INTO target_owner
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_roles AS role ON role.oid = relation.relowner
            WHERE relation.oid = 'public.dashboard_conversations'::regclass;

            FOREACH backup_table IN ARRAY ARRAY[
                'core_209_telegram_anchor_backup',
                'core_209_telegram_message_backup',
                'core_209_telegram_turn_backup',
                'core_209_source_identity_backup'
            ] LOOP
                EXECUTE format(
                    'ALTER TABLE public.%I OWNER TO %I', backup_table, target_owner
                );
            END LOOP;
        END;
        $ownership$
        """
    )


def _candidate_cte() -> str:
    return """
        WITH candidates AS (
            SELECT c.id,
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
    """


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            ADD COLUMN IF NOT EXISTS external_conversation_id TEXT NULL
        """
    )
    _create_backups()
    op.execute("TRUNCATE TABLE public.core_209_telegram_anchor_backup")
    op.execute("TRUNCATE TABLE public.core_209_telegram_message_backup")
    op.execute("TRUNCATE TABLE public.core_209_telegram_turn_backup")
    op.execute("TRUNCATE TABLE public.core_209_source_identity_backup")
    op.execute(
        """
        INSERT INTO public.core_209_source_identity_backup (
            conversation_id, source_thread_identity
        )
        SELECT id, source_thread_identity
        FROM public.dashboard_conversations
        WHERE source_channel IN ('telegram_user_client', 'whatsapp_user_client')
          AND source_thread_identity IS NOT NULL
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
        JOIN public.core_209_telegram_anchor_backup AS b ON b.id = m.conversation_id
        ON CONFLICT (message_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO public.core_209_telegram_turn_backup (message_id, conversation_id)
        SELECT t.message_id, t.conversation_id
        FROM public.dashboard_conversation_turns AS t
        JOIN public.core_209_telegram_anchor_backup AS b ON b.id = t.conversation_id
        ON CONFLICT (message_id) DO NOTHING
        """
    )
    op.execute(
        _candidate_cte()
        + """
        UPDATE public.dashboard_messages AS m
        SET conversation_id = candidates.survivor_id
        FROM candidates
        WHERE m.conversation_id = candidates.id
          AND candidates.id <> candidates.survivor_id
        """
    )
    op.execute(
        _candidate_cte()
        + """
        UPDATE public.dashboard_conversation_turns AS t
        SET conversation_id = candidates.survivor_id
        FROM candidates
        WHERE t.conversation_id = candidates.id
          AND candidates.id <> candidates.survivor_id
        """
    )
    op.execute(
        _candidate_cte()
        + """
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
        SET external_conversation_id = CASE
                WHEN source_channel = 'telegram_user_client'
                     AND source_thread_identity NOT LIKE 'telegram:%'
                    THEN 'telegram:' || source_thread_identity
                WHEN source_channel = 'whatsapp_user_client'
                     AND source_thread_identity NOT LIKE 'whatsapp:%'
                    THEN 'whatsapp:' || source_thread_identity
                ELSE source_thread_identity
            END,
            source_thread_identity = CASE
                WHEN source_channel = 'telegram_user_client'
                     AND source_thread_identity NOT LIKE 'telegram:%'
                    THEN 'telegram:' || source_thread_identity
                WHEN source_channel = 'whatsapp_user_client'
                     AND source_thread_identity NOT LIKE 'whatsapp:%'
                    THEN 'whatsapp:' || source_thread_identity
                ELSE source_thread_identity
            END
        WHERE external_conversation_id IS NULL
          AND source_thread_identity IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION public.core_209_fill_external_conversation_id()
        RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
        DECLARE
            stable_identity TEXT;
            existing_anchor_id UUID;
        BEGIN
            IF NEW.external_conversation_id IS NULL AND NEW.source_thread_identity IS NOT NULL THEN
                stable_identity := CASE
                    WHEN NEW.source_channel IN ('telegram', 'telegram_bot')
                         AND NEW.source_thread_identity ~ '^-?[0-9]+:[0-9]+$'
                        THEN 'telegram:' || split_part(NEW.source_thread_identity, ':', 1)
                    WHEN NEW.source_channel = 'telegram_user_client'
                         AND NEW.source_thread_identity NOT LIKE 'telegram:%'
                        THEN 'telegram:' || NEW.source_thread_identity
                    WHEN NEW.source_channel = 'whatsapp_user_client'
                         AND NEW.source_thread_identity NOT LIKE 'whatsapp:%'
                        THEN 'whatsapp:' || NEW.source_thread_identity
                    ELSE NEW.source_thread_identity
                END;
                IF NEW.source_channel IN (
                    'telegram', 'telegram_bot',
                    'telegram_user_client', 'whatsapp_user_client'
                ) AND stable_identity IS DISTINCT FROM NEW.source_thread_identity THEN
                    UPDATE public.dashboard_conversations
                    SET source_thread_identity = NEW.source_thread_identity
                    WHERE butler_name = NEW.butler_name
                      AND source_channel = NEW.source_channel
                      AND external_conversation_id = stable_identity
                    RETURNING id INTO existing_anchor_id;

                    IF existing_anchor_id IS NOT NULL THEN
                        NEW.external_conversation_id := stable_identity;
                        RETURN NEW;
                    END IF;

                    INSERT INTO public.core_209_source_identity_backup (
                        conversation_id, source_thread_identity
                    ) VALUES (NEW.id, NEW.source_thread_identity)
                    ON CONFLICT (conversation_id) DO NOTHING;
                    NEW.source_thread_identity := stable_identity;
                END IF;
                NEW.external_conversation_id := stable_identity;
            END IF;
            RETURN NEW;
        END;
        $function$
        """
    )
    op.execute(
        """
        DO $ownership$
        DECLARE
            target_owner TEXT;
        BEGIN
            SELECT role.rolname INTO target_owner
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_roles AS role ON role.oid = relation.relowner
            WHERE relation.oid = 'public.dashboard_conversations'::regclass;
            EXECUTE format(
                'ALTER FUNCTION public.core_209_fill_external_conversation_id() OWNER TO %I',
                target_owner
            );
        END;
        $ownership$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_core_209_conversation_identity "
        "ON public.dashboard_conversations"
    )
    op.execute(
        """
        CREATE TRIGGER trg_core_209_conversation_identity
        BEFORE INSERT OR UPDATE OF source_thread_identity, external_conversation_id
        ON public.dashboard_conversations
        FOR EACH ROW EXECUTE FUNCTION public.core_209_fill_external_conversation_id()
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
    op.execute(
        "DROP TRIGGER IF EXISTS trg_core_209_conversation_identity "
        "ON public.dashboard_conversations"
    )
    op.execute("DROP FUNCTION IF EXISTS public.core_209_fill_external_conversation_id()")
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
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_conversations AS c
        SET source_thread_identity = b.source_thread_identity
        FROM public.core_209_telegram_anchor_backup AS b
        WHERE c.id = b.id
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
    op.execute(
        """
        UPDATE public.dashboard_conversation_turns AS t
        SET conversation_id = b.conversation_id
        FROM public.core_209_telegram_turn_backup AS b
        WHERE t.message_id = b.message_id
        """
    )
    op.execute(
        """
        UPDATE public.dashboard_conversations AS c
        SET source_thread_identity = b.source_thread_identity
        FROM public.core_209_source_identity_backup AS b
        WHERE c.id = b.conversation_id
        """
    )
    op.execute(
        """
        ALTER TABLE public.dashboard_conversations
            DROP COLUMN IF EXISTS external_conversation_id
        """
    )
    op.execute("DROP TABLE IF EXISTS public.core_209_source_identity_backup")
    op.execute("DROP TABLE IF EXISTS public.core_209_telegram_turn_backup")
    op.execute("DROP TABLE IF EXISTS public.core_209_telegram_message_backup")
    op.execute("DROP TABLE IF EXISTS public.core_209_telegram_anchor_backup")
