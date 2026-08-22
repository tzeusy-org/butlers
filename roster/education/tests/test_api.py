"""Tests for education butler API endpoints.

Verifies the API contract (status codes, response shapes) for education
endpoints. Uses a mocked DatabaseManager so no real database is required.

Issue: butlers-2kmd.11
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager

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
        "evaluator_notes": None,
        "node_label": None,
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
    mcp_manager: AsyncMock | None = None,
):
    """Build a FastAPI test app with the education pool mocked.

    The MCP manager dependency is always overridden (curriculum-request submission
    triggers an education session through it); pass ``mcp_manager`` to inspect the
    trigger call, otherwise a no-op AsyncMock is used.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: education")

    app = create_app()

    mgr = mcp_manager if mcp_manager is not None else AsyncMock()
    app.dependency_overrides[get_mcp_manager] = lambda: mgr

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

    async def test_returns_real_mastery_score_when_present(self):
        """bu-86c4c.1: the response must carry the node's real mastery_score
        through to the client (never fabricate one from mastery_status)."""
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
                "mastery_score": 0.42,
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
        assert body[0]["mastery_score"] == 0.42

    async def test_mastery_score_defaults_to_null_when_absent(self):
        """Older callers that don't return mastery_score must not crash the
        endpoint, and must never have a value fabricated for them."""
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
        assert body[0]["mastery_score"] is None

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
# Curriculum requests — accepted-to-outcome receipts
#
# The 202 is an *acceptance*, not a completion: the receipt row exists before
# the detached curriculum work starts, and every exit path of that work settles
# a terminal state onto it. These tests hold both halves of that contract.
# ---------------------------------------------------------------------------


def _receipt_row(
    *,
    request_id: str | None = None,
    topic: str = "Python",
    goal: str | None = None,
    status: str = "accepted",
    session_id: str | None = None,
    mind_map_id: str | None = None,
    calibration_ready_at: datetime | None = None,
    failure_reason: str | None = None,
    triggered_at: datetime | None = None,
    settled_at: datetime | None = None,
) -> _MockRecord:
    """Build an ``education.curriculum_requests`` row as asyncpg would return it."""
    return _MockRecord(
        {
            "id": uuid.UUID(request_id) if request_id else uuid.uuid4(),
            "topic": topic,
            "goal": goal,
            "status": status,
            "session_id": session_id,
            "mind_map_id": uuid.UUID(mind_map_id) if mind_map_id else None,
            "calibration_ready_at": calibration_ready_at,
            "failure_reason": failure_reason,
            "requested_at": datetime.now(UTC),
            "triggered_at": triggered_at,
            "settled_at": settled_at,
            "updated_at": datetime.now(UTC),
        }
    )


def _trigger_result(
    *, success: bool = True, session_id: str | None = "sess-1", error: str | None = None
) -> MagicMock:
    """Build an MCP ``trigger`` tool result the way FastMCP returns one."""
    result = MagicMock()
    result.is_error = False
    block = MagicMock()
    block.text = json.dumps(
        {"success": success, "error": error, "session_id": session_id, "output": "ok"}
    )
    result.content = [block]
    return result


class TestSubmitCurriculumRequest:
    async def test_submit_new_request(self):
        """A new request returns 202 accepted, its receipt ID, and triggers a session."""
        mock_pool = AsyncMock()
        mock_client = AsyncMock()
        mock_mgr = AsyncMock()
        mock_mgr.get_client.return_value = mock_client
        app = _app_with_mock_pool(mock_pool, mcp_manager=mock_mgr)
        edu = _get_education_module(app)

        request_id = str(uuid.uuid4())
        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(
                edu,
                "_create_receipt",
                new_callable=AsyncMock,
                return_value=_receipt_row(request_id=request_id),
            ),
            patch.object(edu, "_run_curriculum_request", new_callable=AsyncMock),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "Python", "goal": "Learn web development"},
                )
            if edu._CURRICULUM_TASKS:
                await asyncio.gather(*list(edu._CURRICULUM_TASKS))

        assert resp.status_code == 202
        body = resp.json()
        # "accepted", never "pending"/"done" — the 202 evidences acceptance only.
        assert body["status"] == "accepted"
        assert body["topic"] == "Python"
        assert body["request_id"] == request_id

    async def test_receipt_is_persisted_before_detached_work_starts(self):
        """The receipt must exist before the trigger task is created."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        order: list[str] = []

        async def _create(pool, topic, goal):
            order.append("create_receipt")
            return _receipt_row(topic=topic, goal=goal)

        async def _run(*args, **kwargs):
            order.append("run_detached")

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(edu, "_create_receipt", new=_create),
            patch.object(edu, "_run_curriculum_request", new=_run),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests", json={"topic": "Python"}
                )
            if edu._CURRICULUM_TASKS:
                await asyncio.gather(*list(edu._CURRICULUM_TASKS))

        assert resp.status_code == 202
        assert order == ["create_receipt", "run_detached"]

    async def test_submit_sweeps_abandoned_receipts_first(self):
        """A crashed request must not wedge the next one behind a stale guard."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        sweep = AsyncMock(return_value=1)
        with (
            patch.object(edu, "_sweep_abandoned_receipts", new=sweep),
            patch.object(
                edu, "_create_receipt", new_callable=AsyncMock, return_value=_receipt_row()
            ),
            patch.object(edu, "_run_curriculum_request", new_callable=AsyncMock),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests", json={"topic": "Python"}
                )
            if edu._CURRICULUM_TASKS:
                await asyncio.gather(*list(edu._CURRICULUM_TASKS))

        assert resp.status_code == 202
        sweep.assert_awaited()

    async def test_submit_without_goal(self):
        """Request without goal should still return 202."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(
                edu,
                "_create_receipt",
                new_callable=AsyncMock,
                return_value=_receipt_row(topic="Linear Algebra"),
            ),
            patch.object(edu, "_run_curriculum_request", new_callable=AsyncMock),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "Linear Algebra"},
                )
            if edu._CURRICULUM_TASKS:
                await asyncio.gather(*list(edu._CURRICULUM_TASKS))

        assert resp.status_code == 202
        assert resp.json()["topic"] == "Linear Algebra"

    async def test_duplicate_request_returns_409(self):
        """When the pending guard refuses the insert, return 409 Conflict."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(edu, "_create_receipt", new_callable=AsyncMock, return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "Python"},
                )

        assert resp.status_code == 409

    async def test_submit_returns_503_when_receipts_unavailable(self):
        """Without a receipt store there is nothing to promise — refuse, don't fake it."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(
            edu,
            "_sweep_abandoned_receipts",
            new_callable=AsyncMock,
            side_effect=asyncpg.UndefinedTableError("no such table"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/education/curriculum-requests",
                    json={"topic": "Python"},
                )

        assert resp.status_code == 503
        assert "unavailable" in resp.json()["detail"].lower()

    async def test_empty_topic_returns_422(self):
        """Empty topic should return 422."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)

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

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/education/curriculum-requests",
                json={"topic": "x" * 201},
            )

        assert resp.status_code == 422


class TestCurriculumRequestDetachedWork:
    """The detached task must land a terminal state on every exit path."""

    async def test_successful_session_settles_completed_with_evidence(self):
        """A session that produced a calibrating curriculum settles 'completed'."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        mock_client = AsyncMock()
        mock_client.call_tool.return_value = _trigger_result(session_id="sess-9")
        mock_mgr = AsyncMock()
        mock_mgr.get_client.return_value = mock_client

        map_id = str(uuid.uuid4())
        settle = AsyncMock(return_value=True)
        request_id = str(uuid.uuid4())

        with (
            patch.object(edu, "_mark_receipt_running", new_callable=AsyncMock),
            patch.object(
                edu,
                "_correlate_curriculum",
                new_callable=AsyncMock,
                return_value=(map_id, True),
            ),
            patch.object(edu, "_settle_receipt", new=settle),
        ):
            await edu._run_curriculum_request(mock_mgr, mock_pool, request_id, "Python", None)

        settle.assert_awaited_once()
        kwargs = settle.await_args.kwargs
        assert kwargs["status"] == "completed"
        assert kwargs["session_id"] == "sess-9"
        assert kwargs["mind_map_id"] == map_id
        assert kwargs["calibration_ready"] is True

    async def test_trigger_failure_settles_failed(self):
        """An unreachable butler must produce a terminal, owner-visible failure."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        mock_mgr = AsyncMock()
        mock_mgr.get_client.side_effect = RuntimeError("butler unreachable")

        settle = AsyncMock(return_value=True)
        with (
            patch.object(edu, "_mark_receipt_running", new_callable=AsyncMock),
            patch.object(edu, "_settle_receipt", new=settle),
        ):
            await edu._run_curriculum_request(
                mock_mgr, mock_pool, str(uuid.uuid4()), "Python", None
            )

        settle.assert_awaited_once()
        kwargs = settle.await_args.kwargs
        assert kwargs["status"] == "failed"
        assert kwargs["failure_reason"] == edu._FAILURE_TRIGGER_UNREACHABLE

    async def test_session_reported_error_settles_failed(self):
        """A session that reports its own failure must not read as completed."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        mock_client = AsyncMock()
        mock_client.call_tool.return_value = _trigger_result(
            success=False, session_id="sess-bad", error="teaching_flow_start raised"
        )
        mock_mgr = AsyncMock()
        mock_mgr.get_client.return_value = mock_client

        settle = AsyncMock(return_value=True)
        with (
            patch.object(edu, "_mark_receipt_running", new_callable=AsyncMock),
            patch.object(edu, "_settle_receipt", new=settle),
        ):
            await edu._run_curriculum_request(
                mock_mgr, mock_pool, str(uuid.uuid4()), "Python", None
            )

        kwargs = settle.await_args.kwargs
        assert kwargs["status"] == "failed"
        assert kwargs["failure_reason"] == edu._FAILURE_SESSION_ERROR
        assert kwargs["session_id"] == "sess-bad"

    async def test_clean_exit_without_curriculum_settles_failed(self):
        """A clean exit is not evidence — no curriculum means the request failed."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        mock_client = AsyncMock()
        mock_client.call_tool.return_value = _trigger_result(session_id="sess-empty")
        mock_mgr = AsyncMock()
        mock_mgr.get_client.return_value = mock_client

        settle = AsyncMock(return_value=True)
        with (
            patch.object(edu, "_mark_receipt_running", new_callable=AsyncMock),
            patch.object(
                edu,
                "_correlate_curriculum",
                new_callable=AsyncMock,
                return_value=(None, False),
            ),
            patch.object(edu, "_settle_receipt", new=settle),
        ):
            await edu._run_curriculum_request(
                mock_mgr, mock_pool, str(uuid.uuid4()), "Python", None
            )

        kwargs = settle.await_args.kwargs
        assert kwargs["status"] == "failed"
        assert kwargs["failure_reason"] == edu._FAILURE_NO_CURRICULUM

    async def test_settle_failure_never_escapes_the_task(self):
        """A settle that raises would strand the guard; it must be swallowed."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        mock_mgr = AsyncMock()
        mock_mgr.get_client.side_effect = RuntimeError("butler unreachable")

        with (
            patch.object(edu, "_mark_receipt_running", new_callable=AsyncMock),
            patch.object(
                edu,
                "_settle_receipt",
                new_callable=AsyncMock,
                side_effect=RuntimeError("db down"),
            ),
        ):
            # Must not raise.
            await edu._run_curriculum_request(
                mock_mgr, mock_pool, str(uuid.uuid4()), "Python", None
            )

    async def test_prompt_does_not_ask_the_session_to_release_the_guard(self):
        """The guard is backend-owned; an LLM forgetting a call must not wedge it."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        prompt = edu._curriculum_prompt("Python", "web dev")
        assert "teaching_flow_start" in prompt
        assert "Python" in prompt
        assert "web dev" in prompt
        assert "state_delete" not in prompt


class TestReadCurriculumRequest:
    """Read-only status: terminal evidence, absence, and unavailability are distinct."""

    async def test_get_receipt_by_id(self):
        """The receipt read returns the full evidence set for a terminal request."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        request_id = str(uuid.uuid4())
        map_id = str(uuid.uuid4())
        row = _receipt_row(
            request_id=request_id,
            status="completed",
            session_id="sess-3",
            mind_map_id=map_id,
            calibration_ready_at=datetime.now(UTC),
            triggered_at=datetime.now(UTC),
            settled_at=datetime.now(UTC),
        )

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(edu, "_get_receipt", new_callable=AsyncMock, return_value=row),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/curriculum-requests/{request_id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["receipts_available"] is True
        receipt = body["receipt"]
        assert receipt["request_id"] == request_id
        assert receipt["status"] == "completed"
        assert receipt["session_id"] == "sess-3"
        assert receipt["mind_map_id"] == map_id
        assert receipt["calibration_ready_at"] is not None
        assert receipt["settled_at"] is not None
        assert receipt["failure_reason"] is None

    async def test_get_receipt_exposes_terminal_failure_reason(self):
        """A failed request names its reason so the UI can say what went wrong."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        request_id = str(uuid.uuid4())
        row = _receipt_row(
            request_id=request_id,
            status="failed",
            failure_reason="trigger_unreachable",
            settled_at=datetime.now(UTC),
        )

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(edu, "_get_receipt", new_callable=AsyncMock, return_value=row),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/curriculum-requests/{request_id}")

        assert resp.status_code == 200
        assert resp.json()["receipt"]["failure_reason"] == "trigger_unreachable"

    async def test_unknown_receipt_returns_404(self):
        """An unknown request ID must 404, never a fabricated empty receipt."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(edu, "_get_receipt", new_callable=AsyncMock, return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/curriculum-requests/{uuid.uuid4()}")

        assert resp.status_code == 404

    async def test_receipts_unavailable_is_explicit(self):
        """A pre-migration store reads as unavailable, not as 'nothing in flight'."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(
            edu,
            "_sweep_abandoned_receipts",
            new_callable=AsyncMock,
            side_effect=asyncpg.UndefinedTableError("no such table"),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/education/curriculum-requests/latest")

        assert resp.status_code == 200
        body = resp.json()
        assert body["receipts_available"] is False
        assert body["receipt"] is None

    async def test_latest_receipt_absent_is_distinct_from_unavailable(self):
        """No request ever made: available store, empty receipt."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(edu, "_latest_receipt", new_callable=AsyncMock, return_value=None),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/education/curriculum-requests/latest")

        assert resp.status_code == 200
        body = resp.json()
        assert body["receipts_available"] is True
        assert body["receipt"] is None

    async def test_latest_receipt_returns_open_request(self):
        """An in-flight request reads as accepted with no completion evidence."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        row = _receipt_row(status="accepted")
        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(edu, "_latest_receipt", new_callable=AsyncMock, return_value=row),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/education/curriculum-requests/latest")

        receipt = resp.json()["receipt"]
        assert receipt["status"] == "accepted"
        assert receipt["mind_map_id"] is None
        assert receipt["settled_at"] is None

    async def test_malformed_request_id_returns_422(self):
        """A non-UUID path segment is a client error, not a 500."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(edu, "_sweep_abandoned_receipts", new_callable=AsyncMock, return_value=0),
            patch.object(
                edu,
                "_get_receipt",
                new_callable=AsyncMock,
                side_effect=asyncpg.DataError("invalid input syntax for type uuid"),
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/education/curriculum-requests/not-a-uuid")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/analytics/trend
# ---------------------------------------------------------------------------


def _trend_snapshot_record(*, map_id: str = _MAP_ID) -> dict:
    return {
        "id": uuid.uuid4(),
        "mind_map_id": uuid.UUID(map_id),
        "snapshot_date": date.today(),
        "metrics": {
            "total_nodes": 10,
            "mastered_nodes": 3,
            "mastery_pct": 0.3,
        },
        "created_at": datetime.now(UTC),
    }


class TestGetAnalyticsTrend:
    async def test_returns_trend_response_shape(self):
        """Response must have mind_map_id, days, and trend array."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        snap = _trend_snapshot_record()

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "analytics_get_trend",
                new_callable=AsyncMock,
                return_value=[snap],
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(
                    f"/api/education/mind-maps/{_MAP_ID}/analytics/trend",
                    params={"days": 7},
                )

        assert resp.status_code == 200
        body = resp.json()
        assert "mind_map_id" in body
        assert body["days"] == 7
        assert "trend" in body
        assert isinstance(body["trend"], list)
        assert len(body["trend"]) == 1
        entry = body["trend"][0]
        assert "snapshot_date" in entry
        assert "metrics" in entry
        assert entry["metrics"]["mastery_pct"] == 0.3

    async def test_returns_empty_trend_when_no_snapshots(self):
        """When no snapshots exist in the window, trend should be an empty list."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "analytics_get_trend",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/analytics/trend")

        assert resp.status_code == 200
        body = resp.json()
        assert body["trend"] == []

    async def test_returns_404_for_missing_map(self):
        """Non-existent mind map should return 404."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(edu, "mind_map_get", new_callable=AsyncMock, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{uuid.uuid4()}/analytics/trend")

        assert resp.status_code == 404

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/analytics/trend")

        assert resp.status_code == 503

    async def test_default_days_is_seven(self):
        """When ?days is omitted the endpoint defaults to 7 and echoes it in the response."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "analytics_get_trend",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/analytics/trend")

        assert resp.status_code == 200
        assert resp.json()["days"] == 7


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/struggling-nodes
# ---------------------------------------------------------------------------


class TestGetStrugglingNodes:
    async def test_returns_struggling_nodes_shape(self):
        """Response must have mind_map_id and nodes array with expected fields."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        struggling = [
            {
                "id": _NODE_ID,
                "label": "Closures",
                "mastery_score": 0.15,
                "mastery_status": "learning",
                "reason": "consecutive_low_quality",
            }
        ]

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "mastery_detect_struggles",
                new_callable=AsyncMock,
                return_value=struggling,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/struggling-nodes")

        assert resp.status_code == 200
        body = resp.json()
        assert body["mind_map_id"] == _MAP_ID
        assert isinstance(body["nodes"], list)
        assert len(body["nodes"]) == 1
        node = body["nodes"][0]
        assert node["node_id"] == _NODE_ID
        assert node["node_label"] == "Closures"
        assert node["mastery_score"] == 0.15
        assert node["mastery_status"] == "learning"
        assert node["reason"] == "consecutive_low_quality"

    async def test_returns_empty_nodes_when_none_struggling(self):
        """When no nodes are struggling, nodes should be an empty list."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "mastery_detect_struggles",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/struggling-nodes")

        assert resp.status_code == 200
        assert resp.json()["nodes"] == []

    async def test_returns_404_for_missing_map(self):
        """Non-existent mind map should return 404."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        with patch.object(edu, "mind_map_get", new_callable=AsyncMock, return_value=None):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{uuid.uuid4()}/struggling-nodes")

        assert resp.status_code == 404

    async def test_pool_unavailable_returns_503(self):
        """When the education DB pool is unavailable, return 503."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool, pool_available=False)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/struggling-nodes")

        assert resp.status_code == 503

    async def test_combined_reason_included(self):
        """Nodes with both struggle reasons should expose the combined reason string."""
        mock_pool = AsyncMock()
        app = _app_with_mock_pool(mock_pool)
        edu = _get_education_module(app)

        struggling = [
            {
                "id": _NODE_ID,
                "label": "Generators",
                "mastery_score": 0.1,
                "mastery_status": "learning",
                "reason": "consecutive_low_quality,declining_score",
            }
        ]

        with (
            patch.object(
                edu, "mind_map_get", new_callable=AsyncMock, return_value=_mind_map_record()
            ),
            patch.object(
                edu,
                "mastery_detect_struggles",
                new_callable=AsyncMock,
                return_value=struggling,
            ),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get(f"/api/education/mind-maps/{_MAP_ID}/struggling-nodes")

        assert resp.status_code == 200
        node = resp.json()["nodes"][0]
        assert node["reason"] == "consecutive_low_quality,declining_score"
