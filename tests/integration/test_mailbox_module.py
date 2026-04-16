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
class TestModuleABCAndConfig:
    """MailboxModule satisfies Module ABC, config, lifecycle, registration."""

    def test_module_contract_and_config(self):
        mod = MailboxModule()
        assert issubclass(MailboxModule, Module) and isinstance(mod, Module)
        assert mod.name == "mailbox" and mod.config_schema is MailboxConfig
        assert mod.dependencies == [] and mod.migration_revisions() == "mailbox"
        assert isinstance(MailboxConfig(), BaseModel)

    async def test_lifecycle_startup_shutdown(self):
        mod = MailboxModule()
        mock_pool = MagicMock()
        await mod.on_startup(config={}, db=mock_pool)
        assert mod._pool is mock_pool
        await mod.on_shutdown()
        assert mod._pool is None
        await mod.on_shutdown()  # idempotent

    async def test_tool_registration_and_names(self):
        mod = MailboxModule()
        mcp = MagicMock()
        registered_tools: dict[str, Any] = {}

        def capture_tool():
            def decorator(fn):
                registered_tools[fn.__name__] = fn
                return fn

            return decorator

        mcp.tool.side_effect = capture_tool
        await mod.register_tools(mcp=mcp, config=None, db=None, butler_name="test-butler")

        expected = {
            "mailbox_post",
            "mailbox_list",
            "mailbox_read",
            "mailbox_update_status",
            "mailbox_stats",
        }
        assert set(registered_tools.keys()) == expected
        for fn in registered_tools.values():
            assert asyncio.iscoroutinefunction(fn)

    def test_known_channels_and_no_pool_guard(self):
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

        mod = MailboxModule()
        with pytest.raises(RuntimeError, match="not initialised"):
            mod._get_pool()

    def test_registry_integration(self):
        from butlers.modules.registry import ModuleRegistry

        reg = ModuleRegistry()
        reg.register(MailboxModule)
        assert "mailbox" in reg.available_modules
        modules = reg.load_from_config({"mailbox": {}})
        assert len(modules) == 1 and modules[0].name == "mailbox"


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
    await p.execute(
        "CREATE INDEX IF NOT EXISTS idx_mailbox_created_at ON mailbox (created_at DESC)"
    )

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
            sender="butler-b",
            sender_channel="telegram_bot",
            body="Full body",
            subject="Subject",
            priority=5,
            metadata={"tag": "urgent"},
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
        post = await mailbox._post(
            sender="alice", sender_channel="mcp", body="Hello", subject="Greet"
        )
        result = await mailbox._read(post["id"])
        assert result["status"] == "read" and result["read_at"] is not None
        assert result["subject"] == "Greet"

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
