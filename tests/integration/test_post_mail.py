"""Tests for butlers.tools.switchboard.post_mail — cross-butler mail delivery."""

from __future__ import annotations

import json
import shutil

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with switchboard tables and return a pool."""
    async with provisioned_postgres_pool() as p:
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
                registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_type TEXT NOT NULL DEFAULT 'butler'
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
                thread_id TEXT,
                source_channel TEXT,
                contact_id UUID,
                entity_id UUID,
                sender_roles TEXT[],
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        yield p


# ------------------------------------------------------------------
# Error paths: target not found / mailbox not enabled
# ------------------------------------------------------------------


async def test_post_mail_error_not_found_and_logs(pool):
    """post_mail returns error and logs failure when target butler does not exist."""
    from butlers.tools.switchboard import post_mail

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    result = await post_mail(
        pool, target_butler="nonexistent", sender="alice", sender_channel="mcp", body="Hello!"
    )
    assert "error" in result and "not found" in result["error"]

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'nonexistent'")
    assert (
        len(rows) == 1 and rows[0]["success"] is False and "not found" in rows[0]["error"].lower()
    )


async def test_post_mail_mailbox_not_enabled_and_logs(pool):
    """post_mail returns error and logs failure when mailbox module is not enabled."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await pool.execute("DELETE FROM routing_log")

    await register_butler(pool, "nomb", "http://localhost:41200/sse", modules=["telegram"])
    result = await post_mail(
        pool, target_butler="nomb", sender="alice", sender_channel="mcp", body="test"
    )
    assert "error" in result and "mailbox module" in result["error"].lower()

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'nomb'")
    assert len(rows) == 1 and rows[0]["success"] is False


# ------------------------------------------------------------------
# Success: message_id, routing log, sender identity preserved
# ------------------------------------------------------------------


async def test_post_mail_success_returns_message_id_and_logs(pool):
    """post_mail returns message_id and logs success=True on successful delivery."""
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
        body="Important",
        call_fn=mock_call,
    )
    assert result["message_id"] == "msg-abc-123"

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'target'")
    assert (
        len(rows) == 1 and rows[0]["success"] is True and rows[0]["source_butler"] == "sender_bot"
    )


async def test_post_mail_preserves_sender_identity_and_optional_fields(pool):
    """post_mail forwards sender/channel/body/subject/priority/metadata; omits unset optionals."""
    from butlers.tools.switchboard import post_mail, register_butler

    await pool.execute("DELETE FROM butler_registry")
    await register_butler(pool, "rcv", "http://localhost:8500/sse", modules=["mailbox"])

    captured: dict = {}

    async def capture_call(endpoint_url, tool_name, args):
        captured.update(args)
        return {"message_id": "msg-001"}

    # Full optional fields
    await post_mail(
        pool,
        target_butler="rcv",
        sender="health-butler",
        sender_channel="mcp",
        body="Check-in",
        subject="Urgent",
        priority=0,
        metadata={"thread_id": "t-99"},
        call_fn=capture_call,
    )
    assert captured["sender"] == "health-butler" and captured["body"] == "Check-in"
    assert captured["subject"] == "Urgent" and json.loads(captured["metadata"]) == {
        "thread_id": "t-99"
    }

    # No optionals
    captured.clear()
    await register_butler(pool, "minimal", "http://localhost:8700/sse", modules=["mailbox"])
    await post_mail(
        pool,
        target_butler="minimal",
        sender="bob",
        sender_channel="mcp",
        body="minimal",
        call_fn=capture_call,
    )
    assert "subject" not in captured and "priority" not in captured


# ------------------------------------------------------------------
# Route failure and non-dict result
# ------------------------------------------------------------------


async def test_post_mail_route_failure_and_logs(pool):
    """post_mail returns error and logs failure when the routed tool call raises."""
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
    assert "error" in result and "ConnectionError" in result["error"]

    rows = await pool.fetch("SELECT * FROM routing_log WHERE target_butler = 'broken'")
    assert len(rows) == 1 and rows[0]["success"] is False


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
    assert result["message_id"] == "msg-plain-string"
