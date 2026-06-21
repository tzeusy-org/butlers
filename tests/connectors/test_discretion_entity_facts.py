"""Tests for ContactWeightResolver entity_facts migration (bu-hjo3i).

Verifies that _query reads from relationship.entity_facts instead of
public.contact_info.

Covers:
- Known owner → tiers.owner weight.
- Inner-circle contact → tiers.inner_circle weight.
- Known non-owner → tiers.known weight.
- No triple found → tiers.unknown weight.
- Unknown channel_type (no predicate) → tiers.unknown weight.
- DB error → tiers.unknown weight (graceful).
- Cache hit avoids second DB call.
- SQL uses relationship.entity_facts, not public.contact_info.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from butlers.connectors.discretion import ContactWeightResolver, WeightTier

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(roles: list[str]) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = MagicMock(
        side_effect=lambda k: {
            "entity_id": None,
            "name": "Test",
            "roles": roles,
        }[k]
    )
    return row


def _make_pool(*, row: MagicMock | None) -> AsyncMock:
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=row)
    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_owner_role_returns_owner_weight() -> None:
    pool = _make_pool(row=_make_row(["owner"]))
    resolver = ContactWeightResolver(pool)
    weight = await resolver.resolve("telegram", "123456789")
    assert weight == WeightTier().owner


async def test_inner_circle_role_returns_inner_circle_weight() -> None:
    pool = _make_pool(row=_make_row(["family"]))
    resolver = ContactWeightResolver(pool)
    weight = await resolver.resolve("telegram", "111")
    assert weight == WeightTier().inner_circle


async def test_close_friends_returns_inner_circle_weight() -> None:
    pool = _make_pool(row=_make_row(["close-friends"]))
    resolver = ContactWeightResolver(pool)
    weight = await resolver.resolve("email", "friend@example.com")
    assert weight == WeightTier().inner_circle


async def test_known_contact_returns_known_weight() -> None:
    pool = _make_pool(row=_make_row(["colleague"]))
    resolver = ContactWeightResolver(pool)
    weight = await resolver.resolve("telegram", "222")
    assert weight == WeightTier().known


async def test_no_entity_found_returns_unknown_weight() -> None:
    pool = _make_pool(row=None)
    resolver = ContactWeightResolver(pool)
    weight = await resolver.resolve("telegram", "999")
    assert weight == WeightTier().unknown


async def test_unknown_channel_type_returns_unknown_weight() -> None:
    """Channel type with no predicate mapping → skip DB, return unknown."""
    pool = _make_pool(row=_make_row(["owner"]))
    resolver = ContactWeightResolver(pool)
    weight = await resolver.resolve("unknown_channel_xyz", "some_value")
    assert weight == WeightTier().unknown
    # Pool should not have been called since no predicate exists for this channel
    pool.fetchrow.assert_not_called()


async def test_db_error_returns_unknown_weight() -> None:
    """DB error in _resolve_entity_by_triple → graceful fallback to unknown."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=Exception("connection refused"))
    resolver = ContactWeightResolver(pool)
    weight = await resolver.resolve("telegram", "444")
    assert weight == WeightTier().unknown


async def test_cache_hit_avoids_second_db_call() -> None:
    """Second call with same key uses cache, no extra DB round-trip."""
    pool = _make_pool(row=_make_row(["owner"]))
    resolver = ContactWeightResolver(pool, cache_ttl_s=60.0)
    w1 = await resolver.resolve("telegram", "555")
    w2 = await resolver.resolve("telegram", "555")
    assert w1 == w2
    # Only one DB call despite two resolve() calls
    assert pool.fetchrow.await_count == 1


async def test_query_uses_entity_facts_not_contact_info() -> None:
    """The underlying SQL must reference relationship.entity_facts, not contact_info."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)

    resolver = ContactWeightResolver(pool)
    await resolver._query("telegram", "777")

    call_args = pool.fetchrow.call_args
    assert call_args is not None
    sql: str = call_args[0][0]
    assert "relationship.entity_facts" in sql
    assert "contact_info" not in sql
