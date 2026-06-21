"""Tests for GET /api/butlers/{name}/memory/stats.

Scenarios verified (per spec T.6):
- Success path: butler with memory data returns correct counts + 24h deltas.
- Per-butler scoping: querying butler A doesn't return butler B's counts.
- 24h delta: rows older than 24h are excluded from the *_24h fields.
- Graceful empty: butler exists but no memory tables → all zeros, 200.
- Butler not found → 404.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool_with_counts(
    *,
    total_episodes: int = 0,
    episodes_24h: int = 0,
    total_facts: int = 0,
    facts_24h: int = 0,
    total_entities: int = 0,
    entities_24h: int = 0,
    total_rules: int = 0,
    rules_24h: int = 0,
) -> AsyncMock:
    """Return a mock asyncpg pool that returns the given counts for fetchrow calls.

    The optimized implementation uses a single fetchrow per table with
    COUNT(*) FILTER (...) to fetch total and 24h counts in one round-trip.
    SQL fragments identify which table is being queried.
    """
    pool = AsyncMock()

    # Map SQL table fragment → (total, recent) tuple.
    row_map = {
        "FROM episodes": {"total": total_episodes, "recent": episodes_24h},
        "FROM facts": {"total": total_facts, "recent": facts_24h},
        "FROM public.entities": {"total": total_entities, "recent": entities_24h},
        "FROM rules": {"total": total_rules, "recent": rules_24h},
    }

    async def _fetchrow(sql: str, *_args):
        # Match on the most specific substring first (longer keys win).
        for fragment in sorted(row_map, key=len, reverse=True):
            if fragment in sql:
                return row_map[fragment]
        return {"total": 0, "recent": 0}

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    return pool


def _make_app_with_butler(butler_name: str, pool: AsyncMock) -> object:
    """Wire a fresh app with a mock DB that has exactly one butler pool."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = [butler_name]
    mock_db.pool.return_value = pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _make_app_missing_butler(butler_names: list[str]) -> object:
    """Wire a fresh app where the requested butler is not in butler_names."""
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = butler_names

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _make_app_no_memory_tables(butler_name: str) -> object:
    """Wire a fresh app where the butler exists but fetchrow raises for every query."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=Exception("relation does not exist"))

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = [butler_name]
    mock_db.pool.return_value = pool

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_memory_stats_success_path() -> None:
    """Butler with memory data returns correct counts and 24h deltas."""
    pool = _make_pool_with_counts(
        total_episodes=42,
        episodes_24h=5,
        total_facts=100,
        facts_24h=10,
        total_entities=15,
        entities_24h=2,
        total_rules=8,
        rules_24h=1,
    )
    app = _make_app_with_butler("relationship", pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/relationship/memory/stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_episodes"] == 42
    assert data["episodes_24h"] == 5
    assert data["total_facts"] == 100
    assert data["facts_24h"] == 10
    assert data["total_entities"] == 15
    assert data["entities_24h"] == 2
    assert data["total_rules"] == 8
    assert data["rules_24h"] == 1


async def test_memory_stats_per_butler_scoping() -> None:
    """Querying butler A uses butler A's pool, not butler B's."""
    pool_a = _make_pool_with_counts(total_episodes=10, episodes_24h=3)
    pool_b = _make_pool_with_counts(total_episodes=99, episodes_24h=99)

    def _pool_selector(name: str) -> AsyncMock:
        return pool_a if name == "butler-a" else pool_b

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["butler-a", "butler-b"]
    mock_db.pool.side_effect = _pool_selector

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/butler-a/memory/stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    # Should have butler-a's counts, not butler-b's
    assert data["total_episodes"] == 10
    assert data["episodes_24h"] == 3


async def test_memory_stats_24h_delta_exclusion() -> None:
    """Rows older than 24h are excluded from *_24h fields (returned as 0)."""
    pool = _make_pool_with_counts(
        total_episodes=50,
        episodes_24h=0,  # none in last 24h
        total_facts=30,
        facts_24h=0,
        total_entities=5,
        entities_24h=0,
        total_rules=3,
        rules_24h=0,
    )
    app = _make_app_with_butler("atlas", pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/memory/stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_episodes"] == 50
    assert data["episodes_24h"] == 0
    assert data["total_facts"] == 30
    assert data["facts_24h"] == 0
    assert data["entities_24h"] == 0
    assert data["rules_24h"] == 0


async def test_memory_stats_graceful_empty_no_tables() -> None:
    """Butler exists but has no memory tables → HTTP 200 with all zeros."""
    app = _make_app_no_memory_tables("general")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/general/memory/stats")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_episodes"] == 0
    assert data["episodes_24h"] == 0
    assert data["total_facts"] == 0
    assert data["facts_24h"] == 0
    assert data["total_entities"] == 0
    assert data["entities_24h"] == 0
    assert data["total_rules"] == 0
    assert data["rules_24h"] == 0


async def test_memory_stats_butler_not_found_returns_404() -> None:
    """Requesting stats for an unknown butler returns 404."""
    app = _make_app_missing_butler(["atlas", "relationship"])

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/nonexistent/memory/stats")

    assert resp.status_code == 404


async def test_memory_stats_envelope_and_schema() -> None:
    """Response is wrapped in the ApiResponse envelope and exposes all 8 stat fields."""
    pool = _make_pool_with_counts(total_episodes=7)
    app = _make_app_with_butler("atlas", pool)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/memory/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    data = body["data"]
    assert data["total_episodes"] == 7
    expected_fields = {
        "total_episodes",
        "episodes_24h",
        "total_facts",
        "facts_24h",
        "total_entities",
        "entities_24h",
        "total_rules",
        "rules_24h",
    }
    assert set(data.keys()) >= expected_fields
