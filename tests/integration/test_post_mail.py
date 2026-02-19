"""Tests for butlers.tools.switchboard.post_mail — cross-butler mail delivery."""

from __future__ import annotations

import json
import shutil

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with switchboard tables and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create switchboard tables (mirrors Alembic switchboard migration)
        await p.execute("""
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
            )
        """)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS routing_log (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_butler TEXT NOT NULL,
                target_butler TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                duration_ms INTEGER,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)

        yield p


# ------------------------------------------------------------------
# post_mail — target butler not found
# ------------------------------------------------------------------


async def test_post_mail_target_not_found(pool):
    """post_mail returns error when target butler does not exist."""
    from butlers.tools.switchboard import post_mail

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    result = await post_mail(
        pool,
        target_butler="nonexistent",
        sender="alice",
        sender_channel="mcp",
        body="Hello!",
    )

    assert "error" in result
    assert "not found" in result["error"]


async def test_post_mail_target_not_found_logs_routing(pool):
    """post_mail logs a routing failure when target butler is not found."""
    from butlers.tools.switchboard import post_mail

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await post_mail(
        pool,
        target_butler="ghost",
        sender="alice",
        sender_channel="mcp",
        body="Hello!",
    )

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'ghost'")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["tool_name"] == "mailbox_post"
    assert "not found" in rows[0]["error"].lower()


# ------------------------------------------------------------------
# post_mail — mailbox module not enabled
# ------------------------------------------------------------------


async def test_post_mail_mailbox_not_enabled(pool):
    """post_mail returns error when target butler lacks the mailbox module."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    # Register a butler WITHOUT the mailbox module
    await register_butler(pool, "bob", "http://localhost:40200/sse", "Bob butler", ["email"])

    result = await post_mail(
        pool,
        target_butler="bob",
        sender="alice",
        sender_channel="mcp",
        body="Hey Bob",
    )

    assert "error" in result
    assert "mailbox module" in result["error"].lower()


async def test_post_mail_mailbox_not_enabled_logs_routing(pool):
    """post_mail logs a routing failure when mailbox module is not enabled."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await register_butler(pool, "nomb", "http://localhost:40200/sse", modules=["telegram"])

    await post_mail(
        pool,
        target_butler="nomb",
        sender="alice",
        sender_channel="mcp",
        body="test",
    )

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'nomb'")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert "mailbox" in rows[0]["error"].lower()


# ------------------------------------------------------------------
# post_mail — success
# ------------------------------------------------------------------


async def test_post_mail_success_returns_message_id(pool):
    """post_mail returns message_id on successful delivery."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await register_butler(pool, "target", "http://localhost:8300/sse", "Target butler", ["mailbox"])

    async def mock_call(endpoint_url, tool_name, args):
        assert tool_name == "mailbox_post"
        return {"message_id": "msg-abc-123"}

    result = await post_mail(
        pool,
        target_butler="target",
        sender="sender_bot",
        sender_channel="mcp",
        body="Important message",
        call_fn=mock_call,
    )

    assert "message_id" in result
    assert result["message_id"] == "msg-abc-123"


async def test_post_mail_success_logs_routing(pool):
    """Successful post_mail logs a routing entry with success=True."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await register_butler(pool, "logged", "http://localhost:8400/sse", modules=["mailbox"])

    async def mock_call(endpoint_url, tool_name, args):
        return {"message_id": "msg-xyz"}

    await post_mail(
        pool,
        target_butler="logged",
        sender="sender_bot",
        sender_channel="telegram",
        body="Hi",
        call_fn=mock_call,
    )

    rows = await pool.fetch(
        "SELECT * FROM routing_log WHERE target_butler = 'logged' AND tool_name = 'mailbox_post'"
    )
    assert len(rows) == 1
    assert rows[0]["success"] is True
    assert rows[0]["source_butler"] == "sender_bot"


# ------------------------------------------------------------------
# post_mail — sender identity preserved
# ------------------------------------------------------------------


async def test_post_mail_preserves_sender_identity(pool):
    """post_mail passes sender and sender_channel through to mailbox_post args."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")

    await register_butler(pool, "rcv", "http://localhost:8500/sse", modules=["mailbox"])

    captured_args = {}

    async def capture_call(endpoint_url, tool_name, args):
        captured_args.update(args)
        return {"message_id": "msg-001"}

    await post_mail(
        pool,
        target_butler="rcv",
        sender="health-butler",
        sender_channel="mcp",
        body="Check-in",
        call_fn=capture_call,
    )

    assert captured_args["sender"] == "health-butler"
    assert captured_args["sender_channel"] == "mcp"
    assert captured_args["body"] == "Check-in"


# ------------------------------------------------------------------
# post_mail — optional parameters
# ------------------------------------------------------------------


async def test_post_mail_passes_optional_fields(pool):
    """post_mail forwards subject, priority, and metadata when provided."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")

    await register_butler(pool, "full", "http://localhost:8600/sse", modules=["mailbox"])

    captured_args = {}

    async def capture_call(endpoint_url, tool_name, args):
        captured_args.update(args)
        return {"message_id": "msg-full"}

    await post_mail(
        pool,
        target_butler="full",
        sender="alice",
        sender_channel="email",
        body="Detailed message",
        subject="Urgent",
        priority=0,
        metadata={"thread_id": "t-99"},
        call_fn=capture_call,
    )

    assert captured_args["subject"] == "Urgent"
    assert captured_args["priority"] == 0
    assert json.loads(captured_args["metadata"]) == {"thread_id": "t-99"}


async def test_post_mail_omits_optional_fields_when_not_provided(pool):
    """post_mail does not include optional fields in args when not set."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")

    await register_butler(pool, "minimal", "http://localhost:8700/sse", modules=["mailbox"])

    captured_args = {}

    async def capture_call(endpoint_url, tool_name, args):
        captured_args.update(args)
        return {"message_id": "msg-min"}

    await post_mail(
        pool,
        target_butler="minimal",
        sender="bob",
        sender_channel="mcp",
        body="Just a message",
        call_fn=capture_call,
    )

    assert "subject" not in captured_args
    assert "priority" not in captured_args
    assert "metadata" not in captured_args


# ------------------------------------------------------------------
# post_mail — route failure (tool call exception)
# ------------------------------------------------------------------


async def test_post_mail_route_failure_returns_error(pool):
    """post_mail returns error when the routed tool call fails."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await register_butler(pool, "broken", "http://localhost:8800/sse", modules=["mailbox"])

    async def failing_call(endpoint_url, tool_name, args):
        raise ConnectionError("Connection refused")

    result = await post_mail(
        pool,
        target_butler="broken",
        sender="alice",
        sender_channel="mcp",
        body="Will fail",
        call_fn=failing_call,
    )

    assert "error" in result
    assert "ConnectionError" in result["error"]


async def test_post_mail_route_failure_logs_routing(pool):
    """post_mail logs routing failure when the tool call raises."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await register_butler(pool, "errlog", "http://localhost:8900/sse", modules=["mailbox"])

    async def failing_call(endpoint_url, tool_name, args):
        raise RuntimeError("kaboom")

    await post_mail(
        pool,
        target_butler="errlog",
        sender="alice",
        sender_channel="mcp",
        body="Will fail",
        call_fn=failing_call,
    )

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'errlog'")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert "kaboom" in rows[0]["error"]


# ------------------------------------------------------------------
# post_mail — message_id extraction from non-dict result
# ------------------------------------------------------------------


async def test_post_mail_string_result_as_message_id(pool):
    """post_mail wraps a plain string result as message_id."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")

    await register_butler(pool, "strres", "http://localhost:9000/sse", modules=["mailbox"])

    async def string_call(endpoint_url, tool_name, args):
        return "msg-plain-string"

    result = await post_mail(
        pool,
        target_butler="strres",
        sender="alice",
        sender_channel="mcp",
        body="test",
        call_fn=string_call,
    )

    assert "message_id" in result
    assert result["message_id"] == "msg-plain-string"
