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


# --------------------------------------------------------------------------- #
# Wide-span dedup pre-fetch chunking (bu-5xbpm)
# --------------------------------------------------------------------------- #

_DEDUP_WINDOW = timedelta(days=90)


def _collect_dedup_windows(mock_db) -> list[tuple[datetime, datetime]]:
    """Extract the ``(start, end)`` dedup pre-fetch windows from fan_out calls.

    The read-model passes ``args = (view, end, start, ...)`` (see
    ``query_calendar_workspace``), so each workspace query reveals the time
    window it scanned.
    """
    windows: list[tuple[datetime, datetime]] = []
    for call in mock_db.fan_out.await_args_list:
        sql = call.args[0]
        if "FROM calendar_event_instances AS i" not in sql:
            continue
        query_args = call.args[1]
        end, start = query_args[1], query_args[2]
        windows.append((start, end))
    return windows


def _build_filtering_app(app, *, rows_by_butler: dict[str, list[dict]]) -> tuple:
    """Like ``_build_app`` but the workspace fan_out honours the window bounds.

    Replicates the read-model overlap filter (``ends_at > start AND
    starts_at < end``) so a windowed pre-fetch only sees rows inside each window
    — letting the parity test prove the union over windows still catches every
    existing entry scattered across a wide span.
    """
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=None)

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" not in query:
            return {}
        end, start = args[1], args[2]
        rows_to_scan = rows_by_butler
        if butler_names is not None:
            rows_to_scan = {k: v for k, v in rows_to_scan.items() if k in butler_names}
        filtered: dict[str, list[dict]] = {}
        for butler, rows in rows_to_scan.items():
            kept = [
                r for r in rows if r["instance_ends_at"] > start and r["instance_starts_at"] < end
            ]
            if kept:
                filtered[butler] = kept
        return filtered

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)

    mock_client = AsyncMock()
    mock_client.call_tool = AsyncMock(
        return_value={"content": [{"type": "text", "text": '{"event_id": "evt-1"}'}]}
    )
    mock_mgr = AsyncMock(spec=MCPClientManager)
    mock_mgr.get_client = AsyncMock(return_value=mock_client)

    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db, mock_mgr, mock_client


async def test_import_wide_span_chunks_dedup_prefetch(app):
    """A multi-month .ics dedups via several bounded windows, not one big query."""
    app, mock_db, _, _ = _build_app(app, workspace_rows={})

    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    # ~9 months of monthly events → span far exceeds the 90-day dedup window.
    specs = [
        (
            f"Event-{i}",
            base + timedelta(days=30 * i),
            base + timedelta(days=30 * i, hours=1),
        )
        for i in range(10)
    ]
    ics = _build_ics(specs)

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
    assert body["parsed"] == 10
    assert body["imported"] == 10
    assert body["skipped_duplicates"] == 0

    windows = _collect_dedup_windows(mock_db)
    # Wide span is bucketed into >1 bounded window — never one unbounded query.
    assert len(windows) > 1
    # Non-terminal windows are extended by 1 ms to cover zero-duration boundary events;
    # the terminal window keeps its exact end so the cap still holds.
    assert all((end - start) <= _DEDUP_WINDOW + timedelta(milliseconds=1) for start, end in windows)
    # Window *starts* tile the full span: no gaps between starts.
    windows.sort()
    range_start = min(s for _, s, _ in specs)
    range_end = max(e for _, _, e in specs) + timedelta(seconds=1)
    assert windows[0][0] == range_start
    assert windows[-1][1] == range_end
    for i, ((_, prev_end), (next_start, _)) in enumerate(zip(windows, windows[1:])):
        # prev_end is either exactly next_start (terminal-adjacent) or 1 ms past it.
        assert prev_end - next_start <= timedelta(milliseconds=1), (
            f"gap/overlap at window {i}: prev_end={prev_end} next_start={next_start}"
        )


async def test_import_wide_span_reimport_is_noop_with_windowed_fetch(app):
    """Result parity: a wide-span re-import skips every event even when existing
    rows are scattered across distinct dedup windows."""
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    specs = [
        (
            f"Event-{i}",
            base + timedelta(days=45 * i),
            base + timedelta(days=45 * i, hours=1),
        )
        for i in range(8)
    ]
    rows = [
        _workspace_event_row(
            lane="user",
            source_key="provider:google:primary",
            source_kind="provider_event",
            butler_name=None,
            title=title,
            start=start,
            end=end,
            calendar_id="primary",
        )
        for title, start, end in specs
    ]
    app, mock_db, _, mock_client = _build_filtering_app(app, rows_by_butler={"general": rows})

    ics = _build_ics(specs)

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
    assert body["parsed"] == 8
    assert body["imported"] == 0
    assert body["skipped_duplicates"] == 8
    # No event recreated despite each existing row living in a different window.
    mock_client.call_tool.assert_not_called()
    # The pre-fetch was genuinely chunked (multiple bounded windows).
    assert len(_collect_dedup_windows(mock_db)) > 1


async def test_import_wide_span_partial_dedup_with_windowed_fetch(app):
    """Across a wide span, only events matching an existing row are skipped; the
    rest are created — proving windowed dedup is neither over- nor under-zealous."""
    base = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    # Existing rows in window 1 (day 0) and a far later window (day ~180).
    existing_specs = [
        ("Kickoff", base, base + timedelta(hours=1)),
        ("Review", base + timedelta(days=180), base + timedelta(days=180, hours=1)),
    ]
    rows = [
        _workspace_event_row(
            lane="user",
            source_key="provider:google:primary",
            source_kind="provider_event",
            butler_name=None,
            title=title,
            start=start,
            end=end,
            calendar_id="primary",
        )
        for title, start, end in existing_specs
    ]
    app, mock_db, _, mock_client = _build_filtering_app(app, rows_by_butler={"general": rows})

    import_specs = [
        ("Kickoff", base, base + timedelta(hours=1)),  # dup (window 1)
        ("Standup", base + timedelta(days=90), base + timedelta(days=90, hours=1)),  # new
        ("Review", base + timedelta(days=180), base + timedelta(days=180, hours=1)),  # dup (later)
    ]
    ics = _build_ics(import_specs)

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
    assert body["parsed"] == 3
    assert body["imported"] == 1
    assert body["skipped_duplicates"] == 2
    # Only the genuinely-new "Standup" routed through the create path.
    assert mock_client.call_tool.await_count == 1


def test_ics_dedup_windows_tiles_contiguously_and_bounds_width():
    """Unit-level guard on the windowing helper: start/end bounds, width cap, start spacing."""
    from butlers.api.routers.calendar_workspace import (
        _ICS_DEDUP_WINDOW_OVERLAP,
        _ics_dedup_windows,
    )

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=400)
    width = timedelta(days=90)
    windows = _ics_dedup_windows(start, end, width=width)

    # Outer bounds are preserved.
    assert windows[0][0] == start
    assert windows[-1][1] == end

    # Non-terminal windows are extended by the overlap constant; terminal keeps exact end.
    for i, (w_start, w_end) in enumerate(windows):
        is_terminal = i == len(windows) - 1
        max_width = width if is_terminal else width + _ICS_DEDUP_WINDOW_OVERLAP
        assert w_end - w_start <= max_width, f"window {i} too wide: {w_end - w_start}"

    # Window *starts* advance by exactly ``width`` — the overlap never shifts later starts.
    for i, (w_start, _) in enumerate(windows):
        assert w_start == start + i * width, f"window {i} start drifted"

    # A narrow span stays a single (terminal) window with no overlap extension.
    narrow = _ics_dedup_windows(start, start + timedelta(days=5))
    assert narrow == [(start, start + timedelta(days=5))]


def test_ics_dedup_windows_zero_duration_event_on_internal_boundary():
    """A zero-duration existing event exactly on an internal 90-day boundary is
    covered by the preceding window rather than dropped by both adjacent windows.

    The DB overlap filter is ``ends_at > window_start AND starts_at < window_end``
    (strict inequalities).  Without a 1 ms extension a zero-duration event at
    exactly the boundary T satisfies neither ``T < T`` (window ending at T) nor
    ``T > T`` (window starting at T), so a matching imported event would be created
    instead of skipped.
    """
    from butlers.api.routers.calendar_workspace import _ics_dedup_windows

    range_start = datetime(2026, 1, 1, tzinfo=UTC)
    range_end = range_start + timedelta(days=200)  # forces at least two windows
    width = timedelta(days=90)
    windows = _ics_dedup_windows(range_start, range_end, width=width)

    # The first internal boundary is range_start + 90 days.
    boundary = range_start + width
    # A zero-duration event: starts_at == ends_at == boundary.
    zero_dur_t = boundary

    # The event must be caught by at least one window via the strict-inequality filter.
    covered = any(zero_dur_t > w_start and zero_dur_t < w_end for w_start, w_end in windows)
    assert covered, (
        f"Zero-duration event at boundary {boundary} not covered by any dedup window. "
        f"Windows: {windows}"
    )
