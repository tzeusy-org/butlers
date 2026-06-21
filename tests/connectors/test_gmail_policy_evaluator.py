"""Unit tests for GmailPolicyEvaluator — DB-backed priority contact cache.

Covers:
- DB-primary lookup: returns contacts from DB rows
- 15-min TTL: refreshes when cache is expired, skips when fresh
- Fail-open on DB error: retains previous cache
- Empty set on first DB failure (no flat-file fallback)
"""

from __future__ import annotations

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


async def test_evaluator_empty_on_first_db_error():
    """If DB fails on the very first call, return empty set (no flat-file fallback)."""
    pool = _make_pool(raises=RuntimeError("DB unavailable"))
    evaluator = GmailPolicyEvaluator(db_pool=pool, ttl=900)

    contacts = await evaluator.get_known_contacts()

    assert len(contacts) == 0


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
