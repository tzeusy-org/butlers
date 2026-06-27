"""Integration tests for the mailbox module and Switchboard post_mail tool.

Covers MailboxModule CRUD operations and Switchboard post_mail routing,
all against real PostgreSQL via testcontainers.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid

import pytest

from butlers.db import register_jsonb_codec

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


MAILBOX_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS mailbox (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        sender TEXT NOT NULL,
        sender_channel TEXT NOT NULL,
        subject TEXT,
        body JSONB NOT NULL,
        priority INTEGER NOT NULL DEFAULT 2,
        status TEXT NOT NULL DEFAULT 'unread',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        read_at TIMESTAMPTZ,
        actioned_at TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_mailbox_status_created
        ON mailbox (status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_mailbox_sender
        ON mailbox (sender);
"""

SWITCHBOARD_TABLES_SQL = """
    CREATE TABLE IF NOT EXISTS butler_registry (
        name TEXT PRIMARY KEY,
        endpoint_url TEXT NOT NULL,
        description TEXT,
        modules JSONB NOT NULL DEFAULT '[]',
        last_seen_at TIMESTAMPTZ,
        eligibility_state TEXT NOT NULL DEFAULT 'active',
        liveness_ttl_seconds INTEGER NOT NULL DEFAULT 300,
        quarantined_at TIMESTAMPTZ,
        quarantine_reason TEXT,
        route_contract_min INTEGER NOT NULL DEFAULT 1,
        route_contract_max INTEGER NOT NULL DEFAULT 1,
        capabilities JSONB NOT NULL DEFAULT '[]',
        eligibility_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        agent_type TEXT NOT NULL DEFAULT 'butler'
    );
    CREATE TABLE IF NOT EXISTS routing_log (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        source_butler TEXT NOT NULL,
        target_butler TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        success BOOLEAN NOT NULL,
        duration_ms INTEGER,
        error TEXT,
        thread_id TEXT,
        source_channel TEXT,
        contact_id UUID,
        entity_id UUID,
        sender_roles TEXT[],
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def mailbox_pool(postgres_container):
    """Provision a fresh database with the mailbox table and return a pool."""
    import asyncpg

    db_name = _unique_db_name()
    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await pool.execute(MAILBOX_TABLE_SQL)
    yield pool
    await pool.close()


@pytest.fixture
async def switchboard_pool(postgres_container):
    """Provision a fresh database with switchboard + mailbox tables and return a pool."""
    import asyncpg

    db_name = _unique_db_name()
    admin_conn = await asyncpg.connect(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database="postgres",
    )
    try:
        safe_name = db_name.replace('"', '""')
        await admin_conn.execute(f'CREATE DATABASE "{safe_name}"')
    finally:
        await admin_conn.close()

    pool = await asyncpg.create_pool(
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        database=db_name,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await pool.execute(SWITCHBOARD_TABLES_SQL)
    yield pool
    await pool.close()


# ==================================================================
# MailboxModule CRUD operations
# ==================================================================


class TestMailboxCRUD:
    async def test_post_full_and_defaults(self, mailbox_pool):
        """mailbox_post: all fields stored correctly; minimal post uses correct defaults."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(
            mailbox_pool,
            sender="alice",
            sender_channel="telegram_bot",
            body="Hello",
            subject="Greetings",
            priority=1,
            metadata='{"trace_id": "abc123"}',
        )
        msg_id = uuid.UUID(result["message_id"])
        row = await mailbox_pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_id)
        assert row["sender"] == "alice" and row["priority"] == 1 and row["status"] == "unread"

        # Defaults
        r2 = await mod._mailbox_post(mailbox_pool, "system", "scheduler", "Scheduled check")
        row2 = await mailbox_pool.fetchrow(
            "SELECT * FROM mailbox WHERE id = $1", uuid.UUID(r2["message_id"])
        )
        assert row2["priority"] == 2 and row2["subject"] is None and row2["read_at"] is None

    async def test_list_ordering_filter_pagination_empty(self, mailbox_pool):
        """mailbox_list: newest first; status/sender filter; limit+offset pagination; empty OK."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        await mailbox_pool.execute("DELETE FROM mailbox")

        await mod._mailbox_post(mailbox_pool, "a", "mcp", "first", subject="First")
        await asyncio.sleep(0.01)
        await mod._mailbox_post(mailbox_pool, "b", "mcp", "second", subject="Second")
        await asyncio.sleep(0.01)
        r3 = await mod._mailbox_post(mailbox_pool, "c", "mcp", "third", subject="Third")
        await mod._mailbox_update_status(mailbox_pool, r3["message_id"], "read")

        # Newest first
        msgs = await mod._mailbox_list(mailbox_pool)
        subjects = [m["subject"] for m in msgs]
        assert subjects == ["Third", "Second", "First"]

        # Status filter
        unread = await mod._mailbox_list(mailbox_pool, status="unread")
        assert all(m["status"] == "unread" for m in unread)

        # Pagination
        for i in range(3):
            await mod._mailbox_post(mailbox_pool, f"p{i}", "mcp", f"page-{i}")
            await asyncio.sleep(0.01)
        page1 = await mod._mailbox_list(mailbox_pool, limit=2, offset=0)
        page2 = await mod._mailbox_list(mailbox_pool, limit=2, offset=2)
        assert {m["id"] for m in page1}.isdisjoint({m["id"] for m in page2})

        # Empty
        await mailbox_pool.execute("DELETE FROM mailbox")
        assert await mod._mailbox_list(mailbox_pool) == []

    async def test_read_automarks_and_nonexistent(self, mailbox_pool):
        """mailbox_read: returns full message; auto-marks unread as read; nonexistent errors."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(
            mailbox_pool,
            "health-butler",
            "mcp",
            "Daily report",
            subject="Report",
            priority=1,
            metadata='{"type": "daily"}',
        )
        msg_id = result["message_id"]

        msg = await mod._mailbox_read(mailbox_pool, msg_id)
        assert msg["sender"] == "health-butler" and msg["subject"] == "Report"
        assert msg["status"] == "read" and msg["read_at"] is not None

        # Idempotent — re-read stays read, read_at unchanged
        read_at_1 = msg["read_at"]
        msg2 = await mod._mailbox_read(mailbox_pool, msg_id)
        assert msg2["status"] == "read" and msg2["read_at"] == read_at_1

        # Nonexistent
        result3 = await mod._mailbox_read(mailbox_pool, str(uuid.uuid4()))
        assert "error" in result3 and "not found" in result3["error"].lower()

    async def test_status_transitions_and_invalid(self, mailbox_pool):
        """mailbox_update_status: transitions through statuses, sets timestamps; rejects invalid."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(mailbox_pool, "sender", "mcp", "transition test")
        msg_id = result["message_id"]

        r = await mod._mailbox_update_status(mailbox_pool, msg_id, "read")
        assert r["status"] == "read"
        r = await mod._mailbox_update_status(mailbox_pool, msg_id, "actioned")
        assert r["status"] == "actioned"
        # The actioned transition must set actioned_at (no column-existence guard).
        assert r["actioned_at"] is not None
        r = await mod._mailbox_update_status(mailbox_pool, msg_id, "archived")
        assert r["status"] == "archived"

        # Timestamps: read_at set after read, actioned_at after actioned
        result2 = await mod._mailbox_post(mailbox_pool, "s", "mcp", "ts test")
        await mod._mailbox_update_status(mailbox_pool, result2["message_id"], "read")
        row = await mailbox_pool.fetchrow(
            "SELECT read_at, actioned_at FROM mailbox WHERE id = $1",
            uuid.UUID(result2["message_id"]),
        )
        assert row["read_at"] is not None and row["actioned_at"] is None

        # Invalid status
        result3 = await mod._mailbox_post(mailbox_pool, "s", "mcp", "invalid")
        r3 = await mod._mailbox_update_status(mailbox_pool, result3["message_id"], "deleted")
        assert "error" in r3 and "invalid" in r3["error"].lower()

    async def test_stats_counts(self, mailbox_pool):
        """mailbox_stats returns accurate counts across all statuses."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        await mailbox_pool.execute("DELETE FROM mailbox")

        await mod._mailbox_post(mailbox_pool, "a", "mcp", "unread 1")
        await mod._mailbox_post(mailbox_pool, "b", "mcp", "unread 2")
        r3 = await mod._mailbox_post(mailbox_pool, "c", "mcp", "read")
        r4 = await mod._mailbox_post(mailbox_pool, "d", "mcp", "actioned")
        r5 = await mod._mailbox_post(mailbox_pool, "e", "mcp", "archived")
        await mod._mailbox_update_status(mailbox_pool, r3["message_id"], "read")
        await mod._mailbox_update_status(mailbox_pool, r4["message_id"], "actioned")
        await mod._mailbox_update_status(mailbox_pool, r5["message_id"], "archived")

        stats = await mod._mailbox_stats(mailbox_pool)
        assert stats["unread"] == 2 and stats["read"] == 1 and stats["actioned"] == 1
        assert stats["archived"] == 1 and stats["total"] == 5


# ==================================================================
# Switchboard post_mail routing
# ==================================================================


class TestPostMail:
    async def test_post_mail_routes_to_target(self, switchboard_pool):
        """post_mail routes to target butler, returns message_id, logs correctly."""
        from butlers.tools.switchboard import post_mail, register_butler

        await register_butler(
            switchboard_pool,
            "inbox-butler",
            "http://localhost:9500/sse",
            "Butler with mailbox",
            ["mailbox"],
        )
        call_log: list[dict] = []

        async def mock_call(endpoint_url, tool_name, args):
            call_log.append({"endpoint_url": endpoint_url, "tool_name": tool_name, "args": args})
            return {"message_id": "fake-uuid-123"}

        result = await post_mail(
            switchboard_pool,
            target_butler="inbox-butler",
            sender="relationship",
            sender_channel="mcp",
            body="Check on user",
            subject="Follow-up",
            call_fn=mock_call,
        )

        assert result["result"]["message_id"] == "fake-uuid-123"
        assert call_log[0]["tool_name"] == "mailbox_post"
        assert call_log[0]["args"]["sender"] == "relationship"

    async def test_post_mail_errors_and_identity_preservation(self, switchboard_pool):
        """post_mail: not-found / no-mailbox-module return errors; sender identity preserved."""
        from butlers.tools.switchboard import post_mail, register_butler

        await switchboard_pool.execute("DELETE FROM butler_registry")

        # Not found
        result = await post_mail(
            switchboard_pool,
            target_butler="nonexistent",
            sender="system",
            sender_channel="scheduler",
            body="Hello",
        )
        assert "error" in result and "not found" in result["error"].lower()

        # No mailbox module
        await register_butler(
            switchboard_pool,
            "no-mailbox",
            "http://localhost:9600/sse",
            "No mailbox",
            ["email", "telegram"],
        )
        result2 = await post_mail(
            switchboard_pool,
            target_butler="no-mailbox",
            sender="alice",
            sender_channel="telegram_bot",
            body="fail",
        )
        assert "error" in result2 and "mailbox" in result2["error"].lower()

        # Sender identity preserved
        await register_butler(
            switchboard_pool, "target-butler", "http://localhost:9700/sse", "Target", ["mailbox"]
        )
        received: dict = {}

        async def capture_call(endpoint_url, tool_name, args):
            received.update(args)
            return {"message_id": "captured-uuid"}

        await post_mail(
            switchboard_pool,
            target_butler="target-butler",
            sender="alice",
            sender_channel="telegram_bot",
            body="Hello from Alice",
            subject="Hi",
            priority=1,
            metadata={"correlation_id": "xyz"},
            call_fn=capture_call,
        )
        assert received["sender"] == "alice" and received["sender_channel"] == "telegram_bot"
        assert "correlation_id" in json.loads(received["metadata"])
