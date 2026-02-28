"""Tests for schedules API endpoints.

Verifies the API contract (status codes, response shapes) for schedule
endpoints.  Uses mocked DatabaseManager and MCPClientManager so no real
database or MCP server is required.

Issues: butlers-26h.5.1, 5.2, 5.3
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.routers.schedules import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)
_SCHEDULE_ID = uuid4()


def _make_schedule_record(
    *,
    schedule_id=None,
    name="daily-digest",
    cron="0 9 * * *",
    dispatch_mode="prompt",
    prompt="Send a daily digest",
    job_name=None,
    job_args=None,
    timezone=None,
    start_at=None,
    end_at=None,
    until_at=None,
    display_title=None,
    calendar_event_id=None,
    source="db",
    enabled=True,
    next_run_at=None,
    last_run_at=None,
    created_at=_NOW,
    updated_at=_NOW,
) -> dict:
    """Create a dict mimicking an asyncpg Record for scheduled_tasks columns."""
    return {
        "id": schedule_id or _SCHEDULE_ID,
        "name": name,
        "cron": cron,
        "dispatch_mode": dispatch_mode,
        "prompt": prompt,
        "job_name": job_name,
        "job_args": job_args,
        "timezone": timezone,
        "start_at": start_at,
        "end_at": end_at,
        "until_at": until_at,
        "display_title": display_title,
        "calendar_event_id": calendar_event_id,
        "source": source,
        "enabled": enabled,
        "next_run_at": next_run_at,
        "last_run_at": last_run_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _mock_mcp_result(payload: dict | str = "ok") -> list:
    """Create a mock MCP call_tool result (list of content blocks)."""
    import json

    block = MagicMock()
    if isinstance(payload, dict):
        block.text = json.dumps(payload)
    else:
        block.text = payload
    return [block]


def _app_with_mock_db(
    app: FastAPI,
    *,
    fetch_rows: list | None = None,
) -> FastAPI:
    """Wire a FastAPI app with a mocked DatabaseManager."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas", "switchboard"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


def _app_with_mock_mcp(
    app: FastAPI,
    *,
    call_tool_result=None,
    call_tool_side_effect=None,
) -> tuple:
    """Wire a FastAPI app with a mocked MCPClientManager for write ops."""
    mock_client = AsyncMock()
    if call_tool_side_effect is not None:
        mock_client.call_tool = AsyncMock(side_effect=call_tool_side_effect)
    else:
        mock_client.call_tool = AsyncMock(
            return_value=call_tool_result or _mock_mcp_result({"success": True})
        )

    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    # Also need a DB mock for the app to not fail on other routes
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_client


def _app_with_unreachable_butler(app: FastAPI) -> FastAPI:
    """Wire a FastAPI app where MCP connections fail with 503."""
    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(
        side_effect=ButlerUnreachableError("atlas", cause=ConnectionRefusedError())
    )

    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_pool
    mock_db.butler_names = ["atlas"]

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/schedules — list schedules
# ---------------------------------------------------------------------------


class TestListSchedules:
    async def test_returns_array_of_schedules(self, app):
        """GET should return an ApiResponse wrapping a list of schedules."""
        rows = [
            _make_schedule_record(schedule_id=uuid4(), name="task-a"),
            _make_schedule_record(schedule_id=uuid4(), name="task-b"),
        ]
        _app_with_mock_db(app, fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/schedules")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert len(body["data"]) == 2
        assert body["data"][0]["name"] == "task-a"
        assert body["data"][1]["name"] == "task-b"

    async def test_empty_schedules(self, app):
        """When no schedules exist, return empty list."""
        _app_with_mock_db(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/schedules")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []

    async def test_schedule_fields(self, app):
        """Each schedule should include all expected fields."""
        row = _make_schedule_record()
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/schedules")

        assert resp.status_code == 200
        schedule = resp.json()["data"][0]
        assert schedule["id"] == str(_SCHEDULE_ID)
        assert schedule["name"] == "daily-digest"
        assert schedule["cron"] == "0 9 * * *"
        assert schedule["dispatch_mode"] == "prompt"
        assert schedule["prompt"] == "Send a daily digest"
        assert schedule["job_name"] is None
        assert schedule["job_args"] is None
        assert schedule["timezone"] is None
        assert schedule["start_at"] is None
        assert schedule["end_at"] is None
        assert schedule["until_at"] is None
        assert schedule["display_title"] is None
        assert schedule["calendar_event_id"] is None
        assert schedule["source"] == "db"
        assert schedule["enabled"] is True
        assert "created_at" in schedule
        assert "updated_at" in schedule

    async def test_schedule_fields_for_job_mode(self, app):
        """Job-mode schedules include deterministic dispatch metadata."""
        row = _make_schedule_record(
            schedule_id=uuid4(),
            name="eligibility-sweep",
            dispatch_mode="job",
            prompt=None,
            job_name="eligibility_sweep",
            job_args={"dry_run": True},
        )
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/schedules")

        assert resp.status_code == 200
        schedule = resp.json()["data"][0]
        assert schedule["dispatch_mode"] == "job"
        assert schedule["prompt"] is None
        assert schedule["job_name"] == "eligibility_sweep"
        assert schedule["job_args"] == {"dry_run": True}

    async def test_schedule_fields_for_calendar_projection_linkage(self, app):
        """Schedule rows include calendar linkage columns when present."""
        start_at = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
        end_at = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
        until_at = datetime(2026, 4, 1, 14, 0, tzinfo=UTC)
        calendar_event_id = "abc123def456ghi789jklmnop0"
        row = _make_schedule_record(
            schedule_id=uuid4(),
            name="medication-check",
            timezone="America/New_York",
            start_at=start_at,
            end_at=end_at,
            until_at=until_at,
            display_title="Medication check",
            calendar_event_id=calendar_event_id,
        )
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/schedules")

        assert resp.status_code == 200
        schedule = resp.json()["data"][0]
        assert schedule["timezone"] == "America/New_York"
        assert datetime.fromisoformat(schedule["start_at"].replace("Z", "+00:00")) == start_at
        assert datetime.fromisoformat(schedule["end_at"].replace("Z", "+00:00")) == end_at
        assert datetime.fromisoformat(schedule["until_at"].replace("Z", "+00:00")) == until_at
        assert schedule["display_title"] == "Medication check"
        assert schedule["calendar_event_id"] == calendar_event_id

    async def test_legacy_schedule_row_defaults_to_prompt_mode(self, app):
        """Legacy DB rows without mode columns remain readable."""
        row = {
            "id": uuid4(),
            "name": "legacy-task",
            "cron": "0 8 * * *",
            "prompt": "Run legacy task",
            "source": "db",
            "enabled": True,
            "next_run_at": None,
            "last_run_at": None,
            "created_at": _NOW,
            "updated_at": _NOW,
        }
        _app_with_mock_db(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/schedules")

        assert resp.status_code == 200
        schedule = resp.json()["data"][0]
        assert schedule["dispatch_mode"] == "prompt"
        assert schedule["prompt"] == "Run legacy task"
        assert schedule["job_name"] is None
        assert schedule["job_args"] is None

    async def test_butler_db_unavailable_returns_503(self, app):
        """When the butler's DB pool doesn't exist, return 503."""
        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.side_effect = KeyError("no pool")
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/nonexistent/schedules")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/schedules — create schedule via MCP
# ---------------------------------------------------------------------------


class TestCreateSchedule:
    async def test_creates_schedule_via_mcp(self, app):
        """POST should proxy create through MCP and return 201."""
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"id": str(uuid4()), "name": "new-task"})
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/butlers/atlas/schedules",
                json={"name": "new-task", "cron": "*/5 * * * *", "prompt": "do stuff"},
            )

        assert resp.status_code == 201
        body = resp.json()
        assert "data" in body
        assert body["data"]["name"] == "new-task"

        # Verify MCP tool was called correctly
        mock_client.call_tool.assert_called_once_with(
            "schedule_create",
            {"name": "new-task", "cron": "*/5 * * * *", "prompt": "do stuff"},
        )

    async def test_creates_job_schedule_via_mcp(self, app):
        """POST supports deterministic job-mode schedule creation."""
        _, mock_client = _app_with_mock_mcp(
            app,
            call_tool_result=_mock_mcp_result(
                {
                    "id": str(uuid4()),
                    "dispatch_mode": "job",
                    "job_name": "eligibility_sweep",
                    "job_args": {"dry_run": True},
                }
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/butlers/atlas/schedules",
                json={
                    "name": "eligibility-sweep",
                    "cron": "*/5 * * * *",
                    "dispatch_mode": "job",
                    "job_name": "eligibility_sweep",
                    "job_args": {"dry_run": True},
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["dispatch_mode"] == "job"
        assert body["data"]["job_name"] == "eligibility_sweep"
        assert body["data"]["job_args"] == {"dry_run": True}

        mock_client.call_tool.assert_called_once_with(
            "schedule_create",
            {
                "name": "eligibility-sweep",
                "cron": "*/5 * * * *",
                "dispatch_mode": "job",
                "job_name": "eligibility_sweep",
                "job_args": {"dry_run": True},
            },
        )

    async def test_creates_schedule_with_projection_linkage_fields(self, app):
        """POST forwards projection linkage fields with JSON-safe encoding."""
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"id": str(uuid4()), "status": "created"})
        )
        start_at = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
        end_at = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
        until_at = datetime(2026, 4, 1, 14, 0, tzinfo=UTC)
        calendar_event_id = uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/butlers/atlas/schedules",
                json={
                    "name": "medication-reminder",
                    "cron": "0 9 * * *",
                    "prompt": "Take meds",
                    "timezone": "America/New_York",
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                    "until_at": until_at.isoformat(),
                    "display_title": "Medication Reminder",
                    "calendar_event_id": str(calendar_event_id),
                },
            )

        assert resp.status_code == 201
        mock_client.call_tool.assert_called_once_with(
            "schedule_create",
            {
                "name": "medication-reminder",
                "cron": "0 9 * * *",
                "prompt": "Take meds",
                "timezone": "America/New_York",
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "until_at": until_at.isoformat(),
                "display_title": "Medication Reminder",
                "calendar_event_id": str(calendar_event_id),
            },
        )

    async def test_create_rejects_naive_projection_datetimes(self, app):
        """POST rejects naive projection timestamps at the API validation boundary."""
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"id": str(uuid4()), "status": "created"})
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/butlers/atlas/schedules",
                json={
                    "name": "timezone-validation",
                    "cron": "0 9 * * *",
                    "prompt": "validate timezone awareness",
                    "start_at": "2026-03-01T14:00:00",
                },
            )

        assert resp.status_code == 422
        mock_client.call_tool.assert_not_called()

    async def test_butler_unreachable_returns_503(self, app):
        """When butler is unreachable, POST returns 503."""
        _app_with_unreachable_butler(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/butlers/atlas/schedules",
                json={"name": "new-task", "cron": "0 * * * *", "prompt": "test"},
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/schedules/{id} — update schedule via MCP
# ---------------------------------------------------------------------------


class TestUpdateSchedule:
    async def test_updates_schedule_via_mcp(self, app):
        """PUT should proxy update through MCP."""
        sid = uuid4()
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"id": str(sid), "updated": True})
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/api/butlers/atlas/schedules/{sid}",
                json={"cron": "0 12 * * *", "prompt": "updated prompt"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["updated"] is True

        # Verify the MCP tool received the schedule id and update fields
        mock_client.call_tool.assert_called_once_with(
            "schedule_update",
            {"id": str(sid), "cron": "0 12 * * *", "prompt": "updated prompt"},
        )

    async def test_updates_schedule_job_fields_via_mcp(self, app):
        """PUT supports dispatch mode and deterministic metadata updates."""
        sid = uuid4()
        _, mock_client = _app_with_mock_mcp(
            app,
            call_tool_result=_mock_mcp_result(
                {
                    "id": str(sid),
                    "dispatch_mode": "job",
                    "job_name": "eligibility_sweep",
                    "job_args": {"dry_run": True},
                }
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/api/butlers/atlas/schedules/{sid}",
                json={
                    "dispatch_mode": "job",
                    "job_name": "eligibility_sweep",
                    "job_args": {"dry_run": True},
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["dispatch_mode"] == "job"
        assert body["data"]["job_name"] == "eligibility_sweep"
        assert body["data"]["job_args"] == {"dry_run": True}

        mock_client.call_tool.assert_called_once_with(
            "schedule_update",
            {
                "id": str(sid),
                "dispatch_mode": "job",
                "job_name": "eligibility_sweep",
                "job_args": {"dry_run": True},
            },
        )

    async def test_updates_schedule_projection_linkage_fields_via_mcp(self, app):
        """PUT forwards projection linkage updates with JSON-safe encoding."""
        sid = uuid4()
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"id": str(sid), "status": "updated"})
        )
        start_at = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
        end_at = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)
        until_at = datetime(2026, 4, 2, 14, 0, tzinfo=UTC)
        calendar_event_id = uuid4()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/api/butlers/atlas/schedules/{sid}",
                json={
                    "timezone": "America/Chicago",
                    "start_at": start_at.isoformat(),
                    "end_at": end_at.isoformat(),
                    "until_at": until_at.isoformat(),
                    "display_title": "Updated reminder",
                    "calendar_event_id": str(calendar_event_id),
                },
            )

        assert resp.status_code == 200
        mock_client.call_tool.assert_called_once_with(
            "schedule_update",
            {
                "id": str(sid),
                "timezone": "America/Chicago",
                "start_at": start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "until_at": until_at.isoformat(),
                "display_title": "Updated reminder",
                "calendar_event_id": str(calendar_event_id),
            },
        )

    async def test_update_rejects_naive_projection_datetimes(self, app):
        """PUT rejects naive projection timestamps at the API validation boundary."""
        sid = uuid4()
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"id": str(sid), "status": "updated"})
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/api/butlers/atlas/schedules/{sid}",
                json={"start_at": "2026-03-02T14:00:00"},
            )

        assert resp.status_code == 422
        mock_client.call_tool.assert_not_called()

    async def test_butler_unreachable_returns_503(self, app):
        """When butler is unreachable, PUT returns 503."""
        _app_with_unreachable_butler(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                f"/api/butlers/atlas/schedules/{uuid4()}",
                json={"cron": "0 12 * * *"},
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DELETE /api/butlers/{name}/schedules/{id} — delete schedule via MCP
# ---------------------------------------------------------------------------


class TestDeleteSchedule:
    async def test_deletes_schedule_via_mcp(self, app):
        """DELETE should proxy delete through MCP."""
        sid = uuid4()
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"deleted": True})
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/butlers/atlas/schedules/{sid}")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["deleted"] is True

        mock_client.call_tool.assert_called_once_with(
            "schedule_delete",
            {"id": str(sid)},
        )

    async def test_butler_unreachable_returns_503(self, app):
        """When butler is unreachable, DELETE returns 503."""
        _app_with_unreachable_butler(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/butlers/atlas/schedules/{uuid4()}")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PATCH /api/butlers/{name}/schedules/{id}/toggle — toggle via MCP
# ---------------------------------------------------------------------------


class TestToggleSchedule:
    async def test_toggles_schedule_via_mcp(self, app):
        """PATCH toggle should proxy through MCP."""
        sid = uuid4()
        _, mock_client = _app_with_mock_mcp(
            app, call_tool_result=_mock_mcp_result({"enabled": False})
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/butlers/atlas/schedules/{sid}/toggle")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert body["data"]["enabled"] is False

        mock_client.call_tool.assert_called_once_with(
            "schedule_toggle",
            {"id": str(sid)},
        )

    async def test_toggles_job_schedule_via_mcp(self, app):
        """PATCH toggle keeps job-mode payloads round-trippable."""
        sid = uuid4()
        _, mock_client = _app_with_mock_mcp(
            app,
            call_tool_result=_mock_mcp_result(
                {
                    "enabled": True,
                    "dispatch_mode": "job",
                    "job_name": "eligibility_sweep",
                    "job_args": {"dry_run": True},
                }
            ),
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/butlers/atlas/schedules/{sid}/toggle")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["enabled"] is True
        assert body["data"]["dispatch_mode"] == "job"
        assert body["data"]["job_name"] == "eligibility_sweep"
        assert body["data"]["job_args"] == {"dry_run": True}
        mock_client.call_tool.assert_called_once_with("schedule_toggle", {"id": str(sid)})

    async def test_butler_unreachable_returns_503(self, app):
        """When butler is unreachable, PATCH toggle returns 503."""
        _app_with_unreachable_butler(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/butlers/atlas/schedules/{uuid4()}/toggle")

        assert resp.status_code == 503
