"""Integration tests for conversation history SQL queries against a real database.

Validates that _load_realtime_history, _load_email_history, and
_load_conversation_history run successfully against the v2 message_inbox
schema (post migration sw_008).
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
from sqlalchemy import create_engine, text

from butlers.modules.pipeline import (
    _load_conversation_history,
    _load_email_history,
    _load_realtime_history,
)

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("pgvector/pgvector:pg17") as pg:
        yield pg


@pytest.fixture(scope="module")
def switchboard_dsn(postgres_container):
    """Create a database, run switchboard migrations, return asyncpg DSN."""
    from alembic import command
    from butlers.migrations import _bootstrap_extensions, _build_alembic_config

    db_name = _unique_db_name()
    admin_url = postgres_container.get_connection_url()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    engine.dispose()

    host = postgres_container.get_container_host_ip()
    port = postgres_container.get_exposed_port(5432)
    user = postgres_container.username
    password = postgres_container.password
    sa_url = f"postgresql://{user}:{password}@{host}:{port}/{db_name}"

    _bootstrap_extensions(sa_url)

    config = _build_alembic_config(sa_url, chains=["core"])
    command.upgrade(config, "core@head")
    config = _build_alembic_config(sa_url, chains=["switchboard"])
    command.upgrade(config, "switchboard@head")

    return f"postgres://{user}:{password}@{host}:{port}/{db_name}"


async def _insert_message(
    pool: asyncpg.Pool,
    *,
    text: str,
    sender: str,
    thread_identity: str,
    received_at: datetime,
    channel: str = "telegram_bot",
) -> None:
    """Insert a v2-schema message_inbox row."""
    # Ensure the monthly partition exists (mirrors production ingestion behaviour).
    await pool.execute("SELECT switchboard_message_inbox_ensure_partition($1)", received_at)
    request_context = {
        "source_channel": channel,
        "source_sender_identity": sender,
        "source_thread_identity": thread_identity,
        "source_endpoint_identity": f"{channel}:bot",
    }
    raw_payload = {
        "content": text,
        "metadata": {},
    }
    await pool.execute(
        """
        INSERT INTO message_inbox (
            received_at, request_context, raw_payload,
            normalized_text, lifecycle_state, schema_version
        ) VALUES (
            $1, $2::jsonb, $3::jsonb, $4, 'accepted', 'message_inbox.v2'
        )
        """,
        received_at,
        json.dumps(request_context),
        json.dumps(raw_payload),
        text,
    )


# ---------------------------------------------------------------------------
# _load_realtime_history
# ---------------------------------------------------------------------------


async def test_realtime_history_basic(switchboard_dsn):
    """Realtime history returns messages for thread; empty thread returns []; excludes other threads."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"chat:{uuid.uuid4().hex[:8]}"
        thread_b = f"chat:{uuid.uuid4().hex[:8]}"

        await _insert_message(
            pool,
            text="hello",
            sender="user1",
            thread_identity=thread,
            received_at=now - timedelta(minutes=5),
        )
        await _insert_message(
            pool,
            text="world",
            sender="user2",
            thread_identity=thread,
            received_at=now - timedelta(minutes=3),
        )
        await _insert_message(
            pool,
            text="other thread",
            sender="u2",
            thread_identity=thread_b,
            received_at=now - timedelta(minutes=2),
        )

        messages = await _load_realtime_history(pool, thread, now)
        assert len(messages) == 2
        assert messages[0]["raw_content"] == "hello" and messages[0]["sender_id"] == "user1"

        # Excludes other thread
        msgs_a_only = await _load_realtime_history(pool, thread, now)
        assert all(m["raw_content"] != "other thread" for m in msgs_a_only)

        # Empty thread
        assert await _load_realtime_history(pool, "nonexistent:thread", now) == []
    finally:
        await pool.close()


async def test_realtime_history_count_window(switchboard_dsn):
    """Count-based window picks up older messages outside time window."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"chat:{uuid.uuid4().hex[:8]}"

        await _insert_message(
            pool,
            text="old msg",
            sender="user1",
            thread_identity=thread,
            received_at=now - timedelta(hours=1),
        )

        messages = await _load_realtime_history(
            pool,
            thread,
            now,
            max_time_window_minutes=15,
            max_message_count=30,
        )

        assert len(messages) == 1
        assert messages[0]["raw_content"] == "old msg"
    finally:
        await pool.close()


async def test_realtime_history_telegram_groups_message_scoped_thread_ids(switchboard_dsn):
    """Telegram history groups message-scoped thread IDs by chat identity."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        chat_id = f"-100{uuid.uuid4().int % 10_000_000:07d}"
        current_thread = f"{chat_id}:103"

        await _insert_message(
            pool,
            text="earlier inbound",
            sender="user1",
            thread_identity=f"{chat_id}:101",
            received_at=now - timedelta(minutes=6),
            channel="telegram_bot",
        )
        await _insert_outbound_message(
            pool,
            text="earlier outbound",
            origin_butler="general",
            thread_identity=f"{chat_id}:102",
            received_at=now - timedelta(minutes=5),
            channel="telegram_bot",
        )
        await _insert_message(
            pool,
            text="different chat",
            sender="user2",
            thread_identity=f"-100{uuid.uuid4().int % 10_000_000:07d}:201",
            received_at=now - timedelta(minutes=4),
            channel="telegram_bot",
        )

        messages = await _load_realtime_history(
            pool,
            current_thread,
            now,
            source_channel="telegram_bot",
        )

        assert len(messages) == 2
        assert [m["raw_content"] for m in messages] == ["earlier inbound", "earlier outbound"]
        assert [m["direction"] for m in messages] == ["inbound", "outbound"]
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# _load_email_history
# ---------------------------------------------------------------------------


async def test_email_history_returns_chain(switchboard_dsn):
    """Email history query runs against v2 schema and returns full chain."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"email:{uuid.uuid4().hex[:8]}"

        await _insert_message(
            pool,
            text="First email",
            sender="alice@example.com",
            thread_identity=thread,
            received_at=now - timedelta(days=2),
            channel="email",
        )
        await _insert_message(
            pool,
            text="Reply email",
            sender="bob@example.com",
            thread_identity=thread,
            received_at=now - timedelta(days=1),
            channel="email",
        )

        messages = await _load_email_history(pool, thread, now)

        assert len(messages) == 2
        assert messages[0]["raw_content"] == "First email"
        assert messages[1]["raw_content"] == "Reply email"
    finally:
        await pool.close()


async def test_email_history_truncates_oldest(switchboard_dsn):
    """Email history truncates from oldest end when over token limit."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"email:{uuid.uuid4().hex[:8]}"

        for i in range(3):
            await _insert_message(
                pool,
                text="x" * 100,
                sender=f"u{i}@test.com",
                thread_identity=thread,
                received_at=now - timedelta(hours=3 - i),
                channel="email",
            )

        messages = await _load_email_history(pool, thread, now, max_tokens=30)
        assert len(messages) == 1
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# _load_conversation_history (dispatcher)
# ---------------------------------------------------------------------------


async def test_conversation_history_channels(switchboard_dsn):
    """Telegram returns formatted history; API channel returns empty string."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"chat:{uuid.uuid4().hex[:8]}"

        await _insert_message(
            pool,
            text="previous message",
            sender="user42",
            thread_identity=thread,
            received_at=now - timedelta(minutes=5),
        )

        result = await _load_conversation_history(pool, "telegram_bot", thread, now)
        assert (
            "## Recent Conversation History" in result
            and "previous message" in result
            and "user42" in result
        )

        # API channel returns empty string
        api_result = await _load_conversation_history(pool, "api", "some-thread", now)
        assert api_result == ""
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Outbound message inbox writes (direction column)
# ---------------------------------------------------------------------------


async def _insert_outbound_message(
    pool: asyncpg.Pool,
    *,
    text: str,
    origin_butler: str,
    thread_identity: str,
    received_at: datetime,
    channel: str = "telegram_bot",
) -> None:
    """Insert an outbound (direction='outbound') message_inbox row."""
    import json

    # Ensure the monthly partition exists (mirrors production ingestion behaviour).
    await pool.execute("SELECT switchboard_message_inbox_ensure_partition($1)", received_at)
    request_context = {
        "source_channel": channel,
        "source_sender_identity": origin_butler,
        "source_thread_identity": thread_identity,
        "source_endpoint_identity": f"butler:{origin_butler}",
    }
    raw_payload = {
        "content": text,
        "metadata": {"origin_butler": origin_butler},
    }
    await pool.execute(
        """
        INSERT INTO message_inbox (
            received_at, request_context, raw_payload,
            normalized_text, direction, lifecycle_state, schema_version
        ) VALUES (
            $1, $2::jsonb, $3::jsonb, $4, 'outbound', 'completed', 'message_inbox.v2'
        )
        """,
        received_at,
        json.dumps(request_context),
        json.dumps(raw_payload),
        text,
    )


async def test_realtime_history_outbound_messages_and_formatted(switchboard_dsn):
    """Outbound butler responses appear in realtime history; formatted shows butler arrow prefix."""
    from butlers.modules.pipeline import _format_history_context

    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"chat:{uuid.uuid4().hex[:8]}"

        await _insert_message(
            pool,
            text="So does da pe pe",
            sender="user42",
            thread_identity=thread,
            received_at=now - timedelta(minutes=3),
        )
        await _insert_outbound_message(
            pool,
            text="Got it! I've stored da pe pe's address too.",
            origin_butler="relationship",
            thread_identity=thread,
            received_at=now - timedelta(minutes=2),
        )

        messages = await _load_realtime_history(pool, thread, now)
        assert len(messages) == 2
        assert messages[0]["direction"] == "inbound" and messages[1]["direction"] == "outbound"
        assert messages[1]["sender_id"] == "relationship"

        result = _format_history_context(messages)
        assert "**user42**" in result and "**butler → relationship**" in result
        assert "So does da pe pe" in result
    finally:
        await pool.close()


async def test_email_history_includes_outbound_messages(switchboard_dsn):
    """Outbound butler responses appear in _load_email_history results."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"email:{uuid.uuid4().hex[:8]}"

        await _insert_message(
            pool,
            text="Can you remind me about Sarah's birthday?",
            sender="alice@example.com",
            thread_identity=thread,
            received_at=now - timedelta(days=1),
            channel="email",
        )
        await _insert_outbound_message(
            pool,
            text="Done! I've set a reminder for Sarah's birthday.",
            origin_butler="relationship",
            thread_identity=thread,
            received_at=now - timedelta(hours=23),
            channel="email",
        )

        messages = await _load_email_history(pool, thread, now)

        assert len(messages) == 2
        assert messages[0]["direction"] == "inbound"
        assert messages[1]["direction"] == "outbound"
        assert messages[1]["sender_id"] == "relationship"
    finally:
        await pool.close()
