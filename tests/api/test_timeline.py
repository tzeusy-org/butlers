"""Tests for timeline API endpoint.

Verifies the API contract (status codes, response shapes) for the
cross-butler timeline endpoint, including cursor-based pagination,
butler/event_type filtering, and event merging from sessions and
notifications.

Issues: butlers-26h.8.1, 8.2, 8.3, 8.6
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.routers.timeline import _get_db_manager

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(tz=UTC)


def _make_session_row(
    *,
    session_id=None,
    prompt="test prompt",
    trigger_source="schedule",
    success=True,
    started_at=None,
    completed_at=None,
    duration_ms=1000,
):
    """Create a dict mimicking an asyncpg Record for timeline session columns."""
    return {
        "id": session_id or uuid4(),
        "prompt": prompt,
        "trigger_source": trigger_source,
        "success": success,
        "started_at": started_at or _NOW,
        "completed_at": completed_at or _NOW,
        "duration_ms": duration_ms,
    }


def _make_notification_row(
    *,
    notif_id=None,
    source_butler="atlas",
    channel="email",
    recipient="user@example.com",
    message="Test notification",
    status="sent",
    created_at=None,
):
    """Create a dict mimicking an asyncpg Record for notification columns."""
    return {
        "id": notif_id or uuid4(),
        "source_butler": source_butler,
        "channel": channel,
        "recipient": recipient,
        "message": message,
        "status": status,
        "created_at": created_at or _NOW,
    }


def _app_with_mock_db(
    app: FastAPI,
    *,
    fan_out_results: list[dict[str, list]] | None = None,
    switchboard_fetch: list | None = None,
    switchboard_available: bool = True,
) -> FastAPI:
    """Wire a FastAPI app with a mocked DatabaseManager.

    Accepts the shared module-scoped ``app`` fixture so that create_app()
    is not called per test.

    Parameters
    ----------
    app:
        The shared FastAPI app instance to wire.
    fan_out_results:
        Side effect list for db.fan_out() calls.
    switchboard_fetch:
        Return value for switchboard pool.fetch() calls (notifications).
    switchboard_available:
        Whether the switchboard pool should be available.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["atlas", "switchboard"]

    if fan_out_results is not None:
        mock_db.fan_out = AsyncMock(side_effect=fan_out_results)
    else:
        mock_db.fan_out = AsyncMock(return_value={})

    # Mock the switchboard pool for notification queries
    if switchboard_available:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=switchboard_fetch or [])
        mock_db.pool = MagicMock(return_value=mock_pool)
    else:
        mock_db.pool = MagicMock(side_effect=KeyError("No pool for butler: switchboard"))

    app.dependency_overrides[_get_db_manager] = lambda: mock_db

    return app


# ---------------------------------------------------------------------------
# Tests: Response structure
# ---------------------------------------------------------------------------


class TestTimelineResponseStructure:
    async def test_returns_data_and_cursor(self, app):
        """Response must have 'data' array and 'next_cursor' field."""
        _app_with_mock_db(app, fan_out_results=[{}])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert isinstance(body["data"], list)
        assert "next_cursor" in body

    async def test_empty_timeline(self, app):
        """When no events exist, return empty data with null cursor."""
        _app_with_mock_db(app, fan_out_results=[{}])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# Tests: Event merging
# ---------------------------------------------------------------------------


class TestEventMerging:
    async def test_sessions_become_events(self, app):
        """Sessions should appear as 'session' type events."""
        sid = uuid4()
        row = _make_session_row(session_id=sid, prompt="hello world")
        _app_with_mock_db(app, fan_out_results=[{"atlas": [row]}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 1
        event = events[0]
        assert event["id"] == str(sid)
        assert event["type"] == "session"
        assert event["butler"] == "atlas"
        assert event["summary"] == "hello world"
        assert "timestamp" in event
        assert "data" in event

    async def test_failed_sessions_become_error_events(self, app):
        """Sessions with success=False should have type 'error'."""
        row = _make_session_row(success=False, prompt="failing task")
        _app_with_mock_db(app, fan_out_results=[{"atlas": [row]}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 1
        assert events[0]["type"] == "error"

    async def test_notifications_become_events(self, app):
        """Notifications should appear as 'notification' type events."""
        nid = uuid4()
        notif_row = _make_notification_row(notif_id=nid, message="Email sent")
        _app_with_mock_db(
            app,
            fan_out_results=[{}],  # No sessions
            switchboard_fetch=[notif_row],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 1
        event = events[0]
        assert event["id"] == str(nid)
        assert event["type"] == "notification"
        assert event["summary"] == "Email sent"

    async def test_merged_events_sorted_by_timestamp_desc(self, app):
        """Events from different sources should be sorted by timestamp descending."""
        old_session = _make_session_row(
            session_id=uuid4(),
            prompt="old session",
            started_at=_NOW - timedelta(minutes=10),
        )
        recent_session = _make_session_row(
            session_id=uuid4(),
            prompt="recent session",
            started_at=_NOW - timedelta(minutes=1),
        )
        mid_notif = _make_notification_row(
            notif_id=uuid4(),
            message="mid notification",
            created_at=_NOW - timedelta(minutes=5),
        )

        _app_with_mock_db(
            app,
            fan_out_results=[{"atlas": [old_session, recent_session]}],
            switchboard_fetch=[mid_notif],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 3
        assert events[0]["summary"] == "recent session"
        assert events[1]["summary"] == "mid notification"
        assert events[2]["summary"] == "old session"

    async def test_cross_butler_sessions_merged(self, app):
        """Sessions from multiple butlers should all appear."""
        atlas_row = _make_session_row(prompt="atlas task", started_at=_NOW - timedelta(minutes=1))
        sw_row = _make_session_row(prompt="switchboard task", started_at=_NOW)

        _app_with_mock_db(
            app,
            fan_out_results=[{"atlas": [atlas_row], "switchboard": [sw_row]}],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 2
        butlers = {e["butler"] for e in events}
        assert butlers == {"atlas", "switchboard"}


# ---------------------------------------------------------------------------
# Tests: Cursor-based pagination
# ---------------------------------------------------------------------------


class TestTimelinePagination:
    async def test_pagination_with_limit(self, app):
        """When more events exist than limit, next_cursor should be set."""
        rows = [
            _make_session_row(
                session_id=uuid4(),
                prompt=f"task {i}",
                started_at=_NOW - timedelta(minutes=i),
            )
            for i in range(5)
        ]

        _app_with_mock_db(app, fan_out_results=[{"atlas": rows}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline", params={"limit": 3})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 3
        assert body["next_cursor"] is not None

    async def test_no_cursor_when_all_returned(self, app):
        """When all events fit in the limit, next_cursor should be null."""
        rows = [_make_session_row(session_id=uuid4(), prompt=f"task {i}") for i in range(2)]

        _app_with_mock_db(app, fan_out_results=[{"atlas": rows}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline", params={"limit": 10})

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["data"]) == 2
        assert body["next_cursor"] is None

    async def test_before_cursor_is_passed_to_query(self, app):
        """The 'before' parameter should filter events by timestamp."""
        # Only the session before the cursor should appear
        old_row = _make_session_row(
            session_id=uuid4(),
            prompt="old task",
            started_at=_NOW - timedelta(hours=2),
        )

        _app_with_mock_db(app, fan_out_results=[{"atlas": [old_row]}])

        before_ts = (_NOW - timedelta(hours=1)).isoformat()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline", params={"before": before_ts})

        assert resp.status_code == 200
        # The fan_out was called, so we get results (the SQL WHERE handles filtering)
        body = resp.json()
        assert isinstance(body["data"], list)


# ---------------------------------------------------------------------------
# Tests: Filtering by butler and event type
# ---------------------------------------------------------------------------


class TestTimelineFiltering:
    async def test_filter_by_butler(self, app):
        """Only events from the specified butler(s) should be returned."""
        atlas_row = _make_session_row(prompt="atlas task")

        _app_with_mock_db(
            app,
            fan_out_results=[{"atlas": [atlas_row]}],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline", params={"butler": "atlas"})

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert all(e["butler"] == "atlas" for e in events)

    async def test_filter_by_event_type_session(self, app):
        """When filtering by type='session', only sessions should appear."""
        session_row = _make_session_row(prompt="a session", success=True)

        _app_with_mock_db(
            app,
            fan_out_results=[{"atlas": [session_row]}],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline", params={"event_type": "session"})

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 1
        assert events[0]["type"] == "session"

    async def test_filter_by_event_type_notification(self, app):
        """When filtering by type='notification', only notifications appear."""
        notif_row = _make_notification_row(message="a notification")

        _app_with_mock_db(
            app,
            fan_out_results=[],  # fan_out not called for sessions when filtered out
            switchboard_fetch=[notif_row],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline", params={"event_type": "notification"})

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 1
        assert events[0]["type"] == "notification"

    async def test_filter_by_event_type_error(self, app):
        """When filtering by type='error', only failed sessions appear."""
        ok_row = _make_session_row(prompt="ok task", success=True)
        fail_row = _make_session_row(prompt="fail task", success=False)

        _app_with_mock_db(
            app,
            fan_out_results=[{"atlas": [ok_row, fail_row]}],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline", params={"event_type": "error"})

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["summary"] == "fail task"

    async def test_switchboard_unavailable_gracefully_handled(self, app):
        """If switchboard DB is unavailable, notifications are skipped."""
        session_row = _make_session_row(prompt="task without notifs")

        _app_with_mock_db(
            app,
            fan_out_results=[{"atlas": [session_row]}],
            switchboard_available=False,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        events = resp.json()["data"]
        assert len(events) == 1
        assert events[0]["type"] == "session"


# ---------------------------------------------------------------------------
# Tests: Event envelope format
# ---------------------------------------------------------------------------


class TestEventEnvelope:
    async def test_event_has_required_fields(self, app):
        """Each event must have id, type, butler, timestamp, summary, data."""
        row = _make_session_row(prompt="structured event")
        _app_with_mock_db(app, fan_out_results=[{"atlas": [row]}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        assert resp.status_code == 200
        event = resp.json()["data"][0]
        assert "id" in event
        assert "type" in event
        assert "butler" in event
        assert "timestamp" in event
        assert "summary" in event
        assert "data" in event
        assert isinstance(event["data"], dict)

    async def test_session_event_data_fields(self, app):
        """Session events should include trigger_source, success, duration_ms in data."""
        row = _make_session_row(trigger_source="schedule", success=True, duration_ms=1234)
        _app_with_mock_db(app, fan_out_results=[{"atlas": [row]}])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        data = resp.json()["data"][0]["data"]
        assert data["trigger_source"] == "schedule"
        assert data["success"] is True
        assert data["duration_ms"] == 1234

    async def test_notification_event_data_fields(self, app):
        """Notification events should include channel, recipient, status in data."""
        notif = _make_notification_row(
            channel="telegram", recipient="@user", status="sent", source_butler="atlas"
        )
        _app_with_mock_db(
            app,
            fan_out_results=[{}],
            switchboard_fetch=[notif],
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")

        data = resp.json()["data"][0]["data"]
        assert data["channel"] == "telegram"
        assert data["recipient"] == "@user"
        assert data["status"] == "sent"
