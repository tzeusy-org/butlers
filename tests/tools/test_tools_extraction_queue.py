"""Tests for butlers.tools.extraction_queue — confirmation queue for low-confidence extractions."""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest

# Skip all tests in this module if Docker is not available
docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def queue_pool(db):
    """extraction_queue from the real switchboard chain (sw_002, collapsed into sw_001)."""
    schemas = {"switchboard": "switchboard"}
    return await db("core", "switchboard", schemas=schemas, schema="switchboard")


async def test_add_list_and_filtering(queue_pool):
    """Add creates pending entries; list filters by status, respects limit and ordering."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_list,
        extraction_queue_resolve,
    )

    await queue_pool.execute("DELETE FROM extraction_queue")

    e1 = await extraction_queue_add(
        queue_pool, "msg1", "interaction", {"k": "v"}, "medium", ttl_days=14
    )
    e2 = await extraction_queue_add(queue_pool, "msg2", "contact", {}, "low")

    assert e1["status"] == "pending" and e1["ttl_days"] == 14
    entries = await extraction_queue_list(queue_pool)
    assert len(entries) == 2 and entries[0]["id"] == e2["id"]  # newest first

    await extraction_queue_resolve(queue_pool, e1["id"], "dismiss")
    assert len(await extraction_queue_list(queue_pool, status="pending")) == 1
    assert len(await extraction_queue_list(queue_pool, status="dismissed")) == 1

    # Add 3 more; limit
    for i in range(3):
        await extraction_queue_add(queue_pool, f"m{i}", "t", {}, "low")
    assert len(await extraction_queue_list(queue_pool, limit=2)) == 2

    with pytest.raises(ValueError, match="Invalid status"):
        await extraction_queue_list(queue_pool, status="bogus")


async def test_resolve_confirm_dismiss_and_errors(queue_pool):
    """Confirm dispatches; dismiss does not; error cases raise correctly."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_resolve,
    )

    await queue_pool.execute("DELETE FROM extraction_queue")

    dispatched = []

    async def mock_dispatch(extraction_type, extraction_data):
        dispatched.append({"type": extraction_type, "data": extraction_data})

    async def failing_dispatch(extraction_type, extraction_data):
        raise ConnectionError("unavailable")

    e1 = await extraction_queue_add(
        queue_pool, "had coffee with Alex", "interaction", {"contact_name": "Alex"}, "medium"
    )
    resolved = await extraction_queue_resolve(
        queue_pool, e1["id"], "confirm", resolved_by="tze", dispatch_fn=mock_dispatch
    )
    assert resolved["status"] == "confirmed" and resolved["resolved_by"] == "tze"
    assert len(dispatched) == 1 and dispatched[0]["data"]["contact_name"] == "Alex"

    e2 = await extraction_queue_add(queue_pool, "msg2", "contact", {}, "low")
    resolved2 = await extraction_queue_resolve(
        queue_pool, e2["id"], "dismiss", dispatch_fn=mock_dispatch
    )
    assert resolved2["status"] == "dismissed" and len(dispatched) == 1  # no new dispatch

    # Dispatch failure still confirms
    e3 = await extraction_queue_add(queue_pool, "msg3", "interaction", {}, "medium")
    resolved3 = await extraction_queue_resolve(
        queue_pool, e3["id"], "confirm", dispatch_fn=failing_dispatch
    )
    assert resolved3["status"] == "confirmed"

    # Error cases
    e4 = await extraction_queue_add(queue_pool, "msg4", "t", {}, "low")
    with pytest.raises(ValueError, match="Invalid action"):
        await extraction_queue_resolve(queue_pool, e4["id"], "invalid_action")
    with pytest.raises(ValueError, match="not found"):
        await extraction_queue_resolve(queue_pool, uuid.uuid4(), "confirm")
    await extraction_queue_resolve(queue_pool, e4["id"], "confirm")
    with pytest.raises(ValueError, match="status is 'confirmed'"):
        await extraction_queue_resolve(queue_pool, e4["id"], "dismiss")


@pytest.mark.pg_clock
async def test_stats_expire_and_lifecycle(queue_pool):
    """Stats counts; expire respects per-entry TTL; pending_count and get work."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_expire,
        extraction_queue_get,
        extraction_queue_pending_count,
        extraction_queue_resolve,
        extraction_queue_stats,
    )

    await queue_pool.execute("DELETE FROM extraction_queue")

    e1 = await extraction_queue_add(queue_pool, "m1", "t", {}, "low")
    e2 = await extraction_queue_add(queue_pool, "m2", "t", {}, "medium")
    await extraction_queue_add(queue_pool, "m3", "t", {}, "low")
    await extraction_queue_resolve(queue_pool, e1["id"], "confirm")
    await extraction_queue_resolve(queue_pool, e2["id"], "dismiss")

    stats = await extraction_queue_stats(queue_pool)
    assert stats == {"pending": 1, "confirmed": 1, "dismissed": 1, "expired": 0}
    assert await extraction_queue_pending_count(queue_pool) == 1

    # Expire: short TTL expires; long TTL does not; resolved not re-expired
    short = await extraction_queue_add(queue_pool, "short ttl", "t", {}, "low", ttl_days=2)
    long = await extraction_queue_add(queue_pool, "long ttl", "t", {}, "low", ttl_days=30)
    five_days_ago = datetime.now(UTC) - timedelta(days=5)
    for eid in (short["id"], long["id"]):
        await queue_pool.execute(
            "UPDATE extraction_queue SET created_at = $1 WHERE id = $2", five_days_ago, eid
        )

    assert await extraction_queue_expire(queue_pool) == 1
    assert (await extraction_queue_get(queue_pool, short["id"]))["status"] == "expired"
    assert (await extraction_queue_get(queue_pool, long["id"]))["status"] == "pending"

    await queue_pool.execute(
        "UPDATE extraction_queue SET created_at = $1 WHERE id = $2", five_days_ago, e1["id"]
    )
    assert await extraction_queue_expire(queue_pool) == 0

    with pytest.raises(ValueError, match="not found"):
        await extraction_queue_get(queue_pool, uuid.uuid4())
