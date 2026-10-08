"""Real-Postgres round trip for the conversation identity split (core_265)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from alembic import command
from butlers.api.conversations import conversation_get_or_create_by_thread
from butlers.migrations import _build_alembic_config
from butlers.testing.migration import create_migration_db, migration_db_name

pytestmark = pytest.mark.integration

_SCHEMAS = ("general", "health")
_BACKUP_TABLES = (
    "core_265_anchor_backup",
    "core_265_message_backup",
    "core_265_turn_backup",
)


def _config(db_url: str, schema: str):
    return _build_alembic_config(db_url, chains=["core"], target_schema=schema)


def _insert_anchor(
    conn,
    *,
    thread: str,
    channel: str = "telegram_bot",
    butler: str = "general",
    **fields: Any,
) -> uuid.UUID:
    anchor_id = fields.pop("id", None) or uuid.uuid4()
    offset = fields.pop("offset", 0)
    conn.execute(
        text(
            """
            INSERT INTO public.dashboard_conversations (
                id, butler_name, title, source_channel, source_thread_identity,
                provider_session_id, provider_runtime_type, provider_session_updated_at,
                message_count, created_at, updated_at
            ) VALUES (
                :id, :butler, :title, :channel, :thread,
                :handle, CASE WHEN :handle IS NULL THEN NULL ELSE 'claude' END,
                CASE WHEN :handle IS NULL THEN NULL
                     ELSE now() + (:offset * interval '1 minute') END,
                :message_count,
                now() + (:offset * interval '1 minute'),
                now() + (:offset * interval '1 minute')
            )
            """
        ),
        {
            "id": anchor_id,
            "butler": butler,
            "title": fields.pop("title", f"anchor {thread}"),
            "channel": channel,
            "thread": thread,
            "handle": fields.pop("handle", None),
            "message_count": fields.pop("message_count", 0),
            "offset": offset,
        },
    )
    assert not fields
    return anchor_id


def _insert_message(conn, anchor_id: uuid.UUID, *, cancelled: bool = False) -> uuid.UUID:
    message_id = uuid.uuid4()
    conn.execute(
        text(
            """
            INSERT INTO public.dashboard_messages (id, conversation_id, role, content)
            VALUES (:id, :conversation_id, 'user', 'synthetic')
            """
        ),
        {"id": message_id, "conversation_id": anchor_id},
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
        {"message_id": message_id, "conversation_id": anchor_id, "cancelled": cancelled},
    )
    return message_id


def _anchors(conn) -> dict[uuid.UUID, dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT id, title, status, message_count, source_channel, source_thread_identity,
                   provider_session_id, provider_runtime_type
            FROM public.dashboard_conversations
            """
        )
    ).mappings()
    return {row["id"]: dict(row) for row in rows}


def _links(conn, table: str, key: str) -> dict[uuid.UUID, uuid.UUID]:
    rows = conn.execute(text(f"SELECT {key}, conversation_id FROM public.{table}"))
    return {row[0]: row[1] for row in rows}


def _has_column(conn) -> bool:
    return bool(
        conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'dashboard_conversations'
                      AND column_name = 'external_conversation_id'
                )
                """
            )
        ).scalar()
    )


def _backup_tables(conn) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename LIKE 'core\\_265\\_%'"
        )
    )
    return {row[0] for row in rows}


def _seed(conn) -> dict[str, Any]:
    chat = "-100123"
    # Legacy per-message anchors, the core_208 helper's stable anchor, and a
    # bare-chat anchor all belong to one chat. The newest handle is on msg 104.
    legacy = [
        _insert_anchor(conn, thread=f"{chat}:10{index}", handle=f"provider-{index}", offset=index)
        for index in range(5)
    ]
    stable = _insert_anchor(
        conn, thread=f"telegram:{chat}", handle="provider-stable", offset=-1, message_count=1
    )
    bare = _insert_anchor(conn, thread=chat, offset=6)
    messages = [_insert_message(conn, legacy[0]), _insert_message(conn, legacy[1], cancelled=True)]
    messages.append(_insert_message(conn, stable))
    other_chat = _insert_anchor(conn, thread="555:9", handle="provider-other", message_count=0)
    # The same chat under another butler and under the legacy channel name are
    # separate conversations and collapse separately.
    other_butler = [
        _insert_anchor(conn, butler="health", thread=f"{chat}:20{index}", offset=index)
        for index in range(2)
    ]
    legacy_channel = [
        _insert_anchor(conn, channel="telegram", thread=f"{chat}:30{index}", offset=index)
        for index in range(2)
    ]
    # A bare user-client key and its already namespaced twin share one key.
    user_client = _insert_anchor(conn, thread="998877", channel="telegram_user_client")
    user_client_namespaced = _insert_anchor(
        conn, thread="telegram:998877", channel="telegram_user_client", handle="provider-uc"
    )
    whatsapp = _insert_anchor(
        conn, thread="6591234567@s.whatsapp.net", channel="whatsapp_user_client"
    )
    email = _insert_anchor(conn, thread="gmail-thread-1", channel="email")
    return {
        "chat": chat,
        "legacy": legacy,
        "stable": stable,
        "bare": bare,
        "messages": messages,
        "other_chat": other_chat,
        "other_butler": other_butler,
        "legacy_channel": legacy_channel,
        "user_client": user_client,
        "user_client_namespaced": user_client_namespaced,
        "whatsapp": whatsapp,
        "email": email,
    }


def test_identity_split_collapses_restores_and_cycles(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    engine = create_engine(db_url)
    with engine.begin() as conn:
        for schema in _SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    for schema in _SCHEMAS:
        command.upgrade(_config(db_url, schema), "core@core_261")

    with engine.begin() as conn:
        seeded = _seed(conn)
        before = _anchors(conn)
        messages_before = _links(conn, "dashboard_messages", "id")
        turns_before = _links(conn, "dashboard_conversation_turns", "message_id")

    command.upgrade(_config(db_url, "general"), "core@core_265")

    survivor_id = seeded["legacy"][4]
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    """
                    SELECT id, butler_name, source_channel, source_thread_identity,
                           external_conversation_id, provider_session_id, message_count
                    FROM public.dashboard_conversations
                    """
                )
            )
            .mappings()
            .all()
        )
        by_id = {row["id"]: row for row in rows}
        chat_rows = [
            row
            for row in rows
            if row["butler_name"] == "general"
            and row["source_channel"] == "telegram_bot"
            and row["external_conversation_id"] == f"telegram:{seeded['chat']}"
        ]
        assert [row["id"] for row in chat_rows] == [survivor_id]
        survivor = by_id[survivor_id]
        assert survivor["provider_session_id"] == "provider-4"
        assert survivor["source_thread_identity"] == f"telegram:{seeded['chat']}"
        assert survivor["message_count"] == 3
        assert set(_links(conn, "dashboard_messages", "id").values()) >= {survivor_id}
        assert all(
            conversation == survivor_id
            for message, conversation in _links(conn, "dashboard_messages", "id").items()
            if message in seeded["messages"]
        )
        assert set(_links(conn, "dashboard_conversation_turns", "message_id").values()) == {
            survivor_id
        }
        assert by_id[seeded["other_chat"]]["external_conversation_id"] == "telegram:555"
        for survivor_of_group, group in (
            (seeded["other_butler"][1], seeded["other_butler"]),
            (seeded["legacy_channel"][1], seeded["legacy_channel"]),
        ):
            assert set(group) & set(by_id) == {survivor_of_group}
            assert by_id[survivor_of_group]["external_conversation_id"] == (
                f"telegram:{seeded['chat']}"
            )
        assert seeded["user_client"] not in by_id
        user_client_survivor = by_id[seeded["user_client_namespaced"]]
        assert user_client_survivor["source_thread_identity"] == "telegram:998877"
        assert user_client_survivor["external_conversation_id"] == "telegram:998877"
        assert (
            by_id[seeded["whatsapp"]]["external_conversation_id"]
            == "whatsapp:6591234567@s.whatsapp.net"
        )
        assert by_id[seeded["email"]]["external_conversation_id"] == "gmail-thread-1"
        assert _backup_tables(conn) == set(_BACKUP_TABLES)
        backup_rows = conn.execute(
            text("SELECT count(*) FROM public.core_265_anchor_backup")
        ).scalar()
        assert backup_rows == 15

    # A second schema replays core_265 against the already-migrated public table.
    command.upgrade(_config(db_url, "health"), "core@core_265")
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM public.core_265_anchor_backup")).scalar() == 15
        )

    # The application helper resolves the survivor, not a fresh anchor.
    async def _resolve() -> tuple[dict[str, Any], bool]:
        pool = await asyncpg.create_pool(db_url.replace("+psycopg2", ""), min_size=1, max_size=2)
        try:
            return await conversation_get_or_create_by_thread(
                pool,
                butler_name="general",
                source_channel="telegram_bot",
                external_conversation_id=f"telegram:{seeded['chat']}",
                first_message="next turn",
            )
        finally:
            await pool.close()

    resolved, is_new = asyncio.run(_resolve())
    assert resolved["id"] == survivor_id and not is_new

    # Legitimate post-upgrade activity on the survivor.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE public.dashboard_conversations
                SET title = 'renamed', provider_session_id = 'provider-after'
                WHERE id = :id
                """
            ),
            {"id": survivor_id},
        )
        post_upgrade_message = _insert_message(conn, survivor_id)

    # Downgrading one schema leaves the shared table alone.
    command.downgrade(_config(db_url, "health"), "core@core_261")
    with engine.connect() as conn:
        assert _has_column(conn)
        assert _backup_tables(conn) == set(_BACKUP_TABLES)

    command.downgrade(_config(db_url, "general"), "core@core_261")
    with engine.connect() as conn:
        assert not _has_column(conn)
        assert _backup_tables(conn) == set()
        after = _anchors(conn)
        assert set(after) == set(before)
        for anchor_id, row in before.items():
            restored = after[anchor_id]
            assert restored["source_thread_identity"] == row["source_thread_identity"]
            if anchor_id != survivor_id:
                assert restored == row
        assert after[survivor_id]["title"] == "renamed"
        assert after[survivor_id]["provider_session_id"] == "provider-after"
        assert after[survivor_id]["message_count"] == 1
        messages_after = _links(conn, "dashboard_messages", "id")
        assert messages_after.pop(post_upgrade_message) == survivor_id
        assert messages_after == messages_before
        turns_after = _links(conn, "dashboard_conversation_turns", "message_id")
        turns_after.pop(post_upgrade_message)
        assert turns_after == turns_before
        cancelled = conn.execute(
            text(
                """
                SELECT conversation_id FROM public.dashboard_conversation_turns
                WHERE cancel_requested_at IS NOT NULL
                """
            )
        ).scalar_one()
        assert cancelled == seeded["legacy"][1]

    # A second cycle snapshots afresh instead of reusing the first one.
    command.upgrade(_config(db_url, "general"), "core@core_265")
    with engine.begin() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM public.core_265_anchor_backup")).scalar() == 15
        )
        conn.execute(
            text("UPDATE public.dashboard_conversations SET title = 'cycle-2' WHERE id = :id"),
            {"id": survivor_id},
        )
    command.downgrade(_config(db_url, "general"), "core@core_261")
    with engine.connect() as conn:
        after = _anchors(conn)
        assert set(after) == set(before)
        assert after[survivor_id]["title"] == "cycle-2"
        assert {anchor_id: row["source_thread_identity"] for anchor_id, row in after.items()} == {
            anchor_id: row["source_thread_identity"] for anchor_id, row in before.items()
        }
    engine.dispose()


def test_upgrade_refuses_a_leftover_snapshot(postgres_container) -> None:
    db_url = create_migration_db(postgres_container, migration_db_name())
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS general"))
    command.upgrade(_config(db_url, "general"), "core@core_261")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE public.core_265_anchor_backup (id UUID PRIMARY KEY)"))
    with pytest.raises(Exception, match="core_265_anchor_backup"):
        command.upgrade(_config(db_url, "general"), "core@core_265")
    with engine.connect() as conn:
        assert not _has_column(conn)
    engine.dispose()


def test_downgrade_refuses_a_partial_restore(postgres_container) -> None:
    """A post-upgrade row holding an original identity fails the downgrade intact."""
    db_url = create_migration_db(postgres_container, migration_db_name())
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS general"))
    command.upgrade(_config(db_url, "general"), "core@core_261")
    with engine.begin() as conn:
        first = _insert_anchor(conn, thread="-100777:1", offset=0)
        second = _insert_anchor(conn, thread="-100777:2", offset=1)
        moved_message = _insert_message(conn, first)
    command.upgrade(_config(db_url, "general"), "core@core_265")

    # An unsplit producer re-created the collapsed anchor's original key.
    with engine.begin() as conn:
        blocker = _insert_anchor(conn, thread="-100777:1")
        conn.execute(
            text(
                "UPDATE public.dashboard_conversations "
                "SET external_conversation_id = source_thread_identity WHERE id = :id"
            ),
            {"id": blocker},
        )

    with pytest.raises(Exception, match="core_265 downgrade cannot restore 1 anchor"):
        command.downgrade(_config(db_url, "general"), "core@core_261")
    with engine.connect() as conn:
        assert _has_column(conn)
        assert _backup_tables(conn) == set(_BACKUP_TABLES)
        assert _links(conn, "dashboard_messages", "id") == {moved_message: second}
        assert conn.execute(text("SELECT version_num FROM general.alembic_version")).scalar() == (
            "core_265"
        )

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM public.dashboard_conversations WHERE id = :id"), {"id": blocker}
        )
    command.downgrade(_config(db_url, "general"), "core@core_261")
    with engine.connect() as conn:
        assert not _has_column(conn)
        assert _backup_tables(conn) == set()
        assert _links(conn, "dashboard_messages", "id") == {moved_message: first}
    engine.dispose()
