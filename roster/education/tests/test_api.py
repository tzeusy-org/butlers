"""Tests for education butler API endpoints.

Verifies the API contract (status codes, response shapes) for education
endpoints. Uses a mocked DatabaseManager so no real database is required.

Issue: butlers-2kmd.11
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Mock Record helper
# ---------------------------------------------------------------------------


class _MockRecord(Mapping):
    """Minimal asyncpg.Record-like Mapping object backed by a dict.

    Must extend Mapping so that dict(record) works correctly — Python's
    dict() constructor uses the Mapping protocol (keys() + __getitem__).
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)


# ---------------------------------------------------------------------------
# Fixtures: sample data
# ---------------------------------------------------------------------------

_MAP_ID = str(uuid.uuid4())
_NODE_ID = str(uuid.uuid4())
_NODE_ID2 = str(uuid.uuid4())
_NOW = datetime.now(UTC).isoformat()
_TODAY = date.today().isoformat()


def _mind_map_record(
    *,
    map_id: str = _MAP_ID,
    title: str = "Python",
    status: str = "active",
) -> dict:
    return {
        "id": uuid.UUID(map_id),
        "title": title,
        "root_node_id": None,
        "status": status,
        "metadata": {},
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


def _node_record(
    *,
    node_id: str = _NODE_ID,
    map_id: str = _MAP_ID,
    label: str = "Variables",
    mastery_status: str = "unseen",
) -> dict:
    return {
        "id": uuid.UUID(node_id),
        "mind_map_id": uuid.UUID(map_id),
        "label": label,
        "description": None,
        "depth": 0,
        "mastery_score": 0.0,
        "mastery_status": mastery_status,
        "ease_factor": 2.5,
        "repetitions": 0,
        "next_review_at": None,
        "last_reviewed_at": None,
        "effort_minutes": None,
        "metadata": {},
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }


def _quiz_response_record(
    *,
    node_id: str = _NODE_ID,
    map_id: str = _MAP_ID,
) -> dict:
    return {
        "id": uuid.uuid4(),
        "node_id": uuid.UUID(node_id),
        "mind_map_id": uuid.UUID(map_id),
        "question_text": "What is a variable?",
        "user_answer": "A container for data",
        "quality": 4,
        "response_type": "review",
        "session_id": None,
        "responded_at": datetime.now(UTC),
    }


def _analytics_snapshot_record(
    *,
    map_id: str = _MAP_ID,
) -> dict:
    return {
        "id": uuid.uuid4(),
        "mind_map_id": uuid.UUID(map_id),
        "snapshot_date": date.today(),
        "metrics": {
            "total_nodes": 10,
            "mastered_nodes": 3,
            "mastery_pct": 0.3,
            "avg_ease_factor": 2.5,
            "retention_rate_7d": 0.8,
            "retention_rate_30d": 0.75,
            "velocity_nodes_per_week": 1.5,
            "estimated_completion_days": 14,
            "struggling_nodes": [],
            "strongest_subtree": None,
            "total_quiz_responses": 15,
            "avg_quality_score": 3.5,
            "sessions_this_period": 5,
            "time_of_day_distribution": {"morning": 3, "afternoon": 5, "evening": 7},
        },
        "created_at": datetime.now(UTC),
    }


# ---------------------------------------------------------------------------
# App builder helpers
# ---------------------------------------------------------------------------


def _app_with_mock_pool(
    mock_pool: AsyncMock,
    pool_available: bool = True,
):
    """Build a FastAPI test app with the education pool mocked."""
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: education")

    app = create_app()

    # Override the dependency for the dynamically-loaded education router
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "education" and hasattr(router_module, "_get_db_manager"):
            app.dependency_overrides[router_module._get_db_manager] = lambda: mock_db
            break

    return app


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps
# ---------------------------------------------------------------------------


class TestListMindMaps:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        mock_pool = AsyncMock()
        # mind_map_list calls pool.fetch()
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/mind-maps")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_empty_results(self):
        """When no mind maps exist, data should be an empty list."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/mind-maps")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_status_filter_accepted(self):
        """The ?status= query parameter must not cause an error."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/mind-maps", params={"status": "active"})

        assert resp.status_code == 200

    async def test_pagination_params_accepted(self):
        """The ?offset= and ?limit= query parameters must be accepted."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/mind-maps", params={"offset": 0, "limit": 5})

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["limit"] == 5
        assert body["meta"]["offset"] == 0

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/mind-maps")

        assert resp.status_code == 503

    async def test_returns_mind_map_data(self):
        """When mind maps exist, they should appear in data with correct fields."""
        record = _MockRecord(_mind_map_record())
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[record])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/mind-maps")

        body = resp.json()
        assert body["meta"]["total"] == 1
        assert len(body["data"]) == 1
        item = body["data"][0]
        assert item["title"] == "Python"
        assert item["status"] == "active"
        assert "id" in item
        assert "created_at" in item

    async def test_pagination_slices_correctly(self):
        """Pagination offset/limit should slice the result list."""
        records = [
            _MockRecord(_mind_map_record(map_id=str(uuid.uuid4()), title=f"Topic {i}"))
            for i in range(5)
        ]
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=records)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/mind-maps", params={"offset": 2, "limit": 2})

        body = resp.json()
        assert body["meta"]["total"] == 5
        assert len(body["data"]) == 2
        assert body["meta"]["offset"] == 2


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}
# ---------------------------------------------------------------------------


class TestGetMindMap:
    async def test_returns_404_for_missing_map(self):
        """Non-existent mind map ID should return 404."""
        mock_pool = AsyncMock()
        # mind_map_get calls pool.fetchrow() — return None for not found
        mock_pool.fetchrow = AsyncMock(return_value=None)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{uuid.uuid4()}")

        assert resp.status_code == 404

    async def test_returns_mind_map_with_dag(self):
        """Existing mind map should return full mind map with nodes and edges."""
        map_record = _MockRecord(_mind_map_record())
        node_record = _MockRecord(_node_record())

        mock_pool = AsyncMock()

        async def _fetchrow(sql, *args):
            return map_record

        async def _fetch(sql, *args):
            # Edge query also contains "mind_map_nodes" via JOIN — check edges first
            if "mind_map_edges" in sql:
                return []
            if "mind_map_nodes" in sql:
                return [node_record]
            return []

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_pool.fetch = AsyncMock(side_effect=_fetch)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Python"
        assert "nodes" in body
        assert "edges" in body
        assert isinstance(body["nodes"], list)
        assert isinstance(body["edges"], list)
        assert len(body["nodes"]) == 1
        assert body["nodes"][0]["label"] == "Variables"

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/frontier
# ---------------------------------------------------------------------------


class TestGetMindMapFrontier:
    async def test_returns_404_for_missing_map(self):
        """Non-existent mind map ID should return 404."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{uuid.uuid4()}/frontier")

        assert resp.status_code == 404

    async def test_returns_list_of_frontier_nodes(self):
        """Should return a list of node objects for the frontier."""
        map_record = _MockRecord(_mind_map_record())
        frontier_node = _MockRecord(_node_record())

        mock_pool = AsyncMock()

        async def _fetchrow(sql, *args):
            return map_record

        async def _fetch(sql, *args):
            # mind_map_get's edge query also contains "mind_map_nodes" in JOIN
            # mind_map_frontier only has mind_map_nodes (no edges)
            if "mind_map_edges" in sql:
                return []
            if "mind_map_nodes" in sql:
                return [frontier_node]
            return []

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_pool.fetch = AsyncMock(side_effect=_fetch)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/frontier")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)

    async def test_returns_empty_list_when_no_frontier(self):
        """When no frontier nodes exist, should return an empty list."""
        map_record = _MockRecord(_mind_map_record())

        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=map_record)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/frontier")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/frontier")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/analytics
# ---------------------------------------------------------------------------


class TestGetMindMapAnalytics:
    async def test_returns_404_for_missing_map(self):
        """Non-existent mind map ID should return 404."""
        mock_pool = AsyncMock()
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{uuid.uuid4()}/analytics")

        assert resp.status_code == 404

    async def test_returns_404_when_no_snapshot(self):
        """When no analytics snapshot exists, should return 404."""
        map_record = _MockRecord(_mind_map_record())

        mock_pool = AsyncMock()

        # mind_map_get calls fetchrow (for map), fetch (for nodes and edges)
        # analytics_get_snapshot calls fetchrow (for snapshot)
        call_count = {"fetchrow": 0, "fetch": 0}

        async def _fetchrow(sql, *args):
            call_count["fetchrow"] += 1
            if "mind_maps" in sql and call_count["fetchrow"] == 1:
                return map_record
            # Second fetchrow is for analytics_snapshots — return None
            return None

        async def _fetch(sql, *args):
            return []

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_pool.fetch = AsyncMock(side_effect=_fetch)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/analytics")

        assert resp.status_code == 404

    async def test_returns_snapshot_data(self):
        """When a snapshot exists, it should be returned with correct fields."""
        map_record = _MockRecord(_mind_map_record())
        snap_record = _MockRecord(_analytics_snapshot_record())

        mock_pool = AsyncMock()

        call_count = {"fetchrow": 0}

        async def _fetchrow(sql, *args):
            call_count["fetchrow"] += 1
            if call_count["fetchrow"] == 1:
                return map_record
            return snap_record

        async def _fetch(sql, *args):
            return []

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_pool.fetch = AsyncMock(side_effect=_fetch)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/analytics")

        assert resp.status_code == 200
        body = resp.json()
        assert "metrics" in body
        assert "snapshot_date" in body
        assert "trend" in body
        assert body["trend"] == []

    async def test_trend_days_param_accepted(self):
        """The ?trend_days= query parameter should trigger trend data inclusion."""
        map_record = _MockRecord(_mind_map_record())
        snap_record = _MockRecord(_analytics_snapshot_record())

        mock_pool = AsyncMock()

        call_count = {"fetchrow": 0}

        async def _fetchrow(sql, *args):
            call_count["fetchrow"] += 1
            if call_count["fetchrow"] == 1:
                return map_record
            return snap_record

        async def _fetch(sql, *args):
            # Returns trend snapshots
            return [snap_record]

        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_pool.fetch = AsyncMock(side_effect=_fetch)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/education/mind-maps/{_MAP_ID}/analytics", params={"trend_days": 7}
            )

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["trend"], list)

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/analytics")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/education/quiz-responses
# ---------------------------------------------------------------------------


class TestListQuizResponses:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/quiz-responses")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]

    async def test_empty_results(self):
        """When no quiz responses exist, data should be an empty list."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/quiz-responses")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_mind_map_id_filter_accepted(self):
        """The ?mind_map_id= query parameter must not cause an error."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/education/quiz-responses", params={"mind_map_id": _MAP_ID}
            )

        assert resp.status_code == 200

    async def test_node_id_filter_accepted(self):
        """The ?node_id= query parameter must not cause an error."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/quiz-responses", params={"node_id": _NODE_ID})

        assert resp.status_code == 200

    async def test_both_filters_accepted(self):
        """Both ?mind_map_id= and ?node_id= filters can be combined."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/education/quiz-responses",
                params={"mind_map_id": _MAP_ID, "node_id": _NODE_ID},
            )

        assert resp.status_code == 200

    async def test_pagination_params_accepted(self):
        """The ?offset= and ?limit= query parameters must be accepted."""
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=0)
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/education/quiz-responses", params={"offset": 5, "limit": 10}
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["offset"] == 5
        assert body["meta"]["limit"] == 10

    async def test_returns_quiz_response_data(self):
        """When quiz responses exist, they should appear with correct fields."""
        qr = _quiz_response_record()
        record = _MockRecord(qr)
        mock_pool = AsyncMock()
        mock_pool.fetchval = AsyncMock(return_value=1)
        mock_pool.fetch = AsyncMock(return_value=[record])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/quiz-responses")

        body = resp.json()
        assert body["meta"]["total"] == 1
        assert len(body["data"]) == 1
        item = body["data"][0]
        assert item["question_text"] == "What is a variable?"
        assert item["quality"] == 4
        assert item["response_type"] == "review"
        assert "id" in item
        assert "node_id" in item
        assert "mind_map_id" in item

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/quiz-responses")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/education/flows
# ---------------------------------------------------------------------------


class TestListFlows:
    async def test_returns_list_of_flows(self):
        """Response must be a JSON array of teaching flow objects."""
        mock_pool = AsyncMock()
        # teaching_flow_list calls pool.fetch() for mind maps
        # and state_get (pool.fetchrow) for each map's flow state
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.fetchrow = AsyncMock(return_value=None)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/flows")

        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_empty_results_when_no_flows(self):
        """When no flows exist, should return an empty list."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.fetchrow = AsyncMock(return_value=None)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/flows")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_status_filter_accepted(self):
        """The ?status= query parameter must not cause an error."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        mock_pool.fetchrow = AsyncMock(return_value=None)
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/flows", params={"status": "teaching"})

        assert resp.status_code == 200

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/flows")

        assert resp.status_code == 503

    async def test_returns_flow_fields(self):
        """Flow items should have the expected response fields."""
        # Set up a mind map row
        map_record = _MockRecord(
            {
                "id": uuid.UUID(_MAP_ID),
                "title": "Python",
                "created_at": datetime.now(UTC),
            }
        )

        # Flow state stored in KV (state table).
        # state_get calls pool.fetchval("SELECT value FROM state WHERE key = $1", key)
        # which returns the JSONB value directly (a dict when decoded by asyncpg).
        flow_state_value = {
            "status": "teaching",
            "mind_map_id": _MAP_ID,
            "current_node_id": _NODE_ID,
            "current_phase": "explaining",
            "diagnostic_results": {},
            "session_count": 3,
            "started_at": _NOW,
            "last_session_at": _NOW,
        }

        # mastery_get_map_summary calls fetchrow for aggregation
        summary_record = _MockRecord(
            {
                "total_nodes": 10,
                "mastered_count": 3,
                "learning_count": 2,
                "reviewing_count": 1,
                "unseen_count": 4,
                "diagnosed_count": 0,
                "avg_mastery_score": 0.3,
            }
        )

        mock_pool = AsyncMock()

        async def _fetch(sql, *args):
            if "mind_maps" in sql and "WHERE" not in sql:
                return [map_record]
            if "quiz_responses" in sql or "mind_map_nodes" in sql:
                return []
            return []

        async def _fetchrow(sql, *args):
            # mastery_get_map_summary aggregation query
            return summary_record

        async def _fetchval(sql, *args):
            # state_get: returns the JSONB value dict directly
            if "state" in sql.lower():
                return flow_state_value
            return None

        mock_pool.fetch = AsyncMock(side_effect=_fetch)
        mock_pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        mock_pool.fetchval = AsyncMock(side_effect=_fetchval)

        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/flows")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        if body:
            flow = body[0]
            assert "mind_map_id" in flow
            assert "title" in flow
            assert "status" in flow
            assert "session_count" in flow
            assert "mastery_pct" in flow


# ---------------------------------------------------------------------------
# GET /api/education/analytics/cross-topic
# ---------------------------------------------------------------------------


class TestGetCrossTopicAnalytics:
    async def test_returns_cross_topic_structure(self):
        """Response should have topics list, strongest_topic, weakest_topic, portfolio_mastery."""
        mock_pool = AsyncMock()
        # analytics_get_cross_topic calls pool.fetch
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/analytics/cross-topic")

        assert resp.status_code == 200
        body = resp.json()
        assert "topics" in body
        assert "strongest_topic" in body
        assert "weakest_topic" in body
        assert "portfolio_mastery" in body
        assert isinstance(body["topics"], list)

    async def test_empty_topics_when_no_maps(self):
        """When no active mind maps exist, topics should be empty."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/analytics/cross-topic")

        body = resp.json()
        assert body["topics"] == []
        assert body["strongest_topic"] is None
        assert body["weakest_topic"] is None
        assert body["portfolio_mastery"] == 0.0

    async def test_returns_topic_data_when_snapshots_exist(self):
        """When analytics snapshots exist, topics should include per-map data."""
        metrics = {
            "mastery_pct": 0.6,
            "retention_rate_7d": 0.8,
            "velocity_nodes_per_week": 2.0,
            "mastered_nodes": 6,
            "total_nodes": 10,
        }
        cross_record = _MockRecord(
            {
                "mind_map_id": _MAP_ID,  # SQL returns mind_map_id::text as string
                "title": "Python",
                "metrics": metrics,
            }
        )

        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[cross_record])
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/analytics/cross-topic")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["topics"]) == 1
        topic = body["topics"][0]
        assert topic["title"] == "Python"
        assert topic["mastery_pct"] == 0.6
        assert body["strongest_topic"] == _MAP_ID
        assert body["portfolio_mastery"] > 0

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/education/analytics/cross-topic")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Helper: get the dynamically-loaded education router module for patching
# ---------------------------------------------------------------------------


def _get_education_module(app):
    """Return the dynamically-loaded education router module."""
    for butler_name, router_module in app.state.butler_routers:
        if butler_name == "education":
            return router_module
    raise RuntimeError("Education router not found in app.state.butler_routers")


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/pending-reviews
# ---------------------------------------------------------------------------


class TestGetPendingReviews:
    async def test_returns_pending_review_nodes(self):
        """When reviews are due, return the list of pending nodes."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        review_nodes = [
            {
                "node_id": _NODE_ID,
                "label": "Variables",
                "ease_factor": 2.5,
                "repetitions": 2,
                "next_review_at": _NOW,
                "mastery_status": "reviewing",
            },
        ]

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "spaced_repetition_pending_reviews",
                new_callable=AsyncMock,
                return_value=review_nodes,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/pending-reviews")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) == 1
        assert body[0]["node_id"] == _NODE_ID
        assert body[0]["label"] == "Variables"
        assert body[0]["mastery_status"] == "reviewing"

    async def test_returns_empty_when_no_reviews_due(self):
        """When no reviews are due, return an empty list."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "spaced_repetition_pending_reviews",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/pending-reviews")

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_404_for_missing_map(self):
        """Non-existent mind map should return 404."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(edu, "mind_map_get", new_callable=AsyncMock, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{uuid.uuid4()}/pending-reviews")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/mastery-summary
# ---------------------------------------------------------------------------


class TestGetMasterySummary:
    async def test_returns_summary_data(self):
        """When mind map exists, return aggregate mastery stats."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        summary = {
            "total_nodes": 10,
            "mastered_count": 3,
            "learning_count": 2,
            "reviewing_count": 1,
            "unseen_count": 3,
            "diagnosed_count": 1,
            "avg_mastery_score": 0.35,
            "struggling_node_ids": [_NODE_ID],
        }

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu, "mastery_get_map_summary", new_callable=AsyncMock, return_value=summary
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/mastery-summary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total_nodes"] == 10
        assert body["mastered_count"] == 3
        assert body["avg_mastery_score"] == 0.35
        assert body["struggling_node_ids"] == [_NODE_ID]

    async def test_returns_404_for_missing_map(self):
        """Non-existent mind map should return 404."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(edu, "mind_map_get", new_callable=AsyncMock, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{uuid.uuid4()}/mastery-summary")

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/education/mind-maps/{id}/status
# ---------------------------------------------------------------------------


class TestUpdateMindMapStatus:
    async def test_abandon_active_map(self):
        """Setting status to 'abandoned' should return updated map."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        updated_map = _mind_map_record(status="abandoned")

        with (
            patch.object(edu, "mind_map_update_status", new_callable=AsyncMock),
            patch.object(edu, "mind_map_get", new_callable=AsyncMock, return_value=updated_map),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    f"/api/education/mind-maps/{_MAP_ID}/status",
                    json={"status": "abandoned"},
                )

        assert resp.status_code == 200
        assert resp.json()["status"] == "abandoned"

    async def test_reactivate_abandoned_map(self):
        """Setting status to 'active' should return updated map."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        updated_map = _mind_map_record(status="active")

        with (
            patch.object(edu, "mind_map_update_status", new_callable=AsyncMock),
            patch.object(edu, "mind_map_get", new_callable=AsyncMock, return_value=updated_map),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    f"/api/education/mind-maps/{_MAP_ID}/status",
                    json={"status": "active"},
                )

        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    async def test_invalid_status_returns_422(self):
        """Invalid status value should return 422."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/api/education/mind-maps/{_MAP_ID}/status",
                json={"status": "paused"},
            )

        assert resp.status_code == 422

    async def test_missing_map_returns_404(self):
        """Non-existent mind map should return 404."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(
            edu,
            "mind_map_update_status",
            new_callable=AsyncMock,
            side_effect=ValueError("not found"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    f"/api/education/mind-maps/{uuid.uuid4()}/status",
                    json={"status": "abandoned"},
                )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/education/curriculum-requests
# ---------------------------------------------------------------------------


class TestSubmitCurriculumRequest:
    async def test_submit_new_request(self):
        """New curriculum request should return 202 with pending status."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(edu, "state_get", new_callable=AsyncMock, return_value=None),
            patch.object(edu, "state_set", new_callable=AsyncMock, return_value=1),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "Python", "goal": "Learn web development"},
                )

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending"
        assert body["topic"] == "Python"

    async def test_submit_without_goal(self):
        """Request without goal should still return 202."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(edu, "state_get", new_callable=AsyncMock, return_value=None),
            patch.object(edu, "state_set", new_callable=AsyncMock, return_value=1),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "Linear Algebra"},
                )

        assert resp.status_code == 202
        assert resp.json()["topic"] == "Linear Algebra"

    async def test_duplicate_request_returns_409(self):
        """When a pending request exists, return 409 Conflict."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        existing = {"topic": "Rust", "goal": None, "requested_at": _NOW}
        with patch.object(edu, "state_get", new_callable=AsyncMock, return_value=existing):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "Python"},
                )

        assert resp.status_code == 409

    async def test_empty_topic_returns_422(self):
        """Empty topic should return 422."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(edu, "state_get", new_callable=AsyncMock, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": ""},
                )

        assert resp.status_code == 422

    async def test_topic_too_long_returns_422(self):
        """Topic exceeding 200 chars should return 422."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(edu, "state_get", new_callable=AsyncMock, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "x" * 201},
                )

        assert resp.status_code == 422
