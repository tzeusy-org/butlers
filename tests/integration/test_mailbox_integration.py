"""Integration tests for the mailbox module and Switchboard post_mail tool.

19 test cases covering MailboxModule CRUD operations and Switchboard
post_mail routing, all against real PostgreSQL via testcontainers.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid

import pytest

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
        registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
"""


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


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
    )
    await pool.execute(SWITCHBOARD_TABLES_SQL)
    yield pool
    await pool.close()


# ==================================================================
# Tests 1-15: MailboxModule CRUD operations
# ==================================================================


class TestMailboxPost:
    """Tests 1-2: mailbox_post insert and defaults."""

    async def test_post_inserts_message_with_all_fields(self, mailbox_pool):
        """Test 1: mailbox_post inserts message, all fields stored correctly."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(
            mailbox_pool,
            sender="alice",
            sender_channel="telegram",
            body="Hello from Alice",
            subject="Greetings",
            priority=1,
            metadata='{"trace_id": "abc123"}',
        )

        assert "message_id" in result
        msg_id = uuid.UUID(result["message_id"])

        # Verify stored in DB
        row = await mailbox_pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_id)
        assert row is not None
        assert row["sender"] == "alice"
        assert row["sender_channel"] == "telegram"
        assert row["subject"] == "Greetings"
        body = json.loads(row["body"]) if isinstance(row["body"], str) else row["body"]
        assert body == {"text": "Hello from Alice"}
        assert row["priority"] == 1
        assert row["status"] == "unread"
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        assert meta == {"trace_id": "abc123"}
        assert row["created_at"] is not None

    async def test_post_default_values(self, mailbox_pool):
        """Test 2: mailbox_post uses correct defaults (priority=2, status=unread, metadata={})."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(
            mailbox_pool,
            sender="system",
            sender_channel="scheduler",
            body="Scheduled check",
        )

        msg_id = uuid.UUID(result["message_id"])
        row = await mailbox_pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_id)
        assert row["priority"] == 2
        assert row["status"] == "unread"
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        assert meta == {}
        assert row["subject"] is None
        assert row["read_at"] is None
        assert row["actioned_at"] is None


class TestMailboxList:
    """Tests 3-7: mailbox_list ordering, filtering, pagination, empty."""

    async def test_list_ordered_by_created_at_desc(self, mailbox_pool):
        """Test 3: mailbox_list returns messages ordered by created_at DESC."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()

        # Insert messages with small delays to ensure ordering
        await mod._mailbox_post(mailbox_pool, "a", "mcp", "first message", subject="First")
        await asyncio.sleep(0.01)
        await mod._mailbox_post(mailbox_pool, "b", "mcp", "second message", subject="Second")
        await asyncio.sleep(0.01)
        await mod._mailbox_post(mailbox_pool, "c", "mcp", "third message", subject="Third")

        messages = await mod._mailbox_list(mailbox_pool)
        subjects = [m["subject"] for m in messages]
        assert subjects == ["Third", "Second", "First"]

    async def test_list_filter_by_status(self, mailbox_pool):
        """Test 4: mailbox_list filters by status."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(mailbox_pool, "x", "mcp", "will be read")
        msg_id = result["message_id"]

        # Mark as read
        await mod._mailbox_update_status(mailbox_pool, msg_id, "read")
        await mod._mailbox_post(mailbox_pool, "y", "mcp", "still unread")

        unread = await mod._mailbox_list(mailbox_pool, status="unread")
        read = await mod._mailbox_list(mailbox_pool, status="read")

        unread_senders = {m["sender"] for m in unread}
        read_senders = {m["sender"] for m in read}

        assert "y" in unread_senders
        assert "x" in read_senders
        assert "x" not in unread_senders

    async def test_list_filter_by_sender(self, mailbox_pool):
        """Test 5: mailbox_list filters by sender."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        await mod._mailbox_post(mailbox_pool, "alice", "telegram", "from alice")
        await mod._mailbox_post(mailbox_pool, "bob", "mcp", "from bob")
        await mod._mailbox_post(mailbox_pool, "alice", "email", "also from alice")

        alice_msgs = await mod._mailbox_list(mailbox_pool, sender="alice")
        assert len(alice_msgs) == 2
        assert all(m["sender"] == "alice" for m in alice_msgs)

    async def test_list_pagination(self, mailbox_pool):
        """Test 6: mailbox_list supports limit and offset."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        for i in range(5):
            await mod._mailbox_post(mailbox_pool, f"sender{i}", "mcp", f"msg {i}")
            await asyncio.sleep(0.005)

        page1 = await mod._mailbox_list(mailbox_pool, limit=2, offset=0)
        page2 = await mod._mailbox_list(mailbox_pool, limit=2, offset=2)
        page3 = await mod._mailbox_list(mailbox_pool, limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

        # Pages should not overlap
        ids_p1 = {m["id"] for m in page1}
        ids_p2 = {m["id"] for m in page2}
        ids_p3 = {m["id"] for m in page3}
        assert ids_p1.isdisjoint(ids_p2)
        assert ids_p2.isdisjoint(ids_p3)

    async def test_list_empty_mailbox(self, mailbox_pool):
        """Test 7: mailbox_list returns [] for empty mailbox."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        # Ensure table is empty
        await mailbox_pool.execute("DELETE FROM mailbox")
        messages = await mod._mailbox_list(mailbox_pool)
        assert messages == []


class TestMailboxRead:
    """Tests 8-11: mailbox_read full message, auto-read, idempotent, nonexistent."""

    async def test_read_returns_full_message(self, mailbox_pool):
        """Test 8: mailbox_read returns full message body and metadata."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(
            mailbox_pool,
            sender="health-butler",
            sender_channel="mcp",
            body="Daily health report",
            subject="Health Report",
            priority=1,
            metadata='{"report_type": "daily"}',
        )
        msg_id = result["message_id"]

        msg = await mod._mailbox_read(mailbox_pool, msg_id)
        assert msg["id"] == msg_id
        assert msg["sender"] == "health-butler"
        assert msg["sender_channel"] == "mcp"
        assert msg["subject"] == "Health Report"
        assert msg["body"] == {"text": "Daily health report"}
        assert msg["priority"] == 1
        assert msg["metadata"] == {"report_type": "daily"}
        assert msg["created_at"] is not None

    async def test_read_auto_marks_unread_as_read(self, mailbox_pool):
        """Test 9: mailbox_read auto-marks unread messages as read, sets read_at."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(mailbox_pool, "sender", "mcp", "new message")
        msg_id = result["message_id"]

        # Verify initially unread
        row = await mailbox_pool.fetchrow(
            "SELECT status, read_at FROM mailbox WHERE id = $1", uuid.UUID(msg_id)
        )
        assert row["status"] == "unread"
        assert row["read_at"] is None

        # Read the message
        msg = await mod._mailbox_read(mailbox_pool, msg_id)
        assert msg["status"] == "read"
        assert msg["read_at"] is not None

        # Verify in DB
        row = await mailbox_pool.fetchrow(
            "SELECT status, read_at FROM mailbox WHERE id = $1", uuid.UUID(msg_id)
        )
        assert row["status"] == "read"
        assert row["read_at"] is not None

    async def test_read_already_read_stays_read(self, mailbox_pool):
        """Test 10: reading an already-read message is idempotent."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(mailbox_pool, "sender", "mcp", "read me twice")
        msg_id = result["message_id"]

        # First read
        msg1 = await mod._mailbox_read(mailbox_pool, msg_id)
        read_at_1 = msg1["read_at"]

        # Second read
        msg2 = await mod._mailbox_read(mailbox_pool, msg_id)
        assert msg2["status"] == "read"
        assert msg2["read_at"] == read_at_1  # read_at should not change

    async def test_read_nonexistent_message(self, mailbox_pool):
        """Test 11: mailbox_read returns error for nonexistent message_id."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        fake_id = str(uuid.uuid4())
        result = await mod._mailbox_read(mailbox_pool, fake_id)
        assert "error" in result
        assert "not found" in result["error"].lower()


class TestMailboxUpdateStatus:
    """Tests 12-14: status transitions, timestamps, invalid status."""

    async def test_status_transitions(self, mailbox_pool):
        """Test 12: transitions unread->read, read->actioned, actioned->archived."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(mailbox_pool, "sender", "mcp", "transition test")
        msg_id = result["message_id"]

        # unread -> read
        r = await mod._mailbox_update_status(mailbox_pool, msg_id, "read")
        assert r["status"] == "read"

        # read -> actioned
        r = await mod._mailbox_update_status(mailbox_pool, msg_id, "actioned")
        assert r["status"] == "actioned"

        # actioned -> archived
        r = await mod._mailbox_update_status(mailbox_pool, msg_id, "archived")
        assert r["status"] == "archived"

        # Verify in DB
        row = await mailbox_pool.fetchrow(
            "SELECT status FROM mailbox WHERE id = $1", uuid.UUID(msg_id)
        )
        assert row["status"] == "archived"

    async def test_status_sets_timestamps(self, mailbox_pool):
        """Test 13: status changes set read_at and actioned_at timestamps."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(mailbox_pool, "sender", "mcp", "timestamp test")
        msg_id = result["message_id"]

        # Mark as read -> sets read_at
        await mod._mailbox_update_status(mailbox_pool, msg_id, "read")
        row = await mailbox_pool.fetchrow(
            "SELECT read_at, actioned_at FROM mailbox WHERE id = $1", uuid.UUID(msg_id)
        )
        assert row["read_at"] is not None
        assert row["actioned_at"] is None

        # Mark as actioned -> sets actioned_at (read_at preserved)
        read_at_before = row["read_at"]
        await mod._mailbox_update_status(mailbox_pool, msg_id, "actioned")
        row = await mailbox_pool.fetchrow(
            "SELECT read_at, actioned_at FROM mailbox WHERE id = $1", uuid.UUID(msg_id)
        )
        assert row["read_at"] == read_at_before  # unchanged
        assert row["actioned_at"] is not None

    async def test_rejects_invalid_status(self, mailbox_pool):
        """Test 14: mailbox_update_status rejects invalid status values."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()
        result = await mod._mailbox_post(mailbox_pool, "sender", "mcp", "invalid status test")
        msg_id = result["message_id"]

        r = await mod._mailbox_update_status(mailbox_pool, msg_id, "deleted")
        assert "error" in r
        assert "invalid" in r["error"].lower()


class TestMailboxStats:
    """Test 15: mailbox_stats accurate counts."""

    async def test_stats_counts(self, mailbox_pool):
        """Test 15: mailbox_stats returns accurate counts across all statuses."""
        from butlers.modules.mailbox import MailboxModule

        mod = MailboxModule()

        # Start with empty mailbox
        await mailbox_pool.execute("DELETE FROM mailbox")

        # Insert messages in various statuses
        await mod._mailbox_post(mailbox_pool, "a", "mcp", "unread 1")
        await mod._mailbox_post(mailbox_pool, "b", "mcp", "unread 2")
        r3 = await mod._mailbox_post(mailbox_pool, "c", "mcp", "will be read")
        r4 = await mod._mailbox_post(mailbox_pool, "d", "mcp", "will be actioned")
        r5 = await mod._mailbox_post(mailbox_pool, "e", "mcp", "will be archived")

        await mod._mailbox_update_status(mailbox_pool, r3["message_id"], "read")
        await mod._mailbox_update_status(mailbox_pool, r4["message_id"], "actioned")
        await mod._mailbox_update_status(mailbox_pool, r5["message_id"], "archived")

        stats = await mod._mailbox_stats(mailbox_pool)
        assert stats["unread"] == 2
        assert stats["read"] == 1
        assert stats["actioned"] == 1
        assert stats["archived"] == 1
        assert stats["total"] == 5


# ==================================================================
# Tests 16-19: Switchboard post_mail routing
# ==================================================================


class TestPostMail:
    """Tests 16-19: post_mail routing, error handling, identity preservation."""

    async def test_post_mail_routes_to_target(self, switchboard_pool):
        """Test 16: post_mail routes to target butler and returns message_id."""
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

        assert "result" in result
        assert result["result"]["message_id"] == "fake-uuid-123"

        # Verify correct routing
        assert len(call_log) == 1
        assert call_log[0]["endpoint_url"] == "http://localhost:9500/sse"
        assert call_log[0]["tool_name"] == "mailbox_post"
        assert call_log[0]["args"]["sender"] == "relationship"
        assert call_log[0]["args"]["sender_channel"] == "mcp"

    async def test_post_mail_error_butler_not_found(self, switchboard_pool):
        """Test 17: post_mail returns error when target butler not in registry."""
        from butlers.tools.switchboard import post_mail

        await switchboard_pool.execute("DELETE FROM butler_registry")

        result = await post_mail(
            switchboard_pool,
            target_butler="nonexistent",
            sender="system",
            sender_channel="scheduler",
            body="Hello",
        )

        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_post_mail_error_no_mailbox_module(self, switchboard_pool):
        """Test 18: post_mail returns error when target butler lacks mailbox module."""
        from butlers.tools.switchboard import post_mail, register_butler

        await register_butler(
            switchboard_pool,
            "no-mailbox-butler",
            "http://localhost:9600/sse",
            "Butler without mailbox",
            ["email", "telegram"],  # No mailbox module
        )

        result = await post_mail(
            switchboard_pool,
            target_butler="no-mailbox-butler",
            sender="alice",
            sender_channel="telegram",
            body="This should fail",
        )

        assert "error" in result
        assert "mailbox" in result["error"].lower()

    async def test_post_mail_preserves_sender_identity(self, switchboard_pool):
        """Test 19: sender identity preserved in the delivered message args."""
        from butlers.tools.switchboard import post_mail, register_butler

        await register_butler(
            switchboard_pool,
            "target-butler",
            "http://localhost:9700/sse",
            "Target",
            ["mailbox"],
        )

        received_args: dict = {}

        async def capture_call(endpoint_url, tool_name, args):
            received_args.update(args)
            return {"message_id": "captured-uuid"}

        await post_mail(
            switchboard_pool,
            target_butler="target-butler",
            sender="alice",
            sender_channel="telegram",
            body="Hello from Alice via Telegram",
            subject="Hi there",
            priority=1,
            metadata={"correlation_id": "xyz"},
            call_fn=capture_call,
        )

        # Verify sender identity is preserved exactly
        assert received_args["sender"] == "alice"
        assert received_args["sender_channel"] == "telegram"
        assert received_args["body"] == "Hello from Alice via Telegram"
        assert received_args["subject"] == "Hi there"
        assert received_args["priority"] == 1
        assert "correlation_id" in json.loads(received_args["metadata"])
