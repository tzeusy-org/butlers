"""Tests for the Mailbox module."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from butlers.modules.base import Module
from butlers.modules.mailbox import KNOWN_CHANNELS, MailboxConfig, MailboxModule

# ---------------------------------------------------------------------------
# Module ABC compliance
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleABC:
    """Verify MailboxModule satisfies the Module abstract base class."""

    def test_is_subclass_of_module(self):
        assert issubclass(MailboxModule, Module)

    def test_instantiates(self):
        mod = MailboxModule()
        assert isinstance(mod, Module)

    def test_name(self):
        mod = MailboxModule()
        assert mod.name == "mailbox"

    def test_config_schema(self):
        mod = MailboxModule()
        assert mod.config_schema is MailboxConfig
        assert issubclass(mod.config_schema, BaseModel)

    def test_dependencies_empty(self):
        mod = MailboxModule()
        assert mod.dependencies == []

    def test_migration_revisions(self):
        mod = MailboxModule()
        assert mod.migration_revisions() == "mailbox"


# ---------------------------------------------------------------------------
# MailboxConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMailboxConfig:
    """Verify config schema."""

    def test_defaults(self):
        cfg = MailboxConfig()
        assert isinstance(cfg, BaseModel)

    def test_from_empty_dict(self):
        cfg = MailboxConfig(**{})
        assert isinstance(cfg, BaseModel)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLifecycle:
    """Verify on_startup / on_shutdown lifecycle hooks."""

    async def test_on_startup_stores_pool(self):
        mod = MailboxModule()
        mock_pool = MagicMock()
        await mod.on_startup(config={}, db=mock_pool)
        assert mod._pool is mock_pool

    async def test_on_startup_with_none(self):
        mod = MailboxModule()
        await mod.on_startup(config=None, db=None)
        assert mod._pool is None

    async def test_on_shutdown_clears_pool(self):
        mod = MailboxModule()
        mod._pool = MagicMock()
        await mod.on_shutdown()
        assert mod._pool is None

    async def test_on_shutdown_idempotent(self):
        mod = MailboxModule()
        await mod.on_shutdown()
        await mod.on_shutdown()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterTools:
    """Verify that register_tools creates the expected MCP tools."""

    async def test_registers_five_tools(self):
        mod = MailboxModule()
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn

        await mod.register_tools(mcp=mcp, config=None, db=None)
        assert mcp.tool.call_count == 5

    async def test_registered_tool_names(self):
        mod = MailboxModule()
        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        await mod.register_tools(mcp=mcp, config=None, db=None)

        expected = {
            "mailbox_post",
            "mailbox_list",
            "mailbox_read",
            "mailbox_update_status",
            "mailbox_stats",
        }
        assert set(registered_tools.keys()) == expected

    async def test_registered_tools_are_async(self):
        mod = MailboxModule()
        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool

        await mod.register_tools(mcp=mcp, config=None, db=None)

        for tool_name, tool_fn in registered_tools.items():
            assert asyncio.iscoroutinefunction(tool_fn), f"{tool_name} should be async"

    async def test_register_tools_stores_pool(self):
        mod = MailboxModule()
        mcp = MagicMock()
        mcp.tool.return_value = lambda fn: fn
        mock_pool = MagicMock()

        await mod.register_tools(mcp=mcp, config=None, db=mock_pool)
        assert mod._pool is mock_pool


# ---------------------------------------------------------------------------
# Known channels constant
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKnownChannels:
    """Verify the KNOWN_CHANNELS set."""

    def test_contains_expected_channels(self):
        expected = {"mcp", "telegram", "email", "api", "scheduler", "system"}
        assert KNOWN_CHANNELS == expected


# ---------------------------------------------------------------------------
# No-pool guard
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoPoolGuard:
    """Verify _get_pool raises when module not initialised."""

    def test_raises_runtime_error(self):
        mod = MailboxModule()
        with pytest.raises(RuntimeError, match="not initialised"):
            mod._get_pool()


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryIntegration:
    """Verify MailboxModule works with ModuleRegistry."""

    def test_register_in_registry(self):
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        reg.register(MailboxModule)
        assert "mailbox" in reg.available_modules

    def test_load_from_config(self):
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        reg.register(MailboxModule)
        modules = reg.load_from_config({"mailbox": {}})
        assert len(modules) == 1
        assert modules[0].name == "mailbox"


# ===========================================================================
# DB-backed integration tests (require Docker)
# ===========================================================================

docker_available = shutil.which("docker") is not None
db_tests = pytest.mark.skipif(not docker_available, reason="Docker not available")


def _unique_db_name() -> str:
    return f"test_{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def postgres_container():
    """Start a PostgreSQL container for the test module."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pool(postgres_container):
    """Provision a fresh database with mailbox tables and return a pool."""
    from butlers.db import Database

    db = Database(
        db_name=_unique_db_name(),
        host=postgres_container.get_container_host_ip(),
        port=int(postgres_container.get_exposed_port(5432)),
        user=postgres_container.username,
        password=postgres_container.password,
        min_pool_size=1,
        max_pool_size=3,
    )
    await db.provision()
    p = await db.connect()

    # Create the mailbox table (mirrors the Alembic migration)
    await p.execute("""
        CREATE TABLE IF NOT EXISTS mailbox (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sender TEXT NOT NULL,
            sender_channel TEXT NOT NULL,
            subject TEXT,
            body TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'unread',
            metadata JSONB NOT NULL DEFAULT '{}',
            read_at TIMESTAMPTZ,
            archived_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_mailbox_status ON mailbox (status)
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_mailbox_sender ON mailbox (sender)
    """)
    await p.execute("""
        CREATE INDEX IF NOT EXISTS idx_mailbox_created_at ON mailbox (created_at DESC)
    """)

    yield p
    await db.close()


@pytest.fixture
def mailbox(pool) -> MailboxModule:
    """Create a MailboxModule wired to the test pool."""
    mod = MailboxModule()
    mod._pool = pool
    return mod


# ---------------------------------------------------------------------------
# mailbox_post
# ---------------------------------------------------------------------------


@pytest.mark.integration
@db_tests
@pytest.mark.asyncio(loop_scope="session")
class TestMailboxPost:
    """Verify mailbox_post inserts messages correctly."""

    async def test_post_returns_uuid(self, mailbox: MailboxModule):
        result = await mailbox._post(
            sender="butler-a",
            sender_channel="mcp",
            body="Hello from butler-a",
        )
        assert "id" in result
        # Should be a valid UUID
        uuid.UUID(result["id"])

    async def test_post_returns_created_at(self, mailbox: MailboxModule):
        result = await mailbox._post(
            sender="butler-a",
            sender_channel="mcp",
            body="Test message",
        )
        assert "created_at" in result

    async def test_post_with_all_fields(self, mailbox: MailboxModule, pool):
        result = await mailbox._post(
            sender="butler-b",
            sender_channel="telegram",
            body="Full message body",
            subject="Important Subject",
            priority=5,
            metadata={"tag": "urgent", "ref": 42},
        )
        msg_id = uuid.UUID(result["id"])
        row = await pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_id)
        assert row is not None
        assert row["sender"] == "butler-b"
        assert row["sender_channel"] == "telegram"
        assert row["subject"] == "Important Subject"
        assert row["body"] == "Full message body"
        assert row["priority"] == 5
        assert row["status"] == "unread"
        meta = row["metadata"]
        if isinstance(meta, str):
            import json

            meta = json.loads(meta)
        assert meta == {"tag": "urgent", "ref": 42}

    async def test_post_defaults(self, mailbox: MailboxModule, pool):
        """Post with minimal fields gets correct defaults."""
        result = await mailbox._post(sender="x", sender_channel="api", body="minimal")
        msg_id = uuid.UUID(result["id"])
        row = await pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_id)
        assert row["priority"] == 0
        assert row["status"] == "unread"
        assert row["subject"] is None
        meta = row["metadata"]
        if isinstance(meta, str):
            import json

            meta = json.loads(meta)
        assert meta == {}
        assert row["read_at"] is None
        assert row["archived_at"] is None

    async def test_post_unknown_channel_accepted(self, mailbox: MailboxModule, caplog):
        """Unknown sender_channel is accepted with a warning log."""
        import logging

        with caplog.at_level(logging.WARNING):
            result = await mailbox._post(sender="ext", sender_channel="sms", body="text")
        assert "id" in result
        assert "Unknown sender_channel" in caplog.text

    async def test_post_known_channel_no_warning(self, mailbox: MailboxModule, caplog):
        """Known sender_channel produces no warning."""
        import logging

        with caplog.at_level(logging.WARNING):
            await mailbox._post(sender="ext", sender_channel="email", body="text")
        assert "Unknown sender_channel" not in caplog.text


# ---------------------------------------------------------------------------
# mailbox_read
# ---------------------------------------------------------------------------


@pytest.mark.integration
@db_tests
@pytest.mark.asyncio(loop_scope="session")
class TestMailboxRead:
    """Verify mailbox_read fetches and auto-marks messages."""

    async def test_read_returns_full_message(self, mailbox: MailboxModule):
        post = await mailbox._post(
            sender="alice",
            sender_channel="mcp",
            body="Hello",
            subject="Greetings",
        )
        result = await mailbox._read(post["id"])
        assert result["id"] == post["id"]
        assert result["sender"] == "alice"
        assert result["sender_channel"] == "mcp"
        assert result["body"] == "Hello"
        assert result["subject"] == "Greetings"

    async def test_read_auto_marks_unread_as_read(self, mailbox: MailboxModule, pool):
        """Reading an unread message sets status to 'read' and read_at."""
        post = await mailbox._post(sender="bob", sender_channel="system", body="msg")
        result = await mailbox._read(post["id"])
        assert result["status"] == "read"
        assert result["read_at"] is not None

        # Verify in DB
        row = await pool.fetchrow(
            "SELECT status, read_at FROM mailbox WHERE id = $1",
            uuid.UUID(post["id"]),
        )
        assert row["status"] == "read"
        assert row["read_at"] is not None

    async def test_read_already_read_no_change(self, mailbox: MailboxModule):
        """Reading an already-read message does not alter its status."""
        post = await mailbox._post(sender="carol", sender_channel="api", body="msg")
        # First read marks as read
        await mailbox._read(post["id"])
        # Second read should return same status
        result = await mailbox._read(post["id"])
        assert result["status"] == "read"

    async def test_read_nonexistent(self, mailbox: MailboxModule):
        """Reading a non-existent message returns an error dict."""
        fake_id = str(uuid.uuid4())
        result = await mailbox._read(fake_id)
        assert "error" in result
        assert fake_id in result["error"]


# ---------------------------------------------------------------------------
# mailbox_list
# ---------------------------------------------------------------------------


@pytest.mark.integration
@db_tests
@pytest.mark.asyncio(loop_scope="session")
class TestMailboxList:
    """Verify mailbox_list filtering and pagination."""

    async def test_list_returns_messages(self, mailbox: MailboxModule):
        await mailbox._post(sender="a", sender_channel="mcp", body="m1")
        await mailbox._post(sender="b", sender_channel="mcp", body="m2")
        result = await mailbox._list()
        assert len(result) >= 2

    async def test_list_ordered_by_created_at_desc(self, mailbox: MailboxModule):
        """Messages are returned newest first."""
        r1 = await mailbox._post(sender="a", sender_channel="mcp", body="first")
        r2 = await mailbox._post(sender="a", sender_channel="mcp", body="second")

        result = await mailbox._list(sender="a")
        # Most recent should be first
        ids = [m["id"] for m in result]
        assert ids.index(r2["id"]) < ids.index(r1["id"])

    async def test_list_filter_by_status(self, mailbox: MailboxModule):
        post = await mailbox._post(sender="filter-test", sender_channel="mcp", body="unread msg")
        # Mark one as read
        await mailbox._read(post["id"])

        result = await mailbox._list(status="read", sender="filter-test")
        assert all(m["status"] == "read" for m in result)
        assert any(m["id"] == post["id"] for m in result)

    async def test_list_filter_by_sender(self, mailbox: MailboxModule):
        await mailbox._post(sender="unique-sender-xyz", sender_channel="mcp", body="x")
        result = await mailbox._list(sender="unique-sender-xyz")
        assert len(result) >= 1
        assert all(m["sender"] == "unique-sender-xyz" for m in result)

    async def test_list_pagination_limit(self, mailbox: MailboxModule):
        # Insert a few messages with a unique sender for isolation
        sender = f"page-{uuid.uuid4().hex[:8]}"
        for i in range(5):
            await mailbox._post(sender=sender, sender_channel="mcp", body=f"msg-{i}")

        result = await mailbox._list(sender=sender, limit=3)
        assert len(result) == 3

    async def test_list_pagination_offset(self, mailbox: MailboxModule):
        sender = f"off-{uuid.uuid4().hex[:8]}"
        for i in range(5):
            await mailbox._post(sender=sender, sender_channel="mcp", body=f"msg-{i}")

        page1 = await mailbox._list(sender=sender, limit=2, offset=0)
        page2 = await mailbox._list(sender=sender, limit=2, offset=2)
        ids1 = {m["id"] for m in page1}
        ids2 = {m["id"] for m in page2}
        # Pages should not overlap
        assert ids1.isdisjoint(ids2)

    async def test_list_combined_filters(self, mailbox: MailboxModule):
        sender = f"combo-{uuid.uuid4().hex[:8]}"
        p1 = await mailbox._post(sender=sender, sender_channel="mcp", body="a")
        await mailbox._post(sender=sender, sender_channel="mcp", body="b")
        # Read one
        await mailbox._read(p1["id"])

        result = await mailbox._list(status="read", sender=sender)
        assert len(result) == 1
        assert result[0]["id"] == p1["id"]

    async def test_list_empty_result(self, mailbox: MailboxModule):
        result = await mailbox._list(sender="nonexistent-sender-zzzz")
        assert result == []


# ---------------------------------------------------------------------------
# mailbox_update_status
# ---------------------------------------------------------------------------


@pytest.mark.integration
@db_tests
@pytest.mark.asyncio(loop_scope="session")
class TestMailboxUpdateStatus:
    """Verify mailbox_update_status changes status and timestamps."""

    async def test_update_to_read(self, mailbox: MailboxModule):
        post = await mailbox._post(sender="s", sender_channel="mcp", body="m")
        result = await mailbox._update_status(post["id"], "read")
        assert result["status"] == "read"
        assert result["read_at"] is not None

    async def test_update_to_archived(self, mailbox: MailboxModule):
        post = await mailbox._post(sender="s", sender_channel="mcp", body="m")
        result = await mailbox._update_status(post["id"], "archived")
        assert result["status"] == "archived"
        assert result["archived_at"] is not None

    async def test_update_to_custom_status(self, mailbox: MailboxModule):
        post = await mailbox._post(sender="s", sender_channel="mcp", body="m")
        result = await mailbox._update_status(post["id"], "flagged")
        assert result["status"] == "flagged"

    async def test_update_sets_updated_at(self, mailbox: MailboxModule, pool):
        post = await mailbox._post(sender="s", sender_channel="mcp", body="m")
        await pool.fetchrow(
            "SELECT updated_at FROM mailbox WHERE id = $1",
            uuid.UUID(post["id"]),
        )

        import asyncio

        await asyncio.sleep(0.05)  # Ensure time difference

        result = await mailbox._update_status(post["id"], "read")
        # updated_at should be set
        assert result["updated_at"] is not None

    async def test_update_nonexistent(self, mailbox: MailboxModule):
        fake_id = str(uuid.uuid4())
        result = await mailbox._update_status(fake_id, "read")
        assert "error" in result


# ---------------------------------------------------------------------------
# mailbox_stats
# ---------------------------------------------------------------------------


@pytest.mark.integration
@db_tests
@pytest.mark.asyncio(loop_scope="session")
class TestMailboxStats:
    """Verify mailbox_stats returns correct counts."""

    async def test_stats_returns_counts(self, mailbox: MailboxModule, pool):
        # Clean slate — use a transaction to isolate
        # First, clear any existing messages
        await pool.execute("DELETE FROM mailbox")

        # Insert messages with known statuses
        await mailbox._post(sender="a", sender_channel="mcp", body="1")
        await mailbox._post(sender="a", sender_channel="mcp", body="2")
        p3 = await mailbox._post(sender="a", sender_channel="mcp", body="3")

        # Mark one as read
        await mailbox._read(p3["id"])

        stats = await mailbox._stats()
        assert stats.get("unread") == 2
        assert stats.get("read") == 1

    async def test_stats_empty_mailbox(self, mailbox: MailboxModule, pool):
        await pool.execute("DELETE FROM mailbox")
        stats = await mailbox._stats()
        assert stats == {}

    async def test_stats_after_status_update(self, mailbox: MailboxModule, pool):
        await pool.execute("DELETE FROM mailbox")

        p1 = await mailbox._post(sender="a", sender_channel="mcp", body="1")
        p2 = await mailbox._post(sender="a", sender_channel="mcp", body="2")

        await mailbox._update_status(p1["id"], "archived")
        await mailbox._update_status(p2["id"], "archived")

        stats = await mailbox._stats()
        assert stats.get("archived") == 2
        assert stats.get("unread", 0) == 0
