"""Unit tests for GmailPolicyEvaluator — DB-backed priority contact cache.

Covers:
- DB-primary lookup: returns contacts from DB rows
- 15-min TTL: refreshes when cache is expired, skips when fresh
- Fail-open on DB error: retains previous cache
- Empty set on first DB failure (no flat-file fallback)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.gmail_policy import GmailPolicyEvaluator

pytestmark = pytest.mark.unit


def _make_db_row(email: str):
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: email if key == "value" else None)
    return m


def _make_pool(emails: list[str] | None = None, *, raises: Exception | None = None):
    pool = AsyncMock()
    if raises is not None:
        pool.fetch = AsyncMock(side_effect=raises)
    else:
        pool.fetch = AsyncMock(return_value=[_make_db_row(e) for e in (emails or [])])
    return pool


# ---------------------------------------------------------------------------
# DB-primary lookup
# ---------------------------------------------------------------------------


async def test_evaluator_loads_contacts_from_db():
    # Previously flaked on CI runners with low uptime: _cache_loaded_at was
    # initialised to 0.0, so `time.monotonic() - 0.0 < ttl` evaluated False on
    # freshly-booted runners (uptime < TTL seconds), silently skipping the DB
    # refresh and returning an empty frozenset.  Fixed in PR #1800 by
    # initialising _cache_loaded_at to float("-inf") so the cache is always
    # treated as expired on the first call regardless of system uptime.
    pool = _make_pool(["alice@example.com", "bob@example.com"])
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    contacts = await evaluator.get_known_contacts()

    assert "alice@example.com" in contacts
    assert "bob@example.com" in contacts
    snapshot = evaluator.peek_snapshot()
    assert snapshot.state == "loaded" and snapshot.reason == "none"
    assert snapshot.last_success_at is not None and snapshot.generation > 0
    assert snapshot.contacts is contacts
    success = snapshot.last_success_at
    evaluator._cache_loaded_at -= 901
    aged = evaluator.peek_snapshot()
    assert aged.state == "stale" and aged.reason == "ttl_expired"
    assert aged.contacts == contacts and aged.last_success_at == success
    assert pool.fetch.await_count == 1


async def test_evaluator_normalizes_email_addresses():
    pool = _make_pool(["Alice@Example.COM", " BOB@EXAMPLE.COM "])
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    contacts = await evaluator.get_known_contacts()

    assert "alice@example.com" in contacts
    assert "bob@example.com" in contacts


# ---------------------------------------------------------------------------
# TTL behaviour
# ---------------------------------------------------------------------------


async def test_evaluator_does_not_refresh_within_ttl():
    pool = _make_pool(["alice@example.com"])
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    await evaluator.get_known_contacts()  # first load
    await evaluator.get_known_contacts()  # should NOT re-query

    assert pool.fetch.await_count == 1


async def test_evaluator_refreshes_after_ttl_expiry():
    pool = _make_pool(["alice@example.com"])
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=0.0)  # zero TTL → always expired

    await evaluator.get_known_contacts()
    await evaluator.get_known_contacts()

    assert pool.fetch.await_count == 2


# ---------------------------------------------------------------------------
# Fail-open on DB error
# ---------------------------------------------------------------------------


async def test_evaluator_retains_cache_on_db_error():
    """On DB failure after a successful load, the previous cache is retained."""
    pool = AsyncMock()
    # First call succeeds with alice; second call fails.
    pool.fetch = AsyncMock(
        side_effect=[
            [_make_db_row("alice@example.com")],
            RuntimeError("DB connection refused"),
        ]
    )
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=0.0)  # zero TTL → always re-queries

    first = await evaluator.get_known_contacts()
    second = await evaluator.get_known_contacts()

    assert "alice@example.com" in first
    # Cache retained from first successful load
    assert "alice@example.com" in second
    assert evaluator.peek_snapshot().reason == "refresh_failed"
    assert evaluator.peek_snapshot().last_success_at is not None


async def test_evaluator_empty_on_first_db_error():
    """If DB fails on the very first call, return empty set (no flat-file fallback)."""
    pool = _make_pool(raises=RuntimeError("DB unavailable"))
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    contacts = await evaluator.get_known_contacts()

    assert len(contacts) == 0
    assert evaluator.peek_snapshot().state == "failed"
    assert evaluator.peek_snapshot().last_success_at is None
    no_pool = GmailPolicyEvaluator()
    assert await no_pool.get_known_contacts() == frozenset()
    assert no_pool.peek_snapshot().reason == "no_pool"
    empty = GmailPolicyEvaluator(_make_pool([]))
    empty_success = await empty.get_snapshot()
    assert empty_success.state == "loaded" and empty_success.contacts == frozenset()
    assert empty_success.last_success_at is not None


# ---------------------------------------------------------------------------
# is_priority_sender convenience method
# ---------------------------------------------------------------------------


async def test_is_priority_sender_true():
    pool = _make_pool(["alice@example.com"])
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    result = await evaluator.is_priority_sender("alice@example.com")

    assert result is True


async def test_is_priority_sender_false():
    pool = _make_pool(["alice@example.com"])
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    result = await evaluator.is_priority_sender("unknown@example.com")

    assert result is False


async def test_is_priority_sender_normalizes_input():
    pool = _make_pool(["alice@example.com"])
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    # Input with display name and uppercase should still match
    result = await evaluator.is_priority_sender("Alice Smith <ALICE@EXAMPLE.COM>")

    assert result is True


# REQ-ingestion-policy-002 and REQ-ingestion-policy-003: executed local-query/publication
# boundary; required append/registry rollback and refusal admission need actual SQL V3.
async def test_snapshot_refresh_serializes_and_keeps_query_success_separate_from_publication(
    caplog,
):
    """One actual query generation, bounded diagnostics, and both cancellation boundaries."""
    entered, release = asyncio.Event(), asyncio.Event()
    pool = _make_pool()

    async def fetch(*_args):
        entered.set()
        await release.wait()
        return [_make_db_row("private-contact@example.test")]

    pool.fetch.side_effect = fetch
    evaluator = GmailPolicyEvaluator(pool)
    first = asyncio.create_task(evaluator.get_snapshot())
    await entered.wait()
    refreshing = evaluator.peek_snapshot()
    assert refreshing.reason == "refreshing" and refreshing.last_success_at is None
    second = asyncio.create_task(evaluator.get_snapshot())
    release.set()
    one, two = await asyncio.gather(first, second)
    assert one == two and one.state == "loaded"
    assert pool.fetch.await_count == 1
    assert one.last_success_at <= datetime.now(UTC)

    pool.fetch.side_effect = RuntimeError("private-contact@example.test error-tail TOKEN-sentinel")
    evaluator._cache_loaded_at -= 901
    failed = await evaluator.get_snapshot()
    assert failed.contacts == one.contacts and failed.last_success_at == one.last_success_at
    assert failed.generation > one.generation and failed.reason == "refresh_failed"
    assert "private-contact" not in caplog.text and "TOKEN-sentinel" not in caplog.text

    entered.clear()
    release.clear()
    pool.fetch.side_effect = fetch
    unfinished = asyncio.create_task(evaluator.get_snapshot())
    await entered.wait()
    unfinished.cancel()
    with pytest.raises(asyncio.CancelledError):
        await unfinished
    assert evaluator.peek_snapshot().reason == "refresh_cancelled"
    assert evaluator.peek_snapshot().contacts == one.contacts

    pool.fetch.side_effect = None
    pool.fetch.return_value = []
    calls = 0

    async def observer():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await evaluator.get_snapshot(observer)
    successful = evaluator.peek_snapshot()
    assert successful.state == "loaded" and successful.contacts == frozenset()
    assert successful.last_success_at >= one.last_success_at
    evaluator._cache_loaded_at -= 901

    async def failed_publication():
        raise TimeoutError("provider TOKEN-sentinel")

    after_failure = await evaluator.get_snapshot(failed_publication)
    assert after_failure.state == "loaded" and after_failure.reason == "none"
    assert after_failure.last_success_at >= successful.last_success_at
    assert "TOKEN-sentinel" not in caplog.text
