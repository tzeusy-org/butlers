"""Tests for memory system API endpoints.

Verifies the API contract (status codes, response shapes) for memory
endpoints.  Uses a mocked DatabaseManager so no real database is required.

Issues: butlers-26h.13.1, 13.2
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.memory import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_episode_row(
    *,
    id: str = "ep-001",
    butler: str = "atlas",
    session_id: str | None = "sess-001",
    content: str = "User asked about project status",
    importance: float = 5.0,
    reference_count: int = 0,
    consolidated: bool = False,
    created_at: str = "2025-06-01T12:00:00",
    last_referenced_at: str | None = None,
    expires_at: str | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": id,
        "butler": butler,
        "session_id": session_id,
        "content": content,
        "importance": importance,
        "reference_count": reference_count,
        "consolidated": consolidated,
        "created_at": created_at,
        "last_referenced_at": last_referenced_at,
        "expires_at": expires_at,
        "metadata": metadata or {},
    }


def _make_fact_row(
    *,
    id: str = "fact-001",
    subject: str = "user",
    predicate: str = "prefers",
    content: str = "User prefers dark mode",
    importance: float = 5.0,
    confidence: float = 0.9,
    decay_rate: float = 0.008,
    permanence: str = "standard",
    source_butler: str | None = "atlas",
    source_episode_id: str | None = None,
    supersedes_id: str | None = None,
    validity: str = "active",
    scope: str = "global",
    reference_count: int = 2,
    created_at: str = "2025-06-01T12:00:00",
    last_referenced_at: str | None = None,
    last_confirmed_at: str | None = None,
    tags: list | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": id,
        "subject": subject,
        "predicate": predicate,
        "content": content,
        "importance": importance,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "permanence": permanence,
        "source_butler": source_butler,
        "source_episode_id": source_episode_id,
        "supersedes_id": supersedes_id,
        "validity": validity,
        "scope": scope,
        "reference_count": reference_count,
        "created_at": created_at,
        "last_referenced_at": last_referenced_at,
        "last_confirmed_at": last_confirmed_at,
        "tags": tags or [],
        "metadata": metadata or {},
    }


def _make_rule_row(
    *,
    id: str = "rule-001",
    content: str = "Always greet user by name",
    scope: str = "global",
    maturity: str = "candidate",
    confidence: float = 0.5,
    decay_rate: float = 0.01,
    permanence: str = "standard",
    effectiveness_score: float = 0.0,
    applied_count: int = 0,
    success_count: int = 0,
    harmful_count: int = 0,
    source_episode_id: str | None = None,
    source_butler: str | None = "atlas",
    created_at: str = "2025-06-01T12:00:00",
    last_applied_at: str | None = None,
    last_evaluated_at: str | None = None,
    tags: list | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "id": id,
        "content": content,
        "scope": scope,
        "maturity": maturity,
        "confidence": confidence,
        "decay_rate": decay_rate,
        "permanence": permanence,
        "effectiveness_score": effectiveness_score,
        "applied_count": applied_count,
        "success_count": success_count,
        "harmful_count": harmful_count,
        "source_episode_id": source_episode_id,
        "source_butler": source_butler,
        "created_at": created_at,
        "last_applied_at": last_applied_at,
        "last_evaluated_at": last_evaluated_at,
        "tags": tags or [],
        "metadata": metadata or {},
    }


def _make_pool(
    *,
    fetch_rows: list | None = None,
    fetchval_result: int = 0,
    fetchrow_result: dict | None = None,
    fetchval_side_effect: list | None = None,
    fetch_side_effect: list | None = None,
) -> AsyncMock:
    """Create a mocked asyncpg pool."""
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchval = AsyncMock(return_value=fetchval_result)
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    if fetchval_side_effect is not None:
        pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    if fetch_side_effect is not None:
        pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    return pool


def _app_with_mock_db(
    app: FastAPI,
    *,
    fetch_rows: list | None = None,
    fetchval_result: int = 0,
    fetchrow_result: dict | None = None,
    pool_available: bool = True,
    fetchval_side_effect: list | None = None,
    fetch_side_effect: list | None = None,
    pools_by_name: dict[str, AsyncMock] | None = None,
) -> FastAPI:
    """Wire a FastAPI app with a mocked DatabaseManager.

    Accepts the shared module-scoped ``app`` fixture so that create_app()
    is not called per test.
    """
    mock_pool = _make_pool(
        fetch_rows=fetch_rows,
        fetchval_result=fetchval_result,
        fetchrow_result=fetchrow_result,
        fetchval_side_effect=fetchval_side_effect,
        fetch_side_effect=fetch_side_effect,
    )

    if pools_by_name is None:
        pools_by_name = {"general": mock_pool} if pool_available else {}

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = list(pools_by_name.keys())

    def _pool_lookup(name: str):
        if name not in pools_by_name:
            raise KeyError(f"No pool for butler: {name}")
        return pools_by_name[name]

    mock_db.pool.side_effect = _pool_lookup

    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# GET /api/memory/stats
# ---------------------------------------------------------------------------


class TestMemoryStats:
    async def test_returns_stats_response_structure(self, app):
        """Response must wrap MemoryStats in ApiResponse."""
        _app_with_mock_db(
            app,
            fetchval_side_effect=[10, 3, 20, 15, 2, 5, 2, 2, 1, 0],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/stats")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        data = body["data"]
        assert "total_episodes" in data
        assert "unconsolidated_episodes" in data
        assert "total_facts" in data
        assert "active_facts" in data
        assert "fading_facts" in data
        assert "total_rules" in data
        assert "candidate_rules" in data
        assert "established_rules" in data
        assert "proven_rules" in data
        assert "anti_pattern_rules" in data

    async def test_stats_values_from_db(self, app):
        """Stats should reflect the values from the database."""
        _app_with_mock_db(
            app,
            fetchval_side_effect=[100, 25, 50, 40, 5, 10, 4, 3, 2, 1],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/stats")

        data = resp.json()["data"]
        assert data["total_episodes"] == 100
        assert data["unconsolidated_episodes"] == 25
        assert data["total_facts"] == 50
        assert data["active_facts"] == 40
        assert data["fading_facts"] == 5
        assert data["total_rules"] == 10
        assert data["candidate_rules"] == 4
        assert data["established_rules"] == 3
        assert data["proven_rules"] == 2
        assert data["anti_pattern_rules"] == 1

    async def test_pool_unavailable_returns_zero_stats(self, app):
        """When no memory pools are available, return zeroed stats."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/stats")

        assert resp.status_code == 200
        assert resp.json()["data"] == {
            "total_episodes": 0,
            "unconsolidated_episodes": 0,
            "total_facts": 0,
            "active_facts": 0,
            "fading_facts": 0,
            "total_rules": 0,
            "candidate_rules": 0,
            "established_rules": 0,
            "proven_rules": 0,
            "anti_pattern_rules": 0,
        }

    async def test_aggregates_across_non_dedicated_memory_pools(self, app):
        """Stats fan out across any butler pool exposing memory tables."""
        general_pool = _make_pool(fetchval_side_effect=[10, 2, 5, 4, 1, 3, 2, 1, 0, 0])
        relationship_pool = _make_pool(fetchval_side_effect=[7, 1, 6, 5, 0, 4, 1, 2, 1, 0])
        _app_with_mock_db(
            app, pools_by_name={"general": general_pool, "relationship": relationship_pool}
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/stats")

        data = resp.json()["data"]
        assert data["total_episodes"] == 17
        assert data["unconsolidated_episodes"] == 3
        assert data["total_facts"] == 11
        assert data["active_facts"] == 9
        assert data["total_rules"] == 7


# ---------------------------------------------------------------------------
# GET /api/memory/episodes
# ---------------------------------------------------------------------------


class TestListEpisodes:
    async def test_returns_paginated_response_structure(self, app):
        """Response must have 'data' array and 'meta' with pagination."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/episodes")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_returns_episode_data(self, app):
        """Episodes should be returned with all expected fields."""
        row = _make_episode_row()
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/episodes")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == "ep-001"
        assert data[0]["butler"] == "atlas"
        assert data[0]["content"] == "User asked about project status"

    async def test_filter_params_accepted(self, app):
        """All query filter parameters should be accepted without error."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/memory/episodes",
                params={
                    "butler": "atlas",
                    "consolidated": "false",
                    "since": "2025-01-01",
                    "until": "2025-12-31",
                    "offset": 10,
                    "limit": 25,
                },
            )

        assert resp.status_code == 200

    async def test_empty_results(self, app):
        """When no episodes exist, data should be an empty list."""
        _app_with_mock_db(app, fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/episodes")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_empty_page(self, app):
        """When no memory pools are available, return an empty page."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/episodes")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_aggregates_across_multiple_butlers(self, app):
        """Episodes should merge/sort records from multiple butler pools."""
        general_pool = _make_pool(
            fetch_rows=[_make_episode_row(id="ep-old", created_at="2025-06-01T10:00:00")],
            fetchval_result=1,
        )
        relationship_pool = _make_pool(
            fetch_rows=[_make_episode_row(id="ep-new", created_at="2025-06-01T11:00:00")],
            fetchval_result=1,
        )
        _app_with_mock_db(
            app, pools_by_name={"general": general_pool, "relationship": relationship_pool}
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/episodes")

        body = resp.json()
        assert body["meta"]["total"] == 2
        assert [row["id"] for row in body["data"]] == ["ep-new", "ep-old"]


# ---------------------------------------------------------------------------
# GET /api/memory/facts
# ---------------------------------------------------------------------------


class TestListFacts:
    async def test_returns_paginated_response_structure(self, app):
        """Response must have 'data' array and 'meta' with pagination."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_returns_fact_data(self, app):
        """Facts should be returned with all expected fields."""
        row = _make_fact_row()
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts")

        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == "fact-001"
        assert data[0]["subject"] == "user"
        assert data[0]["predicate"] == "prefers"
        assert data[0]["validity"] == "active"
        assert data[0]["confidence"] == 0.9

    async def test_search_filter_accepted(self, app):
        """Text search via ?q= should be accepted."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts", params={"q": "dark mode"})

        assert resp.status_code == 200

    async def test_all_filters_accepted(self, app):
        """All query filter parameters should be accepted without error."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/memory/facts",
                params={
                    "q": "test",
                    "scope": "global",
                    "validity": "active",
                    "permanence": "standard",
                    "subject": "user",
                    "offset": 0,
                    "limit": 10,
                },
            )

        assert resp.status_code == 200

    async def test_pool_unavailable_returns_empty_page(self, app):
        """When no memory pools are available, return an empty page."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/memory/facts/{fact_id}
# ---------------------------------------------------------------------------


class TestGetFact:
    async def test_returns_fact_detail(self, app):
        """Response should wrap a Fact in ApiResponse envelope."""
        row = _make_fact_row()
        _app_with_mock_db(app, fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts/fact-001")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["id"] == "fact-001"
        assert body["data"]["subject"] == "user"

    async def test_returns_fact_detail_from_non_dedicated_pool(self, app):
        """Fact lookup should fan out across non-memory butler pools."""
        row = _make_fact_row(id="fact-general")
        _app_with_mock_db(
            app,
            pools_by_name={"general": _make_pool(fetchrow_result=row)},
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts/fact-general")

        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == "fact-general"

    async def test_missing_fact_returns_404(self, app):
        """A non-existent fact should return 404."""
        _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts/nonexistent")

        assert resp.status_code == 404

    async def test_pool_unavailable_returns_404(self, app):
        """When no memory pools are available, fact lookup returns 404."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/facts/fact-001")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/memory/rules
# ---------------------------------------------------------------------------


class TestListRules:
    async def test_returns_paginated_response_structure(self, app):
        """Response must have 'data' array and 'meta' with pagination."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/rules")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_returns_rule_data(self, app):
        """Rules should be returned with all expected fields."""
        row = _make_rule_row()
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/rules")

        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == "rule-001"
        assert data[0]["content"] == "Always greet user by name"
        assert data[0]["maturity"] == "candidate"

    async def test_search_filter_accepted(self, app):
        """Text search via ?q= should be accepted."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/rules", params={"q": "greet"})

        assert resp.status_code == 200

    async def test_all_filters_accepted(self, app):
        """All filter parameters accepted."""
        _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/memory/rules",
                params={"q": "test", "scope": "global", "maturity": "proven"},
            )

        assert resp.status_code == 200

    async def test_pool_unavailable_returns_empty_page(self, app):
        """When no memory pools are available, return an empty page."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/rules")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0


# ---------------------------------------------------------------------------
# GET /api/memory/rules/{rule_id}
# ---------------------------------------------------------------------------


class TestGetRule:
    async def test_returns_rule_detail(self, app):
        """Response should wrap a Rule in ApiResponse envelope."""
        row = _make_rule_row()
        _app_with_mock_db(app, fetchrow_result=row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/rules/rule-001")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["id"] == "rule-001"
        assert body["data"]["maturity"] == "candidate"

    async def test_missing_rule_returns_404(self, app):
        """A non-existent rule should return 404."""
        _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/rules/nonexistent")

        assert resp.status_code == 404

    async def test_pool_unavailable_returns_404(self, app):
        """When no memory pools are available, rule lookup returns 404."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/rules/rule-001")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/memory/activity
# ---------------------------------------------------------------------------


class TestMemoryActivity:
    async def test_returns_activity_list(self, app):
        """Response should wrap a list of MemoryActivity in ApiResponse."""
        ep_row = {
            "id": "ep-1",
            "butler": "atlas",
            "content": "Test episode",
            "created_at": "2025-06-02T12:00:00",
        }
        fact_row = {
            "id": "f-1",
            "subject": "user",
            "predicate": "likes",
            "source_butler": "atlas",
            "created_at": "2025-06-02T11:00:00",
        }
        rule_row = {
            "id": "r-1",
            "content": "Be polite",
            "source_butler": "atlas",
            "created_at": "2025-06-02T10:00:00",
        }

        _app_with_mock_db(
            app,
            fetch_side_effect=[[ep_row], [fact_row], [rule_row]],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/activity")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        data = body["data"]
        assert len(data) == 3
        # Should be sorted by created_at descending
        assert data[0]["type"] == "episode"
        assert data[1]["type"] == "fact"
        assert data[2]["type"] == "rule"

    async def test_aggregates_activity_across_multiple_pools(self, app):
        """Activity should fan out across non-dedicated memory pools."""
        general_pool = _make_pool(
            fetch_side_effect=[
                [
                    {
                        "id": "ep-a",
                        "butler": "general",
                        "content": "A",
                        "created_at": "2025-06-02T10:00:00",
                    }
                ],
                [],
                [],
            ]
        )
        relationship_pool = _make_pool(
            fetch_side_effect=[
                [
                    {
                        "id": "ep-b",
                        "butler": "relationship",
                        "content": "B",
                        "created_at": "2025-06-02T11:00:00",
                    }
                ],
                [],
                [],
            ]
        )
        _app_with_mock_db(
            app, pools_by_name={"general": general_pool, "relationship": relationship_pool}
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/activity")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert [item["id"] for item in data] == ["ep-b", "ep-a"]

    async def test_activity_item_fields(self, app):
        """Each activity item should have id, type, summary, butler, created_at."""
        ep_row = {
            "id": "ep-1",
            "butler": "atlas",
            "content": "Hello world",
            "created_at": "2025-06-01T12:00:00",
        }
        _app_with_mock_db(
            app,
            fetch_side_effect=[[ep_row], [], []],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/activity")

        data = resp.json()["data"]
        assert len(data) == 1
        item = data[0]
        assert "id" in item
        assert "type" in item
        assert "summary" in item
        assert "butler" in item
        assert "created_at" in item

    async def test_limit_param_accepted(self, app):
        """The ?limit= query parameter should be accepted."""
        _app_with_mock_db(
            app,
            fetch_side_effect=[[], [], []],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/activity", params={"limit": 10})

        assert resp.status_code == 200

    async def test_empty_results(self, app):
        """When no activity exists, data should be an empty list."""
        _app_with_mock_db(
            app,
            fetch_side_effect=[[], [], []],
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/activity")

        body = resp.json()
        assert body["data"] == []

    async def test_pool_unavailable_returns_empty_activity(self, app):
        """When no memory pools are available, return empty activity."""
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/activity")

        assert resp.status_code == 200
        assert resp.json()["data"] == []


# ---------------------------------------------------------------------------
# Entity row helpers
# ---------------------------------------------------------------------------


def _make_entity_list_row(
    *,
    id: str = "ent-001",
    canonical_name: str = "Alice",
    entity_type: str = "person",
    aliases: list | None = None,
    linked_contact_roles: list | None = None,
    linked_contact_id: str | None = None,
    unidentified: bool = False,
    created_at: str = "2025-06-01T12:00:00",
    updated_at: str = "2025-06-01T12:00:00",
) -> dict:
    """Build a dict mimicking a row returned by list_entities."""
    return {
        "id": id,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "linked_contact_roles": linked_contact_roles or [],
        "linked_contact_id": linked_contact_id,
        "unidentified": unidentified,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _make_entity_detail_row(
    *,
    id: str = "ent-001",
    canonical_name: str = "Alice",
    entity_type: str = "person",
    aliases: list | None = None,
    metadata: dict | None = None,
    linked_contact_id: str | None = None,
    linked_contact_name: str | None = None,
    linked_contact_roles: list | None = None,
    created_at: str = "2025-06-01T12:00:00",
    updated_at: str = "2025-06-01T12:00:00",
) -> dict:
    """Build a dict mimicking a row returned by get_entity."""
    return {
        "id": id,
        "canonical_name": canonical_name,
        "entity_type": entity_type,
        "aliases": aliases or [],
        "metadata": metadata or {},
        "linked_contact_id": linked_contact_id,
        "linked_contact_name": linked_contact_name,
        "linked_contact_roles": linked_contact_roles or [],
        "created_at": created_at,
        "updated_at": updated_at,
    }


# ---------------------------------------------------------------------------
# GET /api/memory/entities
# ---------------------------------------------------------------------------


class TestListEntities:
    async def test_returns_paginated_response_structure(self, app):
        """Response must have 'data' array and 'meta' with pagination."""
        # list_entities queries shared.entities; pool.fetchval for count, pool.fetch
        # for rows, then fans out to memory pools for fact counts.
        _app_with_mock_db(app, fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)

    async def test_returns_entity_data(self, app):
        """Entity list should include expected fields for each entity."""
        row = _make_entity_list_row(canonical_name="Bob", entity_type="person")
        _app_with_mock_db(app, fetch_rows=[row], fetchval_result=1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities")

        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["canonical_name"] == "Bob"
        assert data[0]["entity_type"] == "person"

    async def test_entities_with_any_tenant_id_are_returned(self, app):
        """Entities with tenant_id='owner' (or any other value) must now be visible.

        The query no longer filters by tenant_id so all entities in shared.entities
        are returned regardless of their tenant_id value.
        """
        owner_entity = _make_entity_list_row(id="ent-owner", canonical_name="Owner Person")
        default_entity = _make_entity_list_row(id="ent-default", canonical_name="Default Person")
        _app_with_mock_db(app, fetch_rows=[owner_entity, default_entity], fetchval_result=2)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities")

        data = resp.json()["data"]
        ids = [e["id"] for e in data]
        assert "ent-owner" in ids
        assert "ent-default" in ids

    async def test_search_filter_accepted(self, app):
        """Text search via ?q= should be accepted without error."""
        _app_with_mock_db(app, fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities", params={"q": "alice"})

        assert resp.status_code == 200

    async def test_entity_type_filter_accepted(self, app):
        """Entity type filter via ?entity_type= should be accepted without error."""
        _app_with_mock_db(app, fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities", params={"entity_type": "person"})

        assert resp.status_code == 200

    async def test_pool_unavailable_returns_503(self, app):
        """When no memory pools are available, list_entities raises 503.

        Unlike facts/rules/episodes (which fan out across pools and gracefully
        return empty results), list_entities requires at least one pool to reach
        shared.entities.  _any_pool() raises HTTPException(503) if none exist.
        """
        _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities")

        assert resp.status_code == 503

    async def test_no_tenant_id_filter_in_list_query(self, app):
        """The list_entities handler must NOT restrict results by tenant_id.

        Verify by inspecting the SQL passed to pool.fetchval and pool.fetch:
        neither should contain a tenant_id IN clause.
        """
        pool = _make_pool(fetchval_result=0)
        pools_by_name = {"general": pool}

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/memory/entities")

        # pool.fetchval is the COUNT query; pool.fetch is the SELECT query
        for call in list(pool.fetchval.call_args_list) + list(pool.fetch.call_args_list):
            sql = call.args[0] if call.args else ""
            assert "tenant_id IN" not in sql, (
                f"Unexpected tenant_id filter in entity list query: {sql!r}"
            )


# ---------------------------------------------------------------------------
# GET /api/memory/entities/{entity_id}
# ---------------------------------------------------------------------------


class TestGetEntity:
    async def test_returns_entity_detail(self, app):
        """Response should wrap EntityDetail in ApiResponse envelope."""
        row = _make_entity_detail_row(canonical_name="Carol")
        # fetchrow for entity, fetch for entity_info (info_rows), and two fan-out
        # calls for facts (fetchval + fetch per pool).
        pool = _make_pool(
            fetchrow_result=row,
            fetchval_result=0,
            fetch_rows=[],
        )
        pools_by_name = {"general": pool}

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["canonical_name"] == "Carol"
        assert body["data"]["entity_type"] == "person"

    async def test_entity_with_owner_tenant_id_is_visible(self, app):
        """get_entity must return entities regardless of tenant_id value.

        The query no longer filters on tenant_id so an entity that was created
        with tenant_id='owner' (historical hallucination) is still retrievable.
        """
        row = _make_entity_detail_row(id="ent-owner", canonical_name="The Owner")
        pool = _make_pool(fetchrow_result=row, fetchval_result=0, fetch_rows=[])
        pools_by_name = {"general": pool}

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        assert resp.status_code == 200
        assert resp.json()["data"]["canonical_name"] == "The Owner"

    async def test_missing_entity_returns_404(self, app):
        """A non-existent entity should return 404."""
        pool = _make_pool(fetchrow_result=None)
        pools_by_name = {"general": pool}

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        assert resp.status_code == 404

    async def test_no_tenant_id_filter_in_get_query(self, app):
        """The get_entity handler must NOT restrict results by tenant_id.

        Verify by inspecting the SQL passed to pool.fetchrow: it must not
        contain a tenant_id IN clause.
        """
        row = _make_entity_detail_row()
        pool = _make_pool(fetchrow_result=row, fetchval_result=0, fetch_rows=[])
        pools_by_name = {"general": pool}

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.get("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        for call in pool.fetchrow.call_args_list:
            sql = call.args[0] if call.args else ""
            assert "tenant_id IN" not in sql, (
                f"Unexpected tenant_id filter in get_entity query: {sql!r}"
            )


# ---------------------------------------------------------------------------
# PATCH /api/memory/entities/{entity_id}
# ---------------------------------------------------------------------------


class TestUpdateEntity:
    async def test_updates_entity_successfully(self, app):
        """PATCH /entities/{id} should return updated entity summary."""
        row = {
            "id": "12345678-1234-5678-1234-567812345678",
            "canonical_name": "Updated Name",
            "entity_type": "person",
            "aliases": [],
            "roles": [],
            "created_at": "2025-06-01T12:00:00",
            "updated_at": "2025-06-01T13:00:00",
        }
        pool = _make_pool(fetchrow_result=row)
        pools_by_name = {"general": pool}
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/memory/entities/12345678-1234-5678-1234-567812345678",
                json={"canonical_name": "Updated Name"},
            )

        assert resp.status_code == 200
        assert resp.json()["data"]["canonical_name"] == "Updated Name"

    async def test_missing_entity_returns_404(self, app):
        """PATCH on non-existent entity should return 404."""
        pool = _make_pool(fetchrow_result=None)
        pools_by_name = {"general": pool}
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/memory/entities/12345678-1234-5678-1234-567812345678",
                json={"canonical_name": "Whatever"},
            )

        assert resp.status_code == 404

    async def test_no_tenant_id_filter_in_update_query(self, app):
        """The update_entity handler must NOT restrict results by tenant_id.

        Verify by inspecting the SQL passed to pool.fetchrow: it must not
        contain a tenant_id IN clause.
        """
        row = {
            "id": "12345678-1234-5678-1234-567812345678",
            "canonical_name": "Alice",
            "entity_type": "person",
            "aliases": [],
            "roles": [],
            "created_at": "2025-06-01T12:00:00",
            "updated_at": "2025-06-01T12:00:00",
        }
        pool = _make_pool(fetchrow_result=row)
        pools_by_name = {"general": pool}
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.patch(
                "/api/memory/entities/12345678-1234-5678-1234-567812345678",
                json={"canonical_name": "Alice"},
            )

        for call in pool.fetchrow.call_args_list:
            sql = call.args[0] if call.args else ""
            assert "tenant_id IN" not in sql, (
                f"Unexpected tenant_id filter in update_entity query: {sql!r}"
            )


# ---------------------------------------------------------------------------
# DELETE /api/memory/entities/{entity_id}
# ---------------------------------------------------------------------------


class TestDeleteEntity:
    async def test_deletes_entity_successfully(self, app):
        """DELETE /entities/{id} should return 204 for a valid entity."""
        row = {"id": "12345678-1234-5678-1234-567812345678", "roles": []}
        pool = _make_pool(fetchrow_result=row)
        pools_by_name = {"general": pool}
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        assert resp.status_code == 204

    async def test_owner_entity_cannot_be_deleted(self, app):
        """DELETE on an entity with 'owner' role must return 403."""
        row = {"id": "12345678-1234-5678-1234-567812345678", "roles": ["owner"]}
        pool = _make_pool(fetchrow_result=row)
        pools_by_name = {"general": pool}
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        assert resp.status_code == 403

    async def test_missing_entity_returns_404(self, app):
        """DELETE on non-existent entity should return 404."""
        pool = _make_pool(fetchrow_result=None)
        pools_by_name = {"general": pool}
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        assert resp.status_code == 404

    async def test_no_tenant_id_filter_in_delete_query(self, app):
        """The delete_entity handler must NOT restrict results by tenant_id.

        Verify by inspecting the SQL passed to pool.fetchrow: it must not
        contain a tenant_id IN clause.
        """
        row = {"id": "12345678-1234-5678-1234-567812345678", "roles": []}
        pool = _make_pool(fetchrow_result=row)
        pools_by_name = {"general": pool}
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.butler_names = ["general"]
        mock_db.pool.side_effect = lambda name: pools_by_name[name]
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.delete("/api/memory/entities/12345678-1234-5678-1234-567812345678")

        for call in pool.fetchrow.call_args_list:
            sql = call.args[0] if call.args else ""
            assert "tenant_id IN" not in sql, (
                f"Unexpected tenant_id filter in delete_entity query: {sql!r}"
            )
