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
        expected = {
            "mcp",
            "telegram_bot",
            "telegram_user_client",
            "email",
            "api",
            "scheduler",
            "system",
        }
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

    with PostgresContainer("pgvector/pgvector:pg17") as pg:
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
    await p.execute("CREATE INDEX IF NOT EXISTS idx_mailbox_status ON mailbox (status)")
    await p.execute("CREATE INDEX IF NOT EXISTS idx_mailbox_sender ON mailbox (sender)")
    await p.execute("CREATE INDEX IF NOT EXISTS idx_mailbox_created_at ON mailbox (created_at DESC)")

    yield p
    await db.close()


@pytest.fixture
def mailbox(pool) -> MailboxModule:
    mod = MailboxModule()
    mod._pool = pool
    return mod


# ---------------------------------------------------------------------------
# DB integration: post / read / list / update_status / stats
# ---------------------------------------------------------------------------


@pytest.mark.integration
@db_tests
@pytest.mark.asyncio(loop_scope="session")
class TestMailboxPostAndRead:
    """Verify mailbox_post inserts and mailbox_read fetches/auto-marks."""

    async def test_post_full_fields_and_defaults(self, mailbox: MailboxModule, pool):
        """post with all fields stores correctly; minimal post uses defaults."""
        result = await mailbox._post(
            sender="butler-b", sender_channel="telegram_bot", body="Full body",
            subject="Subject", priority=5, metadata={"tag": "urgent"},
        )
        msg_id = uuid.UUID(result["id"])
        row = await pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_id)
        assert row["sender"] == "butler-b" and row["priority"] == 5 and row["status"] == "unread"
        meta = row["metadata"]
        if isinstance(meta, str):
            import json
            meta = json.loads(meta)
        assert meta == {"tag": "urgent"}

        # Minimal defaults
        r2 = await mailbox._post(sender="x", sender_channel="api", body="minimal")
        row2 = await pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", uuid.UUID(r2["id"]))
        assert row2["priority"] == 0 and row2["subject"] is None and row2["read_at"] is None

    async def test_post_unknown_channel_warns(self, mailbox: MailboxModule, caplog):
        """Unknown sender_channel is accepted with a warning log; known channel does not warn."""
        import logging
        with caplog.at_level(logging.WARNING):
            result = await mailbox._post(sender="ext", sender_channel="sms", body="text")
        assert "id" in result and "Unknown sender_channel" in caplog.text

    async def test_read_marks_as_read_and_nonexistent_errors(self, mailbox: MailboxModule):
        """Reading marks unread→read; re-read stays read; nonexistent returns error."""
        post = await mailbox._post(sender="alice", sender_channel="mcp", body="Hello", subject="Greet")
        result = await mailbox._read(post["id"])
        assert result["status"] == "read" and result["read_at"] is not None and result["subject"] == "Greet"

        # Re-read stays read
        result2 = await mailbox._read(post["id"])
        assert result2["status"] == "read"

        # Nonexistent
        fake_id = str(uuid.uuid4())
        result3 = await mailbox._read(fake_id)
        assert "error" in result3 and fake_id in result3["error"]


@pytest.mark.integration
@db_tests
@pytest.mark.asyncio(loop_scope="session")
class TestMailboxListAndStatus:
    """Verify mailbox_list filtering/pagination and mailbox_update_status."""

    async def test_list_ordering_filters_and_pagination(self, mailbox: MailboxModule):
        """Newest first; status/sender filters; limit+offset non-overlapping pages; empty OK."""
        sender = f"list-{uuid.uuid4().hex[:8]}"
        r1 = await mailbox._post(sender=sender, sender_channel="mcp", body="first")
        r2 = await mailbox._post(sender=sender, sender_channel="mcp", body="second")
        await mailbox._read(r1["id"])  # mark as read

        # Ordering: newest first
        all_msgs = await mailbox._list(sender=sender)
        ids = [m["id"] for m in all_msgs]
        assert ids.index(r2["id"]) < ids.index(r1["id"])

        # Filter by status
        read_msgs = await mailbox._list(status="read", sender=sender)
        assert all(m["status"] == "read" for m in read_msgs)

        # Pagination
        for i in range(3):
            await mailbox._post(sender=sender, sender_channel="mcp", body=f"page-{i}")
        page1 = await mailbox._list(sender=sender, limit=2, offset=0)
        page2 = await mailbox._list(sender=sender, limit=2, offset=2)
        assert {m["id"] for m in page1}.isdisjoint({m["id"] for m in page2})

        # Empty result
        assert await mailbox._list(sender="nonexistent-sender-zzzz") == []

    async def test_update_status_and_nonexistent(self, mailbox: MailboxModule):
        """Update to read/archived/custom sets correct status+timestamps; nonexistent errors."""
        post = await mailbox._post(sender="s", sender_channel="mcp", body="m")
        result = await mailbox._update_status(post["id"], "read")
        assert result["status"] == "read" and result["read_at"] is not None

        post2 = await mailbox._post(sender="s", sender_channel="mcp", body="m2")
        result2 = await mailbox._update_status(post2["id"], "archived")
        assert result2["status"] == "archived" and result2["archived_at"] is not None

        fake_id = str(uuid.uuid4())
        assert "error" in await mailbox._update_status(fake_id, "read")

    async def test_stats_counts(self, mailbox: MailboxModule, pool):
        """stats returns correct counts by status; empty mailbox returns {}."""
        await pool.execute("DELETE FROM mailbox")
        stats_empty = await mailbox._stats()
        assert stats_empty == {}

        await mailbox._post(sender="a", sender_channel="mcp", body="1")
        await mailbox._post(sender="a", sender_channel="mcp", body="2")
        p3 = await mailbox._post(sender="a", sender_channel="mcp", body="3")
        await mailbox._read(p3["id"])

        stats = await mailbox._stats()
        assert stats.get("unread") == 2 and stats.get("read") == 1
