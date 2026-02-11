"""Tests for trace API endpoints and span tree assembly.

Verifies the API contract (status codes, response shapes) for trace
endpoints and the correctness of the tree assembly algorithm.
Uses a mocked DatabaseManager so no real database is required.

Issues: butlers-26h.7.1, 7.2, 7.3, 7.4
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.routers.traces import (
    _determine_trace_status,
    _get_db_manager,
    assemble_span_tree,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_TRACE_ID = "trace-abc-123"


def _make_session_row(
    *,
    session_id=None,
    prompt="test prompt",
    trigger_source="schedule",
    success=True,
    started_at=None,
    completed_at=None,
    duration_ms=1000,
    model="claude-opus-4-20250514",
    input_tokens=100,
    output_tokens=200,
    parent_session_id=None,
    trace_id=_TRACE_ID,
):
    """Create a dict mimicking an asyncpg Record for session columns."""
    return {
        "id": session_id or uuid4(),
        "prompt": prompt,
        "trigger_source": trigger_source,
        "success": success,
        "started_at": started_at or _NOW,
        "completed_at": completed_at or _NOW,
        "duration_ms": duration_ms,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "parent_session_id": parent_session_id,
        "trace_id": trace_id,
    }


def _make_trace_summary_row(
    *,
    trace_id=_TRACE_ID,
    span_count=2,
    started_at=None,
    total_duration_ms=2000,
    all_success=True,
    has_running=False,
    has_failure=False,
):
    """Create a dict mimicking the trace summary aggregate query result."""
    return {
        "trace_id": trace_id,
        "span_count": span_count,
        "started_at": started_at or _NOW,
        "total_duration_ms": total_duration_ms,
        "all_success": all_success,
        "has_running": has_running,
        "has_failure": has_failure,
    }


def _app_with_mock_db(
    *,
    fan_out_results: list[dict[str, list]] | None = None,
):
    """Create a FastAPI app with a mocked DatabaseManager.

    fan_out_results is a list of dicts — one per fan_out call in order.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "switchboard"]

    if fan_out_results is not None:
        mock_db.fan_out = AsyncMock(side_effect=fan_out_results)
    else:
        mock_db.fan_out = AsyncMock(return_value={})

    app = create_app()
    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# Unit tests: assemble_span_tree
# ---------------------------------------------------------------------------


class TestAssembleSpanTree:
    def test_single_root_span(self):
        """A single session with no parent should be a lone root."""
        sid = uuid4()
        sessions = [
            {
                "id": sid,
                "butler": "atlas",
                "prompt": "root task",
                "trigger_source": "schedule",
                "success": True,
                "started_at": _NOW,
                "completed_at": _NOW,
                "duration_ms": 500,
                "model": "claude-opus-4-20250514",
                "input_tokens": 100,
                "output_tokens": 50,
                "parent_session_id": None,
            }
        ]
        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        assert roots[0].id == sid
        assert roots[0].butler == "atlas"
        assert roots[0].children == []

    def test_parent_child_nesting(self):
        """Children should be nested under their parent."""
        parent_id = uuid4()
        child1_id = uuid4()
        child2_id = uuid4()

        sessions = [
            {
                "id": parent_id,
                "butler": "atlas",
                "prompt": "parent",
                "trigger_source": "schedule",
                "success": True,
                "started_at": _NOW,
                "completed_at": _NOW + timedelta(seconds=10),
                "duration_ms": 10000,
                "model": None,
                "input_tokens": None,
                "output_tokens": None,
                "parent_session_id": None,
            },
            {
                "id": child1_id,
                "butler": "switchboard",
                "prompt": "child 1",
                "trigger_source": "mcp",
                "success": True,
                "started_at": _NOW + timedelta(seconds=1),
                "completed_at": _NOW + timedelta(seconds=3),
                "duration_ms": 2000,
                "model": None,
                "input_tokens": None,
                "output_tokens": None,
                "parent_session_id": parent_id,
            },
            {
                "id": child2_id,
                "butler": "atlas",
                "prompt": "child 2",
                "trigger_source": "mcp",
                "success": True,
                "started_at": _NOW + timedelta(seconds=4),
                "completed_at": _NOW + timedelta(seconds=6),
                "duration_ms": 2000,
                "model": None,
                "input_tokens": None,
                "output_tokens": None,
                "parent_session_id": parent_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        root = roots[0]
        assert root.id == parent_id
        assert len(root.children) == 2
        assert root.children[0].id == child1_id
        assert root.children[1].id == child2_id

    def test_children_sorted_by_started_at(self):
        """Children should be sorted by started_at, not insertion order."""
        parent_id = uuid4()
        early_id = uuid4()
        late_id = uuid4()

        sessions = [
            {
                "id": parent_id,
                "butler": "atlas",
                "prompt": "parent",
                "trigger_source": "schedule",
                "started_at": _NOW,
                "parent_session_id": None,
            },
            {
                "id": late_id,
                "butler": "atlas",
                "prompt": "late child",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=10),
                "parent_session_id": parent_id,
            },
            {
                "id": early_id,
                "butler": "switchboard",
                "prompt": "early child",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=1),
                "parent_session_id": parent_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        children = roots[0].children
        assert len(children) == 2
        assert children[0].id == early_id
        assert children[1].id == late_id

    def test_deep_nesting(self):
        """Grandchildren should also be properly nested."""
        root_id = uuid4()
        child_id = uuid4()
        grandchild_id = uuid4()

        sessions = [
            {
                "id": root_id,
                "butler": "atlas",
                "prompt": "root",
                "trigger_source": "schedule",
                "started_at": _NOW,
                "parent_session_id": None,
            },
            {
                "id": child_id,
                "butler": "switchboard",
                "prompt": "child",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=1),
                "parent_session_id": root_id,
            },
            {
                "id": grandchild_id,
                "butler": "atlas",
                "prompt": "grandchild",
                "trigger_source": "mcp",
                "started_at": _NOW + timedelta(seconds=2),
                "parent_session_id": child_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        assert roots[0].id == root_id
        assert len(roots[0].children) == 1
        assert roots[0].children[0].id == child_id
        assert len(roots[0].children[0].children) == 1
        assert roots[0].children[0].children[0].id == grandchild_id

    def test_orphan_becomes_root(self):
        """A session whose parent is not in the trace should become a root."""
        orphan_id = uuid4()
        missing_parent_id = uuid4()

        sessions = [
            {
                "id": orphan_id,
                "butler": "atlas",
                "prompt": "orphan",
                "trigger_source": "mcp",
                "started_at": _NOW,
                "parent_session_id": missing_parent_id,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 1
        assert roots[0].id == orphan_id

    def test_empty_sessions(self):
        """Empty session list should return empty roots."""
        roots = assemble_span_tree([])
        assert roots == []

    def test_multiple_roots(self):
        """Multiple sessions with no parent should all be root spans."""
        root1_id = uuid4()
        root2_id = uuid4()

        sessions = [
            {
                "id": root1_id,
                "butler": "atlas",
                "prompt": "root 1",
                "trigger_source": "schedule",
                "started_at": _NOW + timedelta(seconds=5),
                "parent_session_id": None,
            },
            {
                "id": root2_id,
                "butler": "switchboard",
                "prompt": "root 2",
                "trigger_source": "schedule",
                "started_at": _NOW,
                "parent_session_id": None,
            },
        ]

        roots = assemble_span_tree(sessions)
        assert len(roots) == 2
        # Sorted by started_at
        assert roots[0].id == root2_id
        assert roots[1].id == root1_id


# ---------------------------------------------------------------------------
# Unit tests: _determine_trace_status
# ---------------------------------------------------------------------------


class TestDetermineTraceStatus:
    def test_all_success(self):
        spans = [{"success": True}, {"success": True}]
        assert _determine_trace_status(spans) == "success"

    def test_all_failed(self):
        spans = [{"success": False}, {"success": False}]
        assert _determine_trace_status(spans) == "failed"

    def test_mixed_partial(self):
        spans = [{"success": True}, {"success": False}]
        assert _determine_trace_status(spans) == "partial"

    def test_all_running(self):
        spans = [{"success": None}, {"success": None}]
        assert _determine_trace_status(spans) == "running"

    def test_some_running_no_failure(self):
        spans = [{"success": True}, {"success": None}]
        assert _determine_trace_status(spans) == "running"

    def test_some_running_with_failure(self):
        spans = [{"success": False}, {"success": None}]
        assert _determine_trace_status(spans) == "partial"

    def test_empty_spans(self):
        assert _determine_trace_status([]) == "running"


# ---------------------------------------------------------------------------
# API tests: GET /api/traces/
# ---------------------------------------------------------------------------


class TestListTraces:
    async def test_returns_paginated_response_structure(self):
        """Response must have 'data' array and 'meta' with pagination fields."""
        app = _app_with_mock_db(fan_out_results=[{}])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]
        assert "offset" in body["meta"]
        assert "limit" in body["meta"]

    async def test_returns_trace_summaries(self):
        """Traces from fan_out should be aggregated into summaries."""
        row1 = _make_trace_summary_row(trace_id="trace-1", span_count=3)
        row2 = _make_trace_summary_row(
            trace_id="trace-2",
            span_count=1,
            started_at=_NOW - timedelta(minutes=5),
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [row1], "switchboard": [row2]},
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 2
        assert len(body["data"]) == 2

        # Traces should be sorted by started_at DESC (newest first)
        trace_ids = [t["trace_id"] for t in body["data"]]
        assert trace_ids[0] == "trace-1"
        assert trace_ids[1] == "trace-2"

    async def test_trace_summary_fields(self):
        """Each trace summary should have the expected fields."""
        row = _make_trace_summary_row()

        app = _app_with_mock_db(fan_out_results=[{"atlas": [row]}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        trace = data[0]
        assert trace["trace_id"] == _TRACE_ID
        assert trace["root_butler"] == "atlas"
        assert trace["span_count"] == 2
        assert trace["total_duration_ms"] == 2000
        assert trace["status"] == "success"
        assert "started_at" in trace

    async def test_empty_trace_list(self):
        """When no traces exist, return empty data with total 0."""
        app = _app_with_mock_db(fan_out_results=[{}])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_cross_butler_aggregation(self):
        """Same trace_id from two butlers should be merged into one summary."""
        row_atlas = _make_trace_summary_row(
            trace_id="shared-trace",
            span_count=2,
            total_duration_ms=1000,
            started_at=_NOW,
        )
        row_sw = _make_trace_summary_row(
            trace_id="shared-trace",
            span_count=1,
            total_duration_ms=500,
            started_at=_NOW - timedelta(seconds=1),
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [row_atlas], "switchboard": [row_sw]},
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces")

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 1
        trace = body["data"][0]
        assert trace["trace_id"] == "shared-trace"
        assert trace["span_count"] == 3  # 2 + 1
        assert trace["total_duration_ms"] == 1500  # 1000 + 500
        # Root butler should be the one with earliest started_at
        assert trace["root_butler"] == "switchboard"

    async def test_pagination(self):
        """Pagination offset/limit should be respected."""
        rows = [
            _make_trace_summary_row(
                trace_id=f"trace-{i}",
                span_count=1,
                started_at=_NOW - timedelta(minutes=i),
            )
            for i in range(5)
        ]

        app = _app_with_mock_db(fan_out_results=[{"atlas": rows}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces", params={"offset": 1, "limit": 2})

        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 5
        assert len(body["data"]) == 2


# ---------------------------------------------------------------------------
# API tests: GET /api/traces/{trace_id}
# ---------------------------------------------------------------------------


class TestGetTrace:
    async def test_returns_trace_detail_with_spans(self):
        """Response should contain trace detail with assembled span tree."""
        root_id = uuid4()
        child_id = uuid4()

        root_row = _make_session_row(
            session_id=root_id,
            prompt="root task",
            started_at=_NOW,
            parent_session_id=None,
        )
        child_row = _make_session_row(
            session_id=child_id,
            prompt="child task",
            started_at=_NOW + timedelta(seconds=1),
            parent_session_id=root_id,
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [root_row, child_row]},
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/traces/{_TRACE_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        data = body["data"]

        assert data["trace_id"] == _TRACE_ID
        assert data["span_count"] == 2
        assert data["root_butler"] == "atlas"
        assert data["status"] == "success"
        assert len(data["spans"]) == 1  # One root span

        root_span = data["spans"][0]
        assert root_span["id"] == str(root_id)
        assert len(root_span["children"]) == 1
        assert root_span["children"][0]["id"] == str(child_id)

    async def test_missing_trace_returns_404(self):
        """A non-existent trace should return 404."""
        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [], "switchboard": []},
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/traces/nonexistent-trace")

        assert resp.status_code == 404

    async def test_cross_butler_span_tree(self):
        """Spans from multiple butlers should be merged into one tree."""
        root_id = uuid4()
        child_id = uuid4()

        # Root session in atlas
        root_row = _make_session_row(
            session_id=root_id,
            prompt="root in atlas",
            started_at=_NOW,
            parent_session_id=None,
        )
        # Child session in switchboard
        child_row = _make_session_row(
            session_id=child_id,
            prompt="child in switchboard",
            started_at=_NOW + timedelta(seconds=1),
            parent_session_id=root_id,
        )

        app = _app_with_mock_db(
            fan_out_results=[
                {"atlas": [root_row], "switchboard": [child_row]},
            ]
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/traces/{_TRACE_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["span_count"] == 2

        # Root span from atlas, child from switchboard
        root_span = data["spans"][0]
        assert root_span["butler"] == "atlas"
        assert len(root_span["children"]) == 1
        assert root_span["children"][0]["butler"] == "switchboard"

    async def test_trace_detail_fields(self):
        """TraceDetail should include all expected summary fields."""
        sid = uuid4()
        row = _make_session_row(
            session_id=sid,
            prompt="test",
            started_at=_NOW,
            duration_ms=1500,
            parent_session_id=None,
        )

        app = _app_with_mock_db(fan_out_results=[{"atlas": [row]}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/traces/{_TRACE_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["trace_id"] == _TRACE_ID
        assert data["root_butler"] == "atlas"
        assert data["span_count"] == 1
        assert data["total_duration_ms"] == 1500
        assert data["status"] == "success"
        assert "started_at" in data
        assert "spans" in data

    async def test_running_trace_status(self):
        """A trace with incomplete sessions should show 'running' status."""
        sid = uuid4()
        row = _make_session_row(
            session_id=sid,
            prompt="running task",
            started_at=_NOW,
            completed_at=None,
            duration_ms=None,
            success=None,
            parent_session_id=None,
        )

        app = _app_with_mock_db(fan_out_results=[{"atlas": [row]}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/traces/{_TRACE_ID}")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "running"
