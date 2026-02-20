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
async def pool(provisioned_postgres_pool):
    """Provision a fresh database with extraction_queue table and return a pool."""
    async with provisioned_postgres_pool() as p:
        # Create extraction_queue table (mirrors Alembic switchboard/002 migration)
        await p.execute("""
            CREATE TABLE IF NOT EXISTS extraction_queue (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                source_message TEXT NOT NULL,
                extraction_type VARCHAR(100) NOT NULL,
                extraction_data JSONB NOT NULL DEFAULT '{}',
                confidence VARCHAR(20) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                ttl_days INTEGER NOT NULL DEFAULT 7,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                resolved_at TIMESTAMPTZ,
                resolved_by VARCHAR(100)
            )
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_extraction_queue_status
                ON extraction_queue (status)
        """)
        await p.execute("""
            CREATE INDEX IF NOT EXISTS idx_extraction_queue_created_at
                ON extraction_queue (created_at)
        """)

        yield p


# ------------------------------------------------------------------
# extraction_queue_add
# ------------------------------------------------------------------


async def test_add_creates_pending_entry(pool):
    """Adding an extraction creates a pending queue entry."""
    from butlers.tools.extraction_queue import extraction_queue_add

    entry = await extraction_queue_add(
        pool,
        source_message="might grab dinner with Alex sometime",
        extraction_type="interaction",
        extraction_data={"contact_name": "Alex", "interaction_type": "dinner"},
        confidence="medium",
    )

    assert entry["source_message"] == "might grab dinner with Alex sometime"
    assert entry["extraction_type"] == "interaction"
    assert entry["extraction_data"] == {"contact_name": "Alex", "interaction_type": "dinner"}
    assert entry["confidence"] == "medium"
    assert entry["status"] == "pending"
    assert entry["ttl_days"] == 7
    assert entry["resolved_at"] is None
    assert entry["resolved_by"] is None
    assert entry["id"] is not None
    assert entry["created_at"] is not None


async def test_add_custom_ttl(pool):
    """Adding with a custom TTL stores the value."""
    from butlers.tools.extraction_queue import extraction_queue_add

    entry = await extraction_queue_add(
        pool,
        source_message="maybe see Bob next month",
        extraction_type="interaction",
        extraction_data={"contact_name": "Bob"},
        confidence="low",
        ttl_days=14,
    )
    assert entry["ttl_days"] == 14


async def test_add_multiple_entries(pool):
    """Adding multiple entries creates distinct queue items."""
    from butlers.tools.extraction_queue import extraction_queue_add

    e1 = await extraction_queue_add(
        pool,
        source_message="msg1",
        extraction_type="contact",
        extraction_data={"name": "Alice"},
        confidence="low",
    )
    e2 = await extraction_queue_add(
        pool,
        source_message="msg2",
        extraction_type="date",
        extraction_data={"label": "birthday"},
        confidence="medium",
    )
    assert e1["id"] != e2["id"]


# ------------------------------------------------------------------
# extraction_queue_list
# ------------------------------------------------------------------


async def test_list_returns_all(pool):
    """Listing without filter returns all entries."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_list

    await pool.execute("DELETE FROM extraction_queue")

    await extraction_queue_add(pool, "m1", "t1", {}, "low")
    await extraction_queue_add(pool, "m2", "t2", {}, "medium")

    entries = await extraction_queue_list(pool)
    assert len(entries) == 2


async def test_list_filter_by_status(pool):
    """Listing with status filter returns only matching entries."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_list,
        extraction_queue_resolve,
    )

    await pool.execute("DELETE FROM extraction_queue")

    e1 = await extraction_queue_add(pool, "m1", "t1", {}, "low")
    await extraction_queue_add(pool, "m2", "t2", {}, "medium")

    await extraction_queue_resolve(pool, e1["id"], "dismiss")

    pending = await extraction_queue_list(pool, status="pending")
    dismissed = await extraction_queue_list(pool, status="dismissed")

    assert len(pending) == 1
    assert len(dismissed) == 1
    assert dismissed[0]["id"] == e1["id"]


async def test_list_invalid_status_raises(pool):
    """Listing with an invalid status raises ValueError."""
    from butlers.tools.extraction_queue import extraction_queue_list

    with pytest.raises(ValueError, match="Invalid status"):
        await extraction_queue_list(pool, status="bogus")


async def test_list_respects_limit(pool):
    """Listing with a limit returns at most that many entries."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_list

    await pool.execute("DELETE FROM extraction_queue")

    for i in range(5):
        await extraction_queue_add(pool, f"m{i}", "t", {}, "low")

    entries = await extraction_queue_list(pool, limit=3)
    assert len(entries) == 3


async def test_list_empty(pool):
    """Listing an empty queue returns an empty list."""
    from butlers.tools.extraction_queue import extraction_queue_list

    await pool.execute("DELETE FROM extraction_queue")
    entries = await extraction_queue_list(pool)
    assert entries == []


async def test_list_ordered_newest_first(pool):
    """Listing returns entries ordered by creation time, newest first."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_list

    await pool.execute("DELETE FROM extraction_queue")

    e1 = await extraction_queue_add(pool, "first", "t", {}, "low")
    e2 = await extraction_queue_add(pool, "second", "t", {}, "low")

    entries = await extraction_queue_list(pool)
    assert entries[0]["id"] == e2["id"]
    assert entries[1]["id"] == e1["id"]


# ------------------------------------------------------------------
# extraction_queue_resolve — confirm
# ------------------------------------------------------------------


async def test_confirm_changes_status(pool):
    """Confirming an entry sets status to 'confirmed' and records resolver."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_resolve

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "msg", "interaction", {"key": "val"}, "medium")
    resolved = await extraction_queue_resolve(pool, entry["id"], "confirm", resolved_by="tze")

    assert resolved["status"] == "confirmed"
    assert resolved["resolved_by"] == "tze"
    assert resolved["resolved_at"] is not None


async def test_confirm_dispatches_to_relationship_butler(pool):
    """Confirming an entry calls the dispatch function with extraction details."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_resolve

    await pool.execute("DELETE FROM extraction_queue")

    dispatched = []

    async def mock_dispatch(extraction_type, extraction_data):
        dispatched.append({"type": extraction_type, "data": extraction_data})

    entry = await extraction_queue_add(
        pool,
        "had coffee with Alex",
        "interaction",
        {"contact_name": "Alex", "type": "coffee"},
        "medium",
    )
    await extraction_queue_resolve(pool, entry["id"], "confirm", dispatch_fn=mock_dispatch)

    assert len(dispatched) == 1
    assert dispatched[0]["type"] == "interaction"
    assert dispatched[0]["data"]["contact_name"] == "Alex"


async def test_confirm_without_dispatch_fn_succeeds(pool):
    """Confirming without a dispatch_fn still succeeds (just updates status)."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_resolve

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "msg", "interaction", {}, "low")
    resolved = await extraction_queue_resolve(pool, entry["id"], "confirm")

    assert resolved["status"] == "confirmed"


async def test_confirm_dispatch_failure_still_confirms(pool):
    """If the dispatch function raises, the entry is still confirmed."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_get,
        extraction_queue_resolve,
    )

    await pool.execute("DELETE FROM extraction_queue")

    async def failing_dispatch(extraction_type, extraction_data):
        raise ConnectionError("Relationship butler unavailable")

    entry = await extraction_queue_add(pool, "msg", "interaction", {}, "medium")
    resolved = await extraction_queue_resolve(
        pool, entry["id"], "confirm", dispatch_fn=failing_dispatch
    )

    # Status should still be confirmed even though dispatch failed
    assert resolved["status"] == "confirmed"
    fetched = await extraction_queue_get(pool, entry["id"])
    assert fetched["status"] == "confirmed"


# ------------------------------------------------------------------
# extraction_queue_resolve — dismiss
# ------------------------------------------------------------------


async def test_dismiss_changes_status(pool):
    """Dismissing an entry sets status to 'dismissed'."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_resolve

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "msg", "contact", {}, "low")
    resolved = await extraction_queue_resolve(pool, entry["id"], "dismiss")

    assert resolved["status"] == "dismissed"
    assert resolved["resolved_at"] is not None
    assert resolved["resolved_by"] == "user"


async def test_dismiss_does_not_dispatch(pool):
    """Dismissing does not call the dispatch function."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_resolve

    await pool.execute("DELETE FROM extraction_queue")

    dispatched = []

    async def mock_dispatch(extraction_type, extraction_data):
        dispatched.append(True)

    entry = await extraction_queue_add(pool, "msg", "interaction", {}, "low")
    await extraction_queue_resolve(pool, entry["id"], "dismiss", dispatch_fn=mock_dispatch)

    assert len(dispatched) == 0


# ------------------------------------------------------------------
# extraction_queue_resolve — error cases
# ------------------------------------------------------------------


async def test_resolve_invalid_action_raises(pool):
    """Resolving with an invalid action raises ValueError."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_resolve

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "msg", "t", {}, "low")
    with pytest.raises(ValueError, match="Invalid action"):
        await extraction_queue_resolve(pool, entry["id"], "invalid_action")


async def test_resolve_nonexistent_entry_raises(pool):
    """Resolving a non-existent entry raises ValueError."""
    from butlers.tools.extraction_queue import extraction_queue_resolve

    with pytest.raises(ValueError, match="not found"):
        await extraction_queue_resolve(pool, uuid.uuid4(), "confirm")


async def test_resolve_already_resolved_raises(pool):
    """Resolving an already-resolved entry raises ValueError."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_resolve

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "msg", "t", {}, "low")
    await extraction_queue_resolve(pool, entry["id"], "confirm")

    with pytest.raises(ValueError, match="status is 'confirmed'"):
        await extraction_queue_resolve(pool, entry["id"], "dismiss")


# ------------------------------------------------------------------
# extraction_queue_stats
# ------------------------------------------------------------------


async def test_stats_empty_queue(pool):
    """Stats on an empty queue returns zeros for all statuses."""
    from butlers.tools.extraction_queue import extraction_queue_stats

    await pool.execute("DELETE FROM extraction_queue")

    stats = await extraction_queue_stats(pool)
    assert stats == {"confirmed": 0, "dismissed": 0, "expired": 0, "pending": 0}


async def test_stats_counts_by_status(pool):
    """Stats returns correct counts per status."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_resolve,
        extraction_queue_stats,
    )

    await pool.execute("DELETE FROM extraction_queue")

    e1 = await extraction_queue_add(pool, "m1", "t", {}, "low")
    e2 = await extraction_queue_add(pool, "m2", "t", {}, "medium")
    await extraction_queue_add(pool, "m3", "t", {}, "low")

    await extraction_queue_resolve(pool, e1["id"], "confirm")
    await extraction_queue_resolve(pool, e2["id"], "dismiss")

    stats = await extraction_queue_stats(pool)
    assert stats["pending"] == 1
    assert stats["confirmed"] == 1
    assert stats["dismissed"] == 1
    assert stats["expired"] == 0


# ------------------------------------------------------------------
# extraction_queue_expire
# ------------------------------------------------------------------


async def test_expire_old_entries(pool):
    """Entries older than their TTL are expired."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_expire,
        extraction_queue_get,
    )

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "old msg", "t", {}, "low", ttl_days=3)

    # Backdate the entry to 5 days ago
    await pool.execute(
        "UPDATE extraction_queue SET created_at = $1 WHERE id = $2",
        datetime.now(UTC) - timedelta(days=5),
        entry["id"],
    )

    expired_count = await extraction_queue_expire(pool)
    assert expired_count == 1

    fetched = await extraction_queue_get(pool, entry["id"])
    assert fetched["status"] == "expired"
    assert fetched["resolved_by"] == "auto-expiry"
    assert fetched["resolved_at"] is not None


async def test_expire_does_not_touch_recent(pool):
    """Entries within their TTL are not expired."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_expire,
        extraction_queue_get,
    )

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "recent msg", "t", {}, "low", ttl_days=7)

    expired_count = await extraction_queue_expire(pool)
    assert expired_count == 0

    fetched = await extraction_queue_get(pool, entry["id"])
    assert fetched["status"] == "pending"


async def test_expire_does_not_touch_resolved(pool):
    """Already resolved entries are not expired even if old."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_expire,
        extraction_queue_resolve,
        extraction_queue_stats,
    )

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "old confirmed", "t", {}, "low", ttl_days=1)
    await extraction_queue_resolve(pool, entry["id"], "confirm")

    # Backdate
    await pool.execute(
        "UPDATE extraction_queue SET created_at = $1 WHERE id = $2",
        datetime.now(UTC) - timedelta(days=10),
        entry["id"],
    )

    expired_count = await extraction_queue_expire(pool)
    assert expired_count == 0

    stats = await extraction_queue_stats(pool)
    assert stats["confirmed"] == 1
    assert stats["expired"] == 0


async def test_expire_respects_per_entry_ttl(pool):
    """Each entry's ttl_days is used individually."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_expire,
        extraction_queue_get,
    )

    await pool.execute("DELETE FROM extraction_queue")

    # Short TTL entry — will expire
    short = await extraction_queue_add(pool, "short ttl", "t", {}, "low", ttl_days=2)
    # Long TTL entry — should not expire
    long = await extraction_queue_add(pool, "long ttl", "t", {}, "low", ttl_days=30)

    # Backdate both to 5 days ago
    five_days_ago = datetime.now(UTC) - timedelta(days=5)
    await pool.execute(
        "UPDATE extraction_queue SET created_at = $1 WHERE id = $2",
        five_days_ago,
        short["id"],
    )
    await pool.execute(
        "UPDATE extraction_queue SET created_at = $1 WHERE id = $2",
        five_days_ago,
        long["id"],
    )

    expired_count = await extraction_queue_expire(pool)
    assert expired_count == 1

    short_fetched = await extraction_queue_get(pool, short["id"])
    assert short_fetched["status"] == "expired"

    long_fetched = await extraction_queue_get(pool, long["id"])
    assert long_fetched["status"] == "pending"


async def test_expire_with_custom_now(pool):
    """expire accepts a custom 'now' for deterministic testing."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_expire,
        extraction_queue_get,
    )

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "msg", "t", {}, "low", ttl_days=3)

    # Use a future 'now' that's past the TTL
    future_now = datetime.now(UTC) + timedelta(days=10)
    expired_count = await extraction_queue_expire(pool, now=future_now)
    assert expired_count == 1

    fetched = await extraction_queue_get(pool, entry["id"])
    assert fetched["status"] == "expired"


# ------------------------------------------------------------------
# extraction_queue_get
# ------------------------------------------------------------------


async def test_get_existing_entry(pool):
    """Getting an existing entry returns it."""
    from butlers.tools.extraction_queue import extraction_queue_add, extraction_queue_get

    await pool.execute("DELETE FROM extraction_queue")

    entry = await extraction_queue_add(pool, "msg", "interaction", {"k": "v"}, "medium")
    fetched = await extraction_queue_get(pool, entry["id"])

    assert fetched["id"] == entry["id"]
    assert fetched["source_message"] == "msg"
    assert fetched["extraction_data"] == {"k": "v"}


async def test_get_nonexistent_raises(pool):
    """Getting a non-existent entry raises ValueError."""
    from butlers.tools.extraction_queue import extraction_queue_get

    with pytest.raises(ValueError, match="not found"):
        await extraction_queue_get(pool, uuid.uuid4())


# ------------------------------------------------------------------
# extraction_queue_pending_count
# ------------------------------------------------------------------


async def test_pending_count_empty(pool):
    """Pending count on empty queue returns 0."""
    from butlers.tools.extraction_queue import extraction_queue_pending_count

    await pool.execute("DELETE FROM extraction_queue")
    count = await extraction_queue_pending_count(pool)
    assert count == 0


async def test_pending_count_only_counts_pending(pool):
    """Pending count excludes resolved entries."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_pending_count,
        extraction_queue_resolve,
    )

    await pool.execute("DELETE FROM extraction_queue")

    await extraction_queue_add(pool, "m1", "t", {}, "low")
    await extraction_queue_add(pool, "m2", "t", {}, "low")
    e3 = await extraction_queue_add(pool, "m3", "t", {}, "low")
    await extraction_queue_resolve(pool, e3["id"], "dismiss")

    count = await extraction_queue_pending_count(pool)
    assert count == 2


# ------------------------------------------------------------------
# Full lifecycle: add -> list -> confirm -> stats
# ------------------------------------------------------------------


async def test_full_lifecycle(pool):
    """End-to-end: add entries, list pending, confirm one, dismiss one, check stats."""
    from butlers.tools.extraction_queue import (
        extraction_queue_add,
        extraction_queue_list,
        extraction_queue_pending_count,
        extraction_queue_resolve,
        extraction_queue_stats,
    )

    await pool.execute("DELETE FROM extraction_queue")

    # Add three entries
    e1 = await extraction_queue_add(
        pool,
        "maybe dinner with Alex",
        "interaction",
        {"contact_name": "Alex", "type": "dinner"},
        "medium",
    )
    e2 = await extraction_queue_add(
        pool,
        "I think Sarah's birthday is in March",
        "date",
        {"contact_name": "Sarah", "label": "birthday", "month": 3},
        "low",
    )
    await extraction_queue_add(
        pool,
        "met someone named Jordan",
        "contact",
        {"name": "Jordan"},
        "low",
    )

    # All should be pending
    pending = await extraction_queue_list(pool, status="pending")
    assert len(pending) == 3
    assert await extraction_queue_pending_count(pool) == 3

    # Confirm one (user decided they did have dinner with Alex)
    dispatched = []

    async def mock_dispatch(ext_type, ext_data):
        dispatched.append({"type": ext_type, "data": ext_data})

    resolved1 = await extraction_queue_resolve(
        pool,
        e1["id"],
        "confirm",
        resolved_by="tze",
        dispatch_fn=mock_dispatch,
    )
    assert resolved1["status"] == "confirmed"
    assert len(dispatched) == 1
    assert dispatched[0]["data"]["contact_name"] == "Alex"

    # Dismiss one (Sarah's birthday wasn't real)
    resolved2 = await extraction_queue_resolve(pool, e2["id"], "dismiss")
    assert resolved2["status"] == "dismissed"

    # Check stats
    stats = await extraction_queue_stats(pool)
    assert stats["pending"] == 1
    assert stats["confirmed"] == 1
    assert stats["dismissed"] == 1
    assert stats["expired"] == 0

    # Only one pending
    assert await extraction_queue_pending_count(pool) == 1
