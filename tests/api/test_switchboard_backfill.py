"""Tests for switchboard backfill dashboard API endpoints.

Covers:
- GET  /api/switchboard/backfill              (list jobs)
- POST /api/switchboard/backfill              (create job)
- GET  /api/switchboard/backfill/{job_id}     (job detail)
- PATCH /api/switchboard/backfill/{job_id}/pause
- PATCH /api/switchboard/backfill/{job_id}/cancel
- PATCH /api/switchboard/backfill/{job_id}/resume
- GET  /api/switchboard/backfill/{job_id}/progress

Test scenarios include empty-state, populated-state, degraded-state (DB errors),
not-found, and invalid-transition cases.

Issue: butlers-dsa4.3.3
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

# Reuse the same module name as router_discovery.py and other switchboard test
# files to share the single module instance and avoid re-execution bugs.
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"
_MODULE_NAME = "switchboard_api_router"

if _MODULE_NAME in sys.modules:
    switchboard_module = sys.modules[_MODULE_NAME]
else:
    import importlib.util

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {_router_path}")
    switchboard_module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = switchboard_module
    spec.loader.exec_module(switchboard_module)

pytestmark = pytest.mark.unit

_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

_SAMPLE_JOB_ROW = {
    "id": _JOB_ID,
    "connector_type": "gmail",
    "endpoint_identity": "user@example.com",
    "target_categories": ["finance", "health"],
    "date_from": "2020-01-01",
    "date_to": "2026-01-01",
    "rate_limit_per_hour": 100,
    "daily_cost_cap_cents": 500,
    "status": "pending",
    "cursor": None,
    "rows_processed": 0,
    "rows_skipped": 0,
    "cost_spent_cents": 0,
    "error": None,
    "created_at": "2026-02-23T10:00:00+00:00",
    "started_at": None,
    "completed_at": None,
    "updated_at": "2026-02-23T10:00:00+00:00",
}

_ACTIVE_JOB_ROW = {
    **_SAMPLE_JOB_ROW,
    "status": "active",
    "started_at": "2026-02-23T10:01:00+00:00",
    "rows_processed": 250,
    "cost_spent_cents": 45,
}


def _current_get_db_manager():
    """Fetch _get_db_manager from the live module to avoid stale references."""
    return sys.modules[_MODULE_NAME]._get_db_manager


def _app_with_mock_db(
    app,
    *,
    fetch_rows: list | None = None,
    fetchval_result: int | None = 0,
    fetchrow_result: dict | None = None,
    pool_available: bool = True,
    fetch_side_effect: Exception | None = None,
    fetchrow_side_effect: Exception | None = None,
    fetchval_side_effect: Exception | None = None,
    fetchrow_side_effects: list | None = None,
):
    """Build a FastAPI test app with a mocked DatabaseManager.

    ``fetchrow_side_effects`` (list) allows sequencing multiple fetchrow
    return values for endpoints that call pool.fetchrow() more than once.
    """
    mock_pool = AsyncMock()

    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    if fetchval_side_effect is not None:
        mock_pool.fetchval = AsyncMock(side_effect=fetchval_side_effect)
    else:
        mock_pool.fetchval = AsyncMock(return_value=fetchval_result)

    if fetchrow_side_effects is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effects)
    elif fetchrow_side_effect is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    app.dependency_overrides[_current_get_db_manager()] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# GET /api/switchboard/backfill — list jobs
# ---------------------------------------------------------------------------


class TestListBackfillJobs:
    async def test_empty_state_returns_empty_list(self, app):
        """Empty backfill_jobs table returns paginated empty data list."""
        app = _app_with_mock_db(app, fetchval_result=0, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0
        assert body["meta"]["offset"] == 0
        assert "limit" in body["meta"]

    async def test_returns_job_summaries(self, app):
        """Populated backfill_jobs returns BackfillJobSummary items."""
        app = _app_with_mock_db(app, fetchval_result=1, fetch_rows=[_SAMPLE_JOB_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 1
        job = body["data"][0]
        assert job["id"] == _JOB_ID
        assert job["connector_type"] == "gmail"
        assert job["endpoint_identity"] == "user@example.com"
        assert job["status"] == "pending"
        assert job["target_categories"] == ["finance", "health"]
        assert body["meta"]["total"] == 1

    async def test_filter_by_connector_type_accepted(self, app):
        """connector_type filter is accepted without error."""
        app = _app_with_mock_db(app, fetchval_result=0, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill", params={"connector_type": "gmail"})
        assert resp.status_code == 200

    async def test_filter_by_status_accepted(self, app):
        """Valid status filter is accepted."""
        app = _app_with_mock_db(app, fetchval_result=0, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill", params={"status": "active"})
        assert resp.status_code == 200

    async def test_filter_by_invalid_status_returns_422(self, app):
        """Invalid status value returns 422 Unprocessable Entity."""
        app = _app_with_mock_db(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill", params={"status": "bogus_status"})
        assert resp.status_code == 422

    async def test_pagination_params_accepted(self, app):
        """offset and limit query params are accepted."""
        app = _app_with_mock_db(app, fetchval_result=0, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill", params={"offset": 10, "limit": 25})
        assert resp.status_code == 200

    async def test_degraded_db_returns_empty_list(self, app):
        """When DB errors, returns empty list (not 500)."""
        app = _app_with_mock_db(
            app,
            fetch_side_effect=Exception("relation does not exist"),
            fetchval_side_effect=Exception("relation does not exist"),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, returns 503."""
        app = _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill")
        assert resp.status_code == 503

    async def test_active_job_in_list(self, app):
        """Active jobs appear in list with their status and counters."""
        app = _app_with_mock_db(app, fetchval_result=1, fetch_rows=[_ACTIVE_JOB_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill")

        body = resp.json()
        job = body["data"][0]
        assert job["status"] == "active"
        assert job["rows_processed"] == 250
        assert job["cost_spent_cents"] == 45


# ---------------------------------------------------------------------------
# POST /api/switchboard/backfill — create job
# ---------------------------------------------------------------------------


class TestCreateBackfillJob:
    _CREATE_PAYLOAD = {
        "connector_type": "gmail",
        "endpoint_identity": "user@example.com",
        "target_categories": ["finance"],
        "date_from": "2020-01-01",
        "date_to": "2026-01-01",
        "rate_limit_per_hour": 100,
        "daily_cost_cap_cents": 500,
    }

    async def test_creates_job_returns_201(self, app):
        """Successful creation returns 201 with created job data."""
        app = _app_with_mock_db(app, fetchrow_result=_SAMPLE_JOB_ROW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/backfill", json=self._CREATE_PAYLOAD)

        assert resp.status_code == 201
        body = resp.json()
        assert "data" in body
        job = body["data"]
        assert job["connector_type"] == "gmail"
        assert job["endpoint_identity"] == "user@example.com"
        assert job["status"] == "pending"

    async def test_created_job_has_id(self, app):
        """Created job response includes a non-empty id field."""
        app = _app_with_mock_db(app, fetchrow_result=_SAMPLE_JOB_ROW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/backfill", json=self._CREATE_PAYLOAD)

        assert resp.status_code == 201
        assert resp.json()["data"]["id"]

    async def test_missing_required_field_returns_422(self, app):
        """Request body missing required fields returns 422."""
        app = _app_with_mock_db(app)
        payload = {k: v for k, v in self._CREATE_PAYLOAD.items() if k != "connector_type"}
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/backfill", json=payload)
        assert resp.status_code == 422

    async def test_db_error_on_insert_returns_503(self, app):
        """When INSERT fails, returns 503."""
        app = _app_with_mock_db(app, fetchrow_side_effect=Exception("DB write failed"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/backfill", json=self._CREATE_PAYLOAD)
        assert resp.status_code == 503

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, returns 503."""
        app = _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/backfill", json=self._CREATE_PAYLOAD)
        assert resp.status_code == 503

    async def test_default_rate_limits_applied(self, app):
        """Omitting optional fields uses defaults (rate_limit_per_hour=100, cap=500)."""
        minimal_payload = {
            "connector_type": "gmail",
            "endpoint_identity": "user@example.com",
            "date_from": "2020-01-01",
            "date_to": "2026-01-01",
        }
        app = _app_with_mock_db(app, fetchrow_result=_SAMPLE_JOB_ROW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/backfill", json=minimal_payload)

        assert resp.status_code == 201
        job = resp.json()["data"]
        assert job["rate_limit_per_hour"] == 100
        assert job["daily_cost_cap_cents"] == 500


# ---------------------------------------------------------------------------
# GET /api/switchboard/backfill/{job_id} — job detail
# ---------------------------------------------------------------------------


class TestGetBackfillJob:
    async def test_returns_full_job_detail(self, app):
        """When job exists, detail endpoint returns all fields including cursor."""
        active_with_cursor = {
            **_ACTIVE_JOB_ROW,
            "cursor": {"message_id": "msg-500", "page_token": "abc123"},
        }
        app = _app_with_mock_db(app, fetchrow_result=active_with_cursor)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}")

        assert resp.status_code == 200
        body = resp.json()
        job = body["data"]
        assert job["id"] == _JOB_ID
        assert job["status"] == "active"
        assert job["rows_processed"] == 250
        assert job["cursor"] == {"message_id": "msg-500", "page_token": "abc123"}

    async def test_returns_404_when_not_found(self, app):
        """Job not found returns 404."""
        app = _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill/nonexistent-id")
        assert resp.status_code == 404

    async def test_db_error_returns_503(self, app):
        """When DB fetch fails, returns 503."""
        app = _app_with_mock_db(app, fetchrow_side_effect=Exception("DB error"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}")
        assert resp.status_code == 503

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, returns 503."""
        app = _app_with_mock_db(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PATCH /api/switchboard/backfill/{job_id}/pause
# ---------------------------------------------------------------------------


class TestPauseBackfillJob:
    async def test_pauses_active_job(self, app):
        """Active job can be paused; returns status='paused'."""
        app = _app_with_mock_db(app, fetchrow_side_effects=[{"status": "active"}, None])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "paused"
        assert body["data"]["job_id"] == _JOB_ID

    async def test_pauses_pending_job(self, app):
        """Pending job can also be paused."""
        app = _app_with_mock_db(app, fetchrow_side_effects=[{"status": "pending"}, None])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "paused"

    async def test_returns_404_when_not_found(self, app):
        """Pause on nonexistent job returns 404."""
        app = _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/switchboard/backfill/nonexistent/pause")
        assert resp.status_code == 404

    async def test_already_paused_returns_409(self, app):
        """Pausing an already-paused job returns 409 conflict."""
        app = _app_with_mock_db(app, fetchrow_result={"status": "paused"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")
        assert resp.status_code == 409

    async def test_completed_job_returns_409(self, app):
        """Pausing a completed job returns 409."""
        app = _app_with_mock_db(app, fetchrow_result={"status": "completed"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")
        assert resp.status_code == 409

    async def test_db_error_on_fetch_returns_503(self, app):
        """DB error during status fetch returns 503."""
        app = _app_with_mock_db(app, fetchrow_side_effect=Exception("DB error"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PATCH /api/switchboard/backfill/{job_id}/cancel
# ---------------------------------------------------------------------------


class TestCancelBackfillJob:
    async def test_cancels_pending_job(self, app):
        """Pending job can be cancelled."""
        app = _app_with_mock_db(app, fetchrow_side_effects=[{"status": "pending"}, None])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/cancel")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "cancelled"

    async def test_cancels_active_job(self, app):
        """Active job can be cancelled."""
        app = _app_with_mock_db(app, fetchrow_side_effects=[{"status": "active"}, None])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/cancel")
        assert resp.status_code == 200

    async def test_cancels_paused_job(self, app):
        """Paused job can be cancelled."""
        app = _app_with_mock_db(app, fetchrow_side_effects=[{"status": "paused"}, None])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/cancel")
        assert resp.status_code == 200

    async def test_returns_404_when_not_found(self, app):
        """Cancel on nonexistent job returns 404."""
        app = _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/switchboard/backfill/nonexistent/cancel")
        assert resp.status_code == 404

    async def test_completed_job_returns_409(self, app):
        """Cancelling an already-completed job returns 409."""
        app = _app_with_mock_db(app, fetchrow_result={"status": "completed"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/cancel")
        assert resp.status_code == 409

    async def test_already_cancelled_job_returns_409(self, app):
        """Cancelling an already-cancelled job returns 409."""
        app = _app_with_mock_db(app, fetchrow_result={"status": "cancelled"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/cancel")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# PATCH /api/switchboard/backfill/{job_id}/resume
# ---------------------------------------------------------------------------


class TestResumeBackfillJob:
    async def test_resumes_paused_job(self, app):
        """Paused job can be resumed; returns status='pending'."""
        app = _app_with_mock_db(app, fetchrow_side_effects=[{"status": "paused"}, None])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/resume")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["status"] == "pending"
        assert body["data"]["job_id"] == _JOB_ID

    async def test_returns_404_when_not_found(self, app):
        """Resume on nonexistent job returns 404."""
        app = _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch("/api/switchboard/backfill/nonexistent/resume")
        assert resp.status_code == 404

    async def test_active_job_cannot_be_resumed(self, app):
        """Resuming an active (not paused) job returns 409."""
        app = _app_with_mock_db(app, fetchrow_result={"status": "active"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/resume")
        assert resp.status_code == 409

    async def test_completed_job_cannot_be_resumed(self, app):
        """Resuming a completed job returns 409."""
        app = _app_with_mock_db(app, fetchrow_result={"status": "completed"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/resume")
        assert resp.status_code == 409

    async def test_cancelled_job_cannot_be_resumed(self, app):
        """Resuming a cancelled job returns 409."""
        app = _app_with_mock_db(app, fetchrow_result={"status": "cancelled"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/resume")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /api/switchboard/backfill/{job_id}/progress
# ---------------------------------------------------------------------------


class TestGetBackfillJobProgress:
    async def test_returns_progress_metrics(self, app):
        """Progress endpoint returns rows_processed, rows_skipped, cost_spent_cents."""
        app = _app_with_mock_db(app, fetchrow_result=_ACTIVE_JOB_ROW)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}/progress")

        assert resp.status_code == 200
        body = resp.json()
        job = body["data"]
        assert job["rows_processed"] == 250
        assert job["rows_skipped"] == 0
        assert job["cost_spent_cents"] == 45
        assert job["status"] == "active"

    async def test_returns_404_for_nonexistent_job(self, app):
        """Progress for unknown job returns 404."""
        app = _app_with_mock_db(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill/nonexistent-id/progress")
        assert resp.status_code == 404

    async def test_db_error_returns_503(self, app):
        """DB error during progress fetch returns 503."""
        app = _app_with_mock_db(app, fetchrow_side_effect=Exception("DB error"))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}/progress")
        assert resp.status_code == 503

    async def test_completed_job_progress(self, app):
        """Completed job progress returns all terminal fields."""
        completed_row = {
            **_SAMPLE_JOB_ROW,
            "status": "completed",
            "rows_processed": 5000,
            "rows_skipped": 1200,
            "cost_spent_cents": 380,
            "completed_at": "2026-02-23T12:00:00+00:00",
        }
        app = _app_with_mock_db(app, fetchrow_result=completed_row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}/progress")

        assert resp.status_code == 200
        job = resp.json()["data"]
        assert job["status"] == "completed"
        assert job["rows_processed"] == 5000
        assert job["completed_at"] is not None
