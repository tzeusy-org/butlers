"""Tests for the calendar ICS export endpoint (GET /api/calendar/export/ics).

Read-only data-portability export (bu-8yi687): streams the workspace projection
as a valid VCALENDAR via the ``icalendar`` library, preserving the ``BUTLER:``
title prefix, with no provider write and no LLM session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import icalendar
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.routers.calendar_workspace import _get_db_manager

pytestmark = pytest.mark.unit


def _workspace_event_row(
    *,
    lane: str,
    source_key: str,
    source_kind: str,
    butler_name: str | None,
    title: str = "Calendar item",
    all_day: bool = False,
    calendar_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    start = datetime(2026, 2, 22, 14, 0, tzinfo=UTC)
    end = datetime(2026, 2, 22, 15, 0, tzinfo=UTC)
    synced_at = datetime.now(tz=UTC)
    return {
        "instance_id": uuid4(),
        "source_id": uuid4(),
        "source_key": source_key,
        "source_kind": source_kind,
        "lane": lane,
        "provider": "google" if lane == "user" else "internal",
        "calendar_id": calendar_id,
        "butler_name": butler_name,
        "display_name": source_key,
        "writable": True,
        "source_metadata": {"projection": "test"},
        "event_id": uuid4(),
        "origin_ref": str(uuid4()),
        "origin_instance_ref": str(uuid4()),
        "title": title,
        "description": "desc",
        "location": "loc",
        "event_timezone": "UTC",
        "all_day": all_day,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": metadata or {},
        "instance_timezone": "UTC",
        "instance_starts_at": start,
        "instance_ends_at": end,
        "instance_status": "confirmed",
        "instance_metadata": {},
        "cursor_name": "provider_sync" if lane == "user" else "projection",
        "last_synced_at": synced_at,
        "last_success_at": synced_at,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


def _build_app(app, *, workspace_rows: dict[str, list[dict]] | None = None) -> tuple:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=None)

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            rows_to_scan = workspace_rows or {}
            if butler_names is not None:
                rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
            return rows_to_scan
        return {}

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)

    mock_mgr = AsyncMock(spec=MCPClientManager)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_mgr


_RANGE = {"start": "2026-02-22T00:00:00Z", "end": "2026-02-23T00:00:00Z"}


async def test_export_ics_requires_start_and_end(app):
    app, _, _ = _build_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/export/ics")
    assert resp.status_code == 422


async def test_export_ics_returns_valid_vcalendar(app):
    row = _workspace_event_row(
        lane="user",
        source_key="provider:google:primary",
        source_kind="provider_event",
        butler_name=None,
        title="Team sync",
        calendar_id="primary",
        metadata={"source_type": "provider_event"},
    )
    app, _, _ = _build_app(app, workspace_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/export/ics", params={"view": "user", **_RANGE})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.ics"')

    body = resp.text
    assert "BEGIN:VCALENDAR" in body
    assert "BEGIN:VEVENT" in body

    # Parse with the library to confirm it is structurally valid ICS.
    cal = icalendar.Calendar.from_ical(resp.content)
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(events) == 1
    event = events[0]
    assert str(event["summary"]) == "Team sync"
    assert event.get("uid") is not None
    assert event.get("dtstart") is not None
    assert event.get("dtend") is not None


async def test_export_ics_preserves_butler_prefix(app):
    row = _workspace_event_row(
        lane="butler",
        source_key="internal:general:reminders",
        source_kind="internal_reminders",
        butler_name="general",
        title="BUTLER: Daily standup",
        metadata={"source_type": "manual_butler_event"},
    )
    app, _, _ = _build_app(app, workspace_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/export/ics", params={"view": "butler", **_RANGE})

    assert resp.status_code == 200
    cal = icalendar.Calendar.from_ical(resp.content)
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(events) == 1
    # The BUTLER: prefix is preserved verbatim in the exported SUMMARY.
    assert str(events[0]["summary"]) == "BUTLER: Daily standup"


async def test_export_ics_read_only_no_provider_write(app):
    """Export must not touch the provider/MCP surface — it is a pure read."""
    row = _workspace_event_row(
        lane="user",
        source_key="provider:google:primary",
        source_kind="provider_event",
        butler_name=None,
        calendar_id="primary",
        metadata={"source_type": "provider_event"},
    )
    app, mock_db, mock_mgr = _build_app(app, workspace_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/export/ics", params={"view": "user", **_RANGE})

    assert resp.status_code == 200
    # No MCP client is ever requested (no provider write / no LLM session).
    mock_mgr.get_client.assert_not_called()
    # Only the read fan-out is used; no write/execute path is invoked.
    assert mock_db.fan_out.await_count >= 1


async def test_export_ics_empty_range_is_valid_empty_calendar(app):
    app, _, _ = _build_app(app, workspace_rows={})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/export/ics", params={"view": "user", **_RANGE})

    assert resp.status_code == 200
    cal = icalendar.Calendar.from_ical(resp.content)
    assert [c for c in cal.walk() if c.name == "VEVENT"] == []


async def test_export_ics_rejects_inverted_range(app):
    app, _, _ = _build_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/export/ics",
            params={
                "view": "user",
                "start": "2026-02-23T00:00:00Z",
                "end": "2026-02-22T00:00:00Z",
            },
        )
    assert resp.status_code == 400
