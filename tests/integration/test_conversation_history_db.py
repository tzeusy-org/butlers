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

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture(scope="module")
def switchboard_dsn(postgres_container):
    """Create a database, run switchboard migrations, return asyncpg DSN."""
    from alembic import command
    from butlers.migrations import _build_alembic_config

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
    channel: str = "telegram",
) -> None:
    """Insert a v2-schema message_inbox row."""
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


async def test_realtime_history_returns_messages(switchboard_dsn):
    """Realtime history query runs against v2 schema and returns correct data."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread = f"chat:{uuid.uuid4().hex[:8]}"

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

        messages = await _load_realtime_history(pool, thread, now)

        assert len(messages) == 2
        assert messages[0]["raw_content"] == "hello"
        assert messages[0]["sender_id"] == "user1"
        assert messages[1]["raw_content"] == "world"
        assert messages[1]["sender_id"] == "user2"
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


async def test_realtime_history_empty_thread(switchboard_dsn):
    """Returns empty list for a thread with no messages."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        messages = await _load_realtime_history(pool, "nonexistent:thread", now)
        assert messages == []
    finally:
        await pool.close()


async def test_realtime_history_excludes_other_threads(switchboard_dsn):
    """Messages from other threads are not included."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        now = datetime.now(UTC)
        thread_a = f"chat:{uuid.uuid4().hex[:8]}"
        thread_b = f"chat:{uuid.uuid4().hex[:8]}"

        await _insert_message(
            pool,
            text="thread A",
            sender="u1",
            thread_identity=thread_a,
            received_at=now - timedelta(minutes=2),
        )
        await _insert_message(
            pool,
            text="thread B",
            sender="u2",
            thread_identity=thread_b,
            received_at=now - timedelta(minutes=2),
        )

        messages = await _load_realtime_history(pool, thread_a, now)
        assert len(messages) == 1
        assert messages[0]["raw_content"] == "thread A"
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


async def test_conversation_history_telegram(switchboard_dsn):
    """Full dispatcher path for telegram produces formatted history."""
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

        result = await _load_conversation_history(pool, "telegram", thread, now)

        assert "## Recent Conversation History" in result
        assert "previous message" in result
        assert "user42" in result
    finally:
        await pool.close()


async def test_conversation_history_none_for_api(switchboard_dsn):
    """API channel returns empty string (no history loaded)."""
    pool = await asyncpg.create_pool(switchboard_dsn)
    try:
        result = await _load_conversation_history(
            pool,
            "api",
            "some-thread",
            datetime.now(UTC),
        )
        assert result == ""
    finally:
        await pool.close()
