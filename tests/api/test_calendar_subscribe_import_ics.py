"""Tests for the calendar ICS subscribe feed and .ics import-with-dedup.

Follow-up to the one-shot ICS export (bu-t2zxj):

- ``GET /api/calendar/subscribe.ics`` re-renders the current workspace entries as
  a live ``text/calendar`` feed (inline, no provider write, no LLM).
- ``POST /api/calendar/import/ics`` parses an uploaded ``.ics`` and creates its
  events via the ``calendar_create_event`` MCP path, deduped against existing
  workspace entries using the read-model's ``(title, starts_epoch)`` collapse
  key, so re-importing the same file is a no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    title: str,
    start: datetime,
    end: datetime,
    calendar_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
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
        "all_day": False,
        "event_status": "confirmed",
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": metadata or {"source_type": "provider_event"},
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

    # MCP client whose call_tool returns a minimal create-event payload.
    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value={"content": [{"type": "text", "text": '{"event_id": "evt-1"}'}]}
    )
    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_mgr, mock_client


def _build_ics(events: list[tuple[str, datetime, datetime]]) -> bytes:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Test//EN")
    cal.add("version", "2.0")
    for title, start, end in events:
        ev = icalendar.Event()
        ev.add("uid", f"{title}-{int(start.timestamp())}@test")
        ev.add("summary", title)
        ev.add("dtstart", start)
        ev.add("dtend", end)
        ev.add("dtstamp", start)
        cal.add_component(ev)
    return cal.to_ical()


# --------------------------------------------------------------------------- #
# Subscribe feed
# --------------------------------------------------------------------------- #


async def test_subscribe_rerenders_current_entries(app):
    """The feed re-renders the live workspace entries as an inline VCALENDAR."""
    now = datetime.now(UTC)
    start = now + timedelta(days=1)
    row = _workspace_event_row(
        lane="user",
        source_key="provider:google:primary",
        source_kind="provider_event",
        butler_name=None,
        title="Team sync",
        start=start,
        end=start + timedelta(hours=1),
        calendar_id="primary",
    )
    app, _, _, _ = _build_app(app, workspace_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/subscribe.ics", params={"view": "user"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/calendar")
    # A subscription feed, not a download.
    assert resp.headers["content-disposition"].startswith("inline")

    cal = icalendar.Calendar.from_ical(resp.content)
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(events) == 1
    assert str(events[0]["summary"]) == "Team sync"


async def test_subscribe_preserves_butler_prefix(app):
    now = datetime.now(UTC)
    start = now + timedelta(days=2)
    row = _workspace_event_row(
        lane="butler",
        source_key="internal:general:reminders",
        source_kind="internal_reminders",
        butler_name="general",
        title="BUTLER: Daily standup",
        start=start,
        end=start + timedelta(minutes=15),
        metadata={"source_type": "manual_butler_event"},
    )
    app, _, _, _ = _build_app(app, workspace_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/subscribe.ics", params={"view": "butler"})

    assert resp.status_code == 200
    cal = icalendar.Calendar.from_ical(resp.content)
    events = [c for c in cal.walk() if c.name == "VEVENT"]
    assert str(events[0]["summary"]) == "BUTLER: Daily standup"


async def test_subscribe_read_only_no_provider_write(app):
    now = datetime.now(UTC)
    start = now + timedelta(days=1)
    row = _workspace_event_row(
        lane="user",
        source_key="provider:google:primary",
        source_kind="provider_event",
        butler_name=None,
        title="Team sync",
        start=start,
        end=start + timedelta(hours=1),
        calendar_id="primary",
    )
    app, mock_db, mock_mgr, _ = _build_app(app, workspace_rows={"general": [row]})

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/calendar/subscribe.ics", params={"view": "user"})

    assert resp.status_code == 200
    mock_mgr.get_client.assert_not_called()
    assert mock_db.fan_out.await_count >= 1


async def test_subscribe_rejects_unknown_facet(app):
    app, _, _, _ = _build_app(app, workspace_rows={})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/calendar/subscribe.ics", params={"view": "user", "status": "bogus"}
        )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Import-with-dedup
# --------------------------------------------------------------------------- #


async def test_import_creates_new_events(app):
    """Events not present in the workspace are created via calendar_create_event."""
    app, _, _, mock_client = _build_app(app, workspace_rows={})

    start = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
    ics = _build_ics(
        [
            ("Lunch", start, start + timedelta(hours=1)),
            ("Gym", start + timedelta(hours=4), start + timedelta(hours=5)),
        ]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/import/ics",
            data={"butler_name": "general"},
            files={"file": ("cal.ics", ics, "text/calendar")},
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["parsed"] == 2
    assert body["imported"] == 2
    assert body["skipped_duplicates"] == 0
    assert mock_client.call_tool.await_count == 2
    # Every create routes through the blessed calendar_create_event MCP tool.
    assert all(
        call.args[0] == "calendar_create_event" for call in mock_client.call_tool.await_args_list
    )


async def test_import_dedups_against_existing_entries(app):
    """An event already present in the workspace is skipped, not duplicated."""
    start = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
    existing = _workspace_event_row(
        lane="user",
        source_key="provider:google:primary",
        source_kind="provider_event",
        butler_name=None,
        title="Lunch",
        start=start,
        end=start + timedelta(hours=1),
        calendar_id="primary",
    )
    app, _, _, mock_client = _build_app(app, workspace_rows={"general": [existing]})

    ics = _build_ics(
        [
            ("Lunch", start, start + timedelta(hours=1)),  # duplicate of existing
            ("Gym", start + timedelta(hours=4), start + timedelta(hours=5)),  # new
        ]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/import/ics",
            data={"butler_name": "general"},
            files={"file": ("cal.ics", ics, "text/calendar")},
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["parsed"] == 2
    assert body["imported"] == 1
    assert body["skipped_duplicates"] == 1
    # Only the genuinely-new event was created.
    assert mock_client.call_tool.await_count == 1


async def test_reimport_same_ics_is_noop(app):
    """Re-importing a file whose events all already exist creates nothing."""
    start = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
    rows = [
        _workspace_event_row(
            lane="user",
            source_key="provider:google:primary",
            source_kind="provider_event",
            butler_name=None,
            title="Lunch",
            start=start,
            end=start + timedelta(hours=1),
            calendar_id="primary",
        ),
        _workspace_event_row(
            lane="user",
            source_key="provider:google:primary",
            source_kind="provider_event",
            butler_name=None,
            title="Gym",
            start=start + timedelta(hours=4),
            end=start + timedelta(hours=5),
            calendar_id="primary",
        ),
    ]
    app, _, _, mock_client = _build_app(app, workspace_rows={"general": rows})

    ics = _build_ics(
        [
            ("Lunch", start, start + timedelta(hours=1)),
            ("Gym", start + timedelta(hours=4), start + timedelta(hours=5)),
        ]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/import/ics",
            data={"butler_name": "general"},
            files={"file": ("cal.ics", ics, "text/calendar")},
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["parsed"] == 2
    assert body["imported"] == 0
    assert body["skipped_duplicates"] == 2
    mock_client.call_tool.assert_not_called()


async def test_import_collapses_duplicates_within_file(app):
    """Two identical VEVENTs in one upload create a single event."""
    app, _, _, mock_client = _build_app(app, workspace_rows={})

    start = datetime(2026, 3, 1, 14, 0, tzinfo=UTC)
    ics = _build_ics(
        [
            ("Lunch", start, start + timedelta(hours=1)),
            ("Lunch", start, start + timedelta(hours=1)),
        ]
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/import/ics",
            data={"butler_name": "general"},
            files={"file": ("cal.ics", ics, "text/calendar")},
        )

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["parsed"] == 2
    assert body["imported"] == 1
    assert body["skipped_duplicates"] == 1
    assert mock_client.call_tool.await_count == 1


async def test_import_rejects_empty_file(app):
    app, _, _, mock_client = _build_app(app, workspace_rows={})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/import/ics",
            data={"butler_name": "general"},
            files={"file": ("cal.ics", b"", "text/calendar")},
        )
    assert resp.status_code == 400
    mock_client.call_tool.assert_not_called()


async def test_import_rejects_invalid_payload(app):
    app, _, _, mock_client = _build_app(app, workspace_rows={})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/calendar/import/ics",
            data={"butler_name": "general"},
            files={"file": ("cal.ics", b"this is not ics", "text/calendar")},
        )
    assert resp.status_code == 400
    mock_client.call_tool.assert_not_called()
