"""Tests for the calendar conflict & overcommitment radar (bu-q8o90x).

Covers the two halves the bead's acceptance criteria require:

- the pure detector (``detect_conflict_issues``): overlap / back-to-back /
  overloaded-day detection, severity rules, and no false positives, plus the
  canonical overlap-pair id used to attach fix proposals;
- the ``GET /api/calendar/workspace/conflicts`` endpoint: overlaps detected over
  a window, empty on a clean window, the pending-proposal join, fail-open
  degraded mode, and window validation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.routers.calendar_workspace import _get_db_manager
from butlers.core.temporal.conflicts import (
    ConflictCandidate,
    detect_conflict_issues,
    overlap_pair_id,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure detector
# ---------------------------------------------------------------------------


def _c(
    *,
    start: datetime,
    minutes: int = 60,
    title: str = "Meeting",
    status: str = "confirmed",
    all_day: bool = False,
    entry_id: str | None = None,
) -> ConflictCandidate:
    return ConflictCandidate(
        entry_id=entry_id or str(uuid4()),
        title=title,
        start_at=start,
        end_at=start + timedelta(minutes=minutes),
        timezone="UTC",
        status=status,
        all_day=all_day,
    )


_DAY = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)


def test_overlap_detected():
    a = _c(start=_DAY, minutes=60, title="Design review", entry_id="a")
    b = _c(start=_DAY + timedelta(minutes=30), minutes=60, title="1:1", entry_id="b")
    issues = detect_conflict_issues([a, b])
    overlaps = [i for i in issues if i.kind == "overlap"]
    assert len(overlaps) == 1
    issue = overlaps[0]
    assert issue.severity == "warning"
    assert {e.entry_id for e in issue.events} == {"a", "b"}
    assert issue.date == "2026-07-01"
    assert "overlap" in issue.summary.lower()
    # pair_id is the canonical, order-independent id.
    assert issue.pair_id == overlap_pair_id("a", "b") == overlap_pair_id("b", "a")


def test_adjacent_events_do_not_overlap():
    # Half-open [start, end): b starting exactly when a ends is NOT an overlap.
    a = _c(start=_DAY, minutes=60, entry_id="a")
    b = _c(start=_DAY + timedelta(minutes=60), minutes=60, entry_id="b")
    issues = detect_conflict_issues([a, b], back_to_back_gap_minutes=0)
    assert [i for i in issues if i.kind == "overlap"] == []


def test_back_to_back_info_vs_warning():
    # Two adjacent (gap < 15) -> info.
    a = _c(start=_DAY, minutes=30, entry_id="a")
    b = _c(start=_DAY + timedelta(minutes=40), minutes=30, entry_id="b")  # 10-min gap
    issues = detect_conflict_issues([a, b])
    b2b = [i for i in issues if i.kind == "back_to_back"]
    assert len(b2b) == 1
    assert b2b[0].severity == "info"
    assert len(b2b[0].events) == 2

    # Three in an unbroken chain -> warning.
    c = _c(start=_DAY + timedelta(minutes=80), minutes=30, entry_id="c")  # 10-min gap
    issues3 = detect_conflict_issues([a, b, c])
    b2b3 = [i for i in issues3 if i.kind == "back_to_back"]
    assert len(b2b3) == 1
    assert b2b3[0].severity == "warning"
    assert len(b2b3[0].events) == 3


def test_overlapping_events_are_not_also_back_to_back():
    # Two overlapping meetings must surface ONLY as an overlap, never as a
    # redundant back-to-back card (the detectors stay orthogonal).
    a = _c(start=_DAY, minutes=60, entry_id="a")
    b = _c(start=_DAY + timedelta(minutes=30), minutes=60, entry_id="b")
    issues = detect_conflict_issues([a, b])
    assert [i.kind for i in issues if i.kind == "overlap"] == ["overlap"]
    assert [i for i in issues if i.kind == "back_to_back"] == []


def test_overlap_then_adjacent_event_still_back_to_back():
    # A overlaps B; C is genuinely adjacent to B (10-min gap) -> the chain forms
    # from the non-overlapping pair (B, C) while A/B remain the overlap.
    a = _c(start=_DAY, minutes=60, entry_id="a")  # 09:00-10:00
    b = _c(start=_DAY + timedelta(minutes=30), minutes=60, entry_id="b")  # 09:30-10:30
    c = _c(start=_DAY + timedelta(minutes=100), minutes=30, entry_id="c")  # 10:40-11:10
    issues = detect_conflict_issues([a, b, c])
    assert len([i for i in issues if i.kind == "overlap"]) >= 1
    b2b = [i for i in issues if i.kind == "back_to_back"]
    assert len(b2b) == 1
    assert {e.entry_id for e in b2b[0].events} == {"b", "c"}


def test_back_to_back_respects_gap_threshold():
    # 30-min gap with default 15-min threshold -> no back-to-back issue.
    a = _c(start=_DAY, minutes=30, entry_id="a")
    b = _c(start=_DAY + timedelta(minutes=60), minutes=30, entry_id="b")
    issues = detect_conflict_issues([a, b])
    assert [i for i in issues if i.kind == "back_to_back"] == []


def test_overloaded_day_detected():
    # 7 hours of meetings in one day exceeds the 6.0h default budget.
    events = [_c(start=_DAY + timedelta(hours=i), minutes=60, entry_id=f"e{i}") for i in range(7)]
    issues = detect_conflict_issues(events, overloaded_day_hours=6.0)
    overloaded = [i for i in issues if i.kind == "overloaded_day"]
    assert len(overloaded) == 1
    assert overloaded[0].severity == "warning"
    assert "7.0 h" in overloaded[0].summary


def test_overloaded_day_under_budget_clean():
    events = [
        _c(start=_DAY + timedelta(hours=2 * i), minutes=60, entry_id=f"e{i}") for i in range(4)
    ]
    issues = detect_conflict_issues(events, overloaded_day_hours=6.0)
    assert [i for i in issues if i.kind == "overloaded_day"] == []


def test_all_day_and_cancelled_events_excluded():
    a = _c(start=_DAY, minutes=60, all_day=True, entry_id="a")
    b = _c(start=_DAY + timedelta(minutes=30), minutes=60, status="cancelled", entry_id="b")
    c = _c(start=_DAY + timedelta(minutes=30), minutes=60, entry_id="c")
    # a is all-day (ignored), b is cancelled (ignored); only c remains -> no overlap.
    assert detect_conflict_issues([a, b, c]) == []


def test_clean_window_returns_no_issues():
    a = _c(start=_DAY, minutes=30, entry_id="a")
    b = _c(start=_DAY + timedelta(hours=3), minutes=30, entry_id="b")
    assert detect_conflict_issues([a, b]) == []


def test_display_tz_changes_grouping_date():
    # 23:30 UTC on Jul 1 is 19:30 on Jul 1 in New York but still Jul 1.
    midnight_utc = datetime(2026, 7, 2, 1, 0, tzinfo=UTC)  # 21:00 Jul 1 in NY
    a = _c(start=midnight_utc, minutes=60, entry_id="a")
    b = _c(start=midnight_utc + timedelta(minutes=30), minutes=60, entry_id="b")
    ny = ZoneInfo("America/New_York")
    issues = detect_conflict_issues([a, b], display_tz=ny)
    overlap = [i for i in issues if i.kind == "overlap"][0]
    assert overlap.date == "2026-07-01"


# ---------------------------------------------------------------------------
# GET /api/calendar/workspace/conflicts
# ---------------------------------------------------------------------------


def _ws_row(
    *, entry_id, title: str, start: datetime, minutes: int = 60, status="confirmed"
) -> dict:
    """A workspace event-instance row (shape ``row_to_workspace`` consumes)."""
    return {
        "instance_id": entry_id,
        "origin_instance_ref": str(uuid4()),
        "instance_timezone": "UTC",
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(minutes=minutes),
        "instance_status": status,
        "instance_metadata": {},
        "event_id": uuid4(),
        "origin_ref": "ref-" + str(entry_id),
        "title": title,
        "description": "",
        "location": "",
        "event_timezone": "UTC",
        "all_day": False,
        "event_status": status,
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": {"source_type": "provider_event"},
        "source_butler": None,
        "source_session_id": None,
        "source_id": uuid4(),
        "source_key": "provider:google:primary",
        "source_kind": "provider_event",
        "lane": "user",
        "provider": "google",
        "calendar_id": "primary",
        "butler_name": None,
        "display_name": "Primary",
        "writable": True,
        "source_metadata": {},
        "cursor_name": "provider_sync",
        "last_synced_at": start,
        "last_success_at": start,
        "last_error_at": None,
        "last_error": None,
        "full_sync_required": False,
    }


def _proposal_row(*, proposal_id, source_event_id: str, start: datetime, status="pending") -> dict:
    return {
        "proposal_id": proposal_id,
        "butler_name": "general",
        "title": "Decline tentative",
        "start_at": start,
        "end_at": start + timedelta(hours=1),
        "description": None,
        "location": None,
        "timezone": "UTC",
        "source_event_id": source_event_id,
        "source_snippet": None,
        "confidence": 0.8,
        "entity_ids": None,
        "status": status,
        "accepted_event_id": None,
        "created_at": start,
        "updated_at": start,
    }


def _build_app(app, *, workspace_rows=None, proposal_rows=None):
    workspace_rows = workspace_rows or {}
    proposal_rows = proposal_rows or {}
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=["general", "relationship"])

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            rows = workspace_rows
        elif "FROM calendar_event_proposals AS p" in query:
            rows = proposal_rows
        else:
            return {}
        if butler_names is not None:
            rows = {k: v for k, v in rows.items() if k in butler_names}
        return rows

    mock_db.fan_out = AsyncMock(side_effect=_fan_out)
    mock_mgr = AsyncMock(spec=MCPClientManager)
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    app.dependency_overrides[get_mcp_manager] = lambda: mock_mgr
    return app, mock_db


_PARAMS = {"start": "2026-07-01T00:00:00Z", "end": "2026-07-02T00:00:00Z"}


async def _get(app, params=_PARAMS):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/calendar/workspace/conflicts", params=params)


async def test_conflicts_endpoint_detects_overlap(app):
    a = _ws_row(entry_id="a", title="Design review", start=_DAY)
    b = _ws_row(entry_id="b", title="1:1", start=_DAY + timedelta(minutes=30))
    app, _ = _build_app(app, workspace_rows={"general": [a, b]})

    resp = await _get(app)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["issues_available"] is True
    overlaps = [i for i in data["issues"] if i["kind"] == "overlap"]
    assert len(overlaps) == 1
    assert {e["entry_id"] for e in overlaps[0]["events"]} == {"a", "b"}
    assert overlaps[0]["proposal_ids"] == []


async def test_conflicts_endpoint_empty_on_clean_window(app):
    a = _ws_row(entry_id="a", title="Standup", start=_DAY, minutes=30)
    b = _ws_row(entry_id="b", title="Lunch", start=_DAY + timedelta(hours=4), minutes=30)
    app, _ = _build_app(app, workspace_rows={"general": [a, b]})

    resp = await _get(app)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["issues_available"] is True
    assert data["issues"] == []


async def test_conflicts_endpoint_attaches_pending_proposal(app):
    a = _ws_row(entry_id="a", title="Design review", start=_DAY)
    b = _ws_row(entry_id="b", title="1:1", start=_DAY + timedelta(minutes=30))
    pair_id = str(overlap_pair_id("a", "b"))
    pid = uuid4()
    prop = _proposal_row(proposal_id=pid, source_event_id=pair_id, start=_DAY)
    app, _ = _build_app(app, workspace_rows={"general": [a, b]}, proposal_rows={"general": [prop]})

    resp = await _get(app)
    data = resp.json()["data"]
    overlap = [i for i in data["issues"] if i["kind"] == "overlap"][0]
    assert overlap["proposal_ids"] == [str(pid)]


async def test_conflicts_endpoint_fails_open_on_db_error(app, monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("fan-out down")

    monkeypatch.setattr("butlers.api.routers.calendar_workspace.query_calendar_conflicts", _boom)
    app, _ = _build_app(app)
    resp = await _get(app)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["issues_available"] is False
    assert data["issues"] == []


async def test_conflicts_endpoint_rejects_inverted_window(app):
    app, _ = _build_app(app)
    resp = await _get(app, params={"start": "2026-07-02T00:00:00Z", "end": "2026-07-01T00:00:00Z"})
    assert resp.status_code == 400


async def test_conflicts_endpoint_rejects_oversized_window(app):
    app, _ = _build_app(app)
    resp = await _get(app, params={"start": "2026-01-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"})
    assert resp.status_code == 400
