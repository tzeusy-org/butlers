"""Real-Postgres round trip for the conversation identity split migration."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = pytest.mark.integration


def test_telegram_anchor_collapse_and_downgrade_restore(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    config = _build_alembic_config(db_url, chains=["core"])
    command.upgrade(config, "core@core_208")

    engine = create_engine(db_url)
    anchor_ids = [uuid.uuid4() for _ in range(5)]
    message_ids: list[uuid.UUID] = []
    telegram_user_anchor = uuid.uuid4()
    whatsapp_anchor = uuid.uuid4()
    with engine.begin() as conn:
        for index, anchor_id in enumerate(anchor_ids):
            conn.execute(
                text(
                    """
                    INSERT INTO public.dashboard_conversations (
                        id, butler_name, title, source_channel,
                        source_thread_identity, provider_session_id,
                        provider_runtime_type, provider_session_updated_at,
                        created_at, updated_at
                    ) VALUES (
                        :id, 'general', :title, 'telegram_bot', :thread,
                        :handle, 'claude', now() + (:offset * interval '1 minute'),
                        now() + (:offset * interval '1 minute'),
                        now() + (:offset * interval '1 minute')
                    )
                    """
                ),
                {
                    "id": anchor_id,
                    "title": f"ghost-{index}",
                    "thread": f"-100123:10{index}",
                    "handle": f"provider-{index}",
                    "offset": index,
                },
            )
            if index < 2:
                message_id = uuid.uuid4()
                message_ids.append(message_id)
                conn.execute(
                    text(
                        """
                        INSERT INTO public.dashboard_messages (
                            id, conversation_id, role, content
                        ) VALUES (:id, :conversation_id, 'user', :content)
                        """
                    ),
                    {
                        "id": message_id,
                        "conversation_id": anchor_id,
                        "content": f"message-{index}",
                    },
                )
                conn.execute(
                    text(
                        """
                        INSERT INTO public.dashboard_conversation_turns (
                            message_id, conversation_id, ingress_state, cancel_requested_at
                        ) VALUES (
                            :message_id, :conversation_id, 'accepted',
                            CASE WHEN :cancelled THEN now() ELSE NULL END
                        )
                        """
                    ),
                    {
                        "message_id": message_id,
                        "conversation_id": anchor_id,
                        "cancelled": index == 1,
                    },
                )
        conn.execute(
            text(
                """
                INSERT INTO public.dashboard_conversations (
                    id, butler_name, title, source_channel, source_thread_identity
                ) VALUES
                    (:telegram_id, 'general', 'telegram user',
                     'telegram_user_client', '998877'),
                    (:whatsapp_id, 'general', 'whatsapp user',
                     'whatsapp_user_client', '6591234567@s.whatsapp.net')
                """
            ),
            {"telegram_id": telegram_user_anchor, "whatsapp_id": whatsapp_anchor},
        )

    command.upgrade(config, "core@core_209")

    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """
                SELECT id, external_conversation_id, provider_session_id, message_count
                FROM public.dashboard_conversations
                WHERE butler_name = 'general' AND source_channel = 'telegram_bot'
                """
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        assert rows[0]["external_conversation_id"] == "telegram:-100123"
        assert rows[0]["provider_session_id"] == "provider-4"
        assert rows[0]["message_count"] == 2
        turn_rows = (
            conn.execute(
                text(
                    """
                SELECT message_id, conversation_id, ingress_state,
                       cancel_requested_at IS NOT NULL AS cancelled
                FROM public.dashboard_conversation_turns
                WHERE message_id = ANY(:message_ids)
                ORDER BY message_id
                """
                ),
                {"message_ids": message_ids},
            )
            .mappings()
            .all()
        )
        assert len(turn_rows) == 2
        assert {row["conversation_id"] for row in turn_rows} == {anchor_ids[-1]}
        assert {row["ingress_state"] for row in turn_rows} == {"accepted"}
        assert {row["cancelled"] for row in turn_rows} == {False, True}

        prefixed = dict(
            conn.execute(
                text(
                    """
                    SELECT id, external_conversation_id
                    FROM public.dashboard_conversations
                    WHERE id IN (:telegram_id, :whatsapp_id)
                    """
                ),
                {"telegram_id": telegram_user_anchor, "whatsapp_id": whatsapp_anchor},
            ).all()
        )
        assert prefixed[telegram_user_anchor] == "telegram:998877"
        assert prefixed[whatsapp_anchor] == "whatsapp:6591234567@s.whatsapp.net"

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE public.dashboard_conversations
                SET title = 'changed after upgrade',
                    provider_session_id = 'provider-after-upgrade'
                WHERE id = :survivor_id
                """
            ),
            {"survivor_id": anchor_ids[-1]},
        )
        mixed_version_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO public.dashboard_conversations (
                    id, butler_name, title, source_channel, source_thread_identity
                ) VALUES (
                    :id, 'general', 'old writer', 'telegram_bot', '-100456:88'
                )
                """
            ),
            {"id": mixed_version_id},
        )
        assert (
            conn.execute(
                text(
                    """
                SELECT external_conversation_id
                FROM public.dashboard_conversations WHERE id = :id
                """
                ),
                {"id": mixed_version_id},
            ).scalar_one()
            == "telegram:-100456"
        )
        old_writer_duplicate_id = uuid.uuid4()
        conn.execute(
            text(
                """
                INSERT INTO public.dashboard_conversations (
                    id, butler_name, title, source_channel, source_thread_identity
                ) VALUES (
                    :id, 'general', 'old writer duplicate',
                    'telegram_bot', '-100123:999'
                )
                ON CONFLICT (butler_name, source_channel, source_thread_identity)
                    WHERE source_thread_identity IS NOT NULL
                DO NOTHING
                """
            ),
            {"id": old_writer_duplicate_id},
        )
        canonical_rows = conn.execute(
            text(
                """
                SELECT id, source_thread_identity, external_conversation_id
                FROM public.dashboard_conversations
                WHERE butler_name = 'general'
                  AND source_channel = 'telegram_bot'
                  AND external_conversation_id = 'telegram:-100123'
                """
            )
        ).all()
        assert canonical_rows == [(anchor_ids[-1], "telegram:-100123", "telegram:-100123")]
        trigger_security = conn.execute(
            text(
                """
                SELECT procedure.prosecdef,
                       procedure.proconfig,
                       procedure.proowner = conversations.relowner AS owner_aligned
                FROM pg_proc AS procedure
                JOIN pg_class AS conversations
                  ON conversations.oid = 'public.dashboard_conversations'::regclass
                WHERE procedure.oid =
                    'public.core_209_fill_external_conversation_id()'::regprocedure
                """
            )
        ).one()
        assert trigger_security[0] is True
        assert trigger_security[1] == ["search_path=pg_catalog, public"]
        assert trigger_security[2] is True

    command.downgrade(config, "core@core_208")

    with engine.connect() as conn:
        restored = conn.execute(
            text(
                """
                SELECT source_thread_identity, provider_session_id
                FROM public.dashboard_conversations
                WHERE id = ANY(:ids)
                ORDER BY source_thread_identity
                """
            ),
            {"ids": anchor_ids},
        ).all()
        assert len(restored) == 5
        assert restored[-1] == ("-100123:104", "provider-after-upgrade")
        survivor = conn.execute(
            text(
                """
                SELECT title, provider_session_id
                FROM public.dashboard_conversations WHERE id = :id
                """
            ),
            {"id": anchor_ids[-1]},
        ).one()
        assert survivor == ("changed after upgrade", "provider-after-upgrade")
        restored_turns = conn.execute(
            text(
                """
                SELECT message_id, conversation_id
                FROM public.dashboard_conversation_turns
                WHERE message_id = ANY(:message_ids)
                """
            ),
            {"message_ids": message_ids},
        ).all()
        assert dict(restored_turns) == dict(zip(message_ids, anchor_ids[:2], strict=True))
        restored_prefixes = dict(
            conn.execute(
                text(
                    """
                    SELECT id, source_thread_identity
                    FROM public.dashboard_conversations
                    WHERE id IN (:telegram_id, :whatsapp_id)
                    """
                ),
                {"telegram_id": telegram_user_anchor, "whatsapp_id": whatsapp_anchor},
            ).all()
        )
        assert restored_prefixes == {
            telegram_user_anchor: "998877",
            whatsapp_anchor: "6591234567@s.whatsapp.net",
        }
        backup_tables = (
            conn.execute(
                text(
                    """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename LIKE 'core_209_%_backup'
                """
                )
            )
            .scalars()
            .all()
        )
        assert backup_tables == []

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE public.dashboard_conversations
                SET source_thread_identity = CASE id
                    WHEN :telegram_id THEN '112233'
                    WHEN :whatsapp_id THEN '6597654321@s.whatsapp.net'
                END
                WHERE id IN (:telegram_id, :whatsapp_id)
                """
            ),
            {"telegram_id": telegram_user_anchor, "whatsapp_id": whatsapp_anchor},
        )
        conn.execute(
            text(
                """
                UPDATE public.dashboard_messages
                SET conversation_id = :conversation_id
                WHERE id = :message_id
                """
            ),
            {"conversation_id": anchor_ids[2], "message_id": message_ids[0]},
        )
        conn.execute(
            text(
                """
                UPDATE public.dashboard_conversation_turns
                SET conversation_id = :conversation_id
                WHERE message_id = :message_id
                """
            ),
            {"conversation_id": anchor_ids[2], "message_id": message_ids[0]},
        )

    command.upgrade(config, "core@core_209")
    command.downgrade(config, "core@core_208")

    with engine.connect() as conn:
        second_cycle_prefixes = dict(
            conn.execute(
                text(
                    """
                    SELECT id, source_thread_identity
                    FROM public.dashboard_conversations
                    WHERE id IN (:telegram_id, :whatsapp_id)
                    """
                ),
                {"telegram_id": telegram_user_anchor, "whatsapp_id": whatsapp_anchor},
            ).all()
        )
        assert second_cycle_prefixes == {
            telegram_user_anchor: "112233",
            whatsapp_anchor: "6597654321@s.whatsapp.net",
        }
        second_cycle_messages = dict(
            conn.execute(
                text(
                    """
                    SELECT id, conversation_id
                    FROM public.dashboard_messages
                    WHERE id = ANY(:message_ids)
                    """
                ),
                {"message_ids": message_ids},
            ).all()
        )
        assert second_cycle_messages[message_ids[0]] == anchor_ids[2]
        second_cycle_turn = conn.execute(
            text(
                """
                SELECT conversation_id
                FROM public.dashboard_conversation_turns
                WHERE message_id = :message_id
                """
            ),
            {"message_id": message_ids[0]},
        ).scalar_one()
        assert second_cycle_turn == anchor_ids[2]
        remaining_backups = conn.execute(
            text(
                """
                SELECT count(*) FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename LIKE 'core_209_%_backup'
                """
            )
        ).scalar_one()
        assert remaining_backups == 0
    engine.dispose()
