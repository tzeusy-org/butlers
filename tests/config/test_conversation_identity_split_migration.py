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
                conn.execute(
                    text(
                        """
                        INSERT INTO public.dashboard_messages (
                            id, conversation_id, role, content
                        ) VALUES (:id, :conversation_id, 'user', :content)
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "conversation_id": anchor_id,
                        "content": f"message-{index}",
                    },
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
        assert restored[-1] == ("-100123:104", "provider-4")
    engine.dispose()
