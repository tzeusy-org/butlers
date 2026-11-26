# Spec: REQ-dashboard-api-067
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

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_db, get_mcp_manager
from butlers.api.read_models.calendar_workspace import query_calendar_conflicts
from butlers.core.temporal.conflicts import (
    ConflictCandidate,
    detect_conflict_issues,
    overlap_pair_id,
)
from butlers.core.temporal.scheduling import SchedulingPreferences

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


def test_a_no_day_load_candidate_still_overlaps_but_adds_no_hours():
    """bu-s11n0s.4 AC D: only the overloaded-day sum reads the flag; the default counts."""
    assert ConflictCandidate("x", "t", _DAY, _DAY, "UTC").counts_toward_day_load is True
    meetings = [_c(start=_DAY + timedelta(hours=i), minutes=60, entry_id=f"e{i}") for i in range(5)]
    status = replace(_c(start=_DAY, minutes=8 * 60, entry_id="ooo"), counts_toward_day_load=False)
    issues = detect_conflict_issues([*meetings, status], overloaded_day_hours=6.0)
    assert not [i for i in issues if i.kind == "overloaded_day"]
    assert any(i.kind == "overlap" and "ooo" in {ref.entry_id for ref in i.events} for i in issues)
    counted = replace(status, counts_toward_day_load=True)
    issues = detect_conflict_issues([*meetings, counted], overloaded_day_hours=6.0)
    assert [i for i in issues if i.kind == "overloaded_day"]


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
    *,
    entry_id,
    title: str,
    start: datetime,
    minutes: int = 60,
    status="confirmed",
    all_day: bool = False,
    timezone: str = "UTC",
    event_metadata: object | None = None,
    time_role: str = "owner",
    subject_entity_id=None,
    subject_label: str | None = None,
) -> dict:
    """A workspace event-instance row (shape ``row_to_workspace`` consumes)."""
    return {
        "source_time_role": time_role,
        "source_subject_entity_id": subject_entity_id,
        "source_subject_label": subject_label,
        "instance_id": entry_id,
        "origin_instance_ref": str(uuid4()),
        "instance_timezone": timezone,
        "instance_starts_at": start,
        "instance_ends_at": start + timedelta(minutes=minutes),
        "instance_status": status,
        "instance_metadata": {},
        "event_id": uuid4(),
        "origin_ref": "ref-" + str(entry_id),
        "title": title,
        "description": "",
        "location": "",
        "event_timezone": timezone,
        "all_day": all_day,
        "event_status": status,
        "visibility": "default",
        "recurrence_rule": None,
        "event_metadata": (
            {"source_type": "provider_event"} if event_metadata is None else event_metadata
        ),
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


def _build_app(app, *, workspace_rows=None, proposal_rows=None, workspace_failed=None):
    workspace_rows = workspace_rows or {}
    proposal_rows = proposal_rows or {}
    workspace_failed = workspace_failed or []
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.butler_names = ["general", "relationship"]
    mock_db.butlers_with_module = MagicMock(return_value=["general", "relationship"])

    async def _fan_out(query: str, args=(), butler_names=None):
        if "FROM calendar_event_instances AS i" in query:
            rows = workspace_rows
        elif "FROM calendar_event_proposals AS p" in query:
            rows = proposal_rows
        elif "FROM calendar_sources AS s" in query:
            rows = {
                k: list({r["source_key"]: r for r in v}.values()) for k, v in workspace_rows.items()
            }
        else:
            return {}
        if butler_names is not None:
            rows = {k: v for k, v in rows.items() if k in butler_names}
        return rows

    async def _fan_out_with_status(query: str, args=(), butler_names=None, deadline_seconds=None):
        rows = await _fan_out(query, args, butler_names)
        # Only the events fan-out carries the simulated partial failure.
        failed = list(workspace_failed) if "FROM calendar_event_instances AS i" in query else []
        return rows, failed

    mock_db.fan_out_with_status = AsyncMock(side_effect=_fan_out_with_status)
    mock_mgr = AsyncMock(spec=MCPClientManager)
    app.dependency_overrides[get_db] = lambda: mock_db
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


async def test_conflicts_proposal_stays_attached_when_resync_rotates_surviving_copy(app):
    """bu-nd32y3: each calendar schema holds its own copy of a provider event and the
    origin_ref collapse keeps the freshest, so a resync can make another schema's copy
    survive. The overlap's pair_id (and the proposal stored under it) must not rotate."""
    a_general = _ws_row(entry_id="a-general", title="Design review", start=_DAY)
    a_relationship = _ws_row(entry_id="a-relationship", title="Design review", start=_DAY)
    for row in (a_general, a_relationship):
        row["origin_ref"] = "provider-a"
    b = _ws_row(entry_id="b", title="1:1", start=_DAY + timedelta(minutes=30))
    a_general["instance_updated_at"] = _DAY
    a_relationship["instance_updated_at"] = _DAY - timedelta(hours=1)
    proposals: list[dict] = []
    _, db = _build_app(
        app,
        workspace_rows={"general": [a_general, b], "relationship": [a_relationship]},
        proposal_rows={"general": proposals},
    )
    window = {"start": _DAY - timedelta(hours=9), "end": _DAY + timedelta(hours=15)}

    before = await query_calendar_conflicts(db, **window)
    (overlap,) = [i for i in before.issues if i.kind == "overlap"]
    assert {e.entry_id for e in overlap.events} == {"a-general", "b"}
    pid = uuid4()
    proposals.append(
        _proposal_row(proposal_id=pid, source_event_id=str(overlap.pair_id), start=_DAY)
    )

    a_relationship["instance_updated_at"] = _DAY + timedelta(hours=1)  # resync: other copy wins
    after = await query_calendar_conflicts(db, **window)
    (overlap,) = [i for i in after.issues if i.kind == "overlap"]
    assert {e.entry_id for e in overlap.events} == {"a-relationship", "b"}
    assert overlap.proposal_ids == [str(pid)]


async def test_conflicts_endpoint_attaches_proposal_stored_under_legacy_instance_pair_id(app):
    """Proposals written before bu-nd32y3 carry the instance-based pair id; they still join."""
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


async def test_conflicts_endpoint_partial_failure_flags_degraded(app):
    """Named bug (bu-yjfk2): a partial per-butler events fan-out failure must set
    issues_available=False even though the responding schema still produced a
    (possibly non-empty, but incomplete) issue set — otherwise the radar reports
    a fabricated all-clear when a failed schema's events could hide a conflict."""
    a = _ws_row(entry_id="a", title="Design review", start=_DAY)
    b = _ws_row(entry_id="b", title="1:1", start=_DAY + timedelta(minutes=30))
    app, _ = _build_app(
        app,
        workspace_rows={"general": [a, b]},
        workspace_failed=["relationship"],
    )

    resp = await _get(app)
    assert resp.status_code == 200
    data = resp.json()["data"]
    # The overlap from the schema that DID respond is still surfaced ...
    overlaps = [i for i in data["issues"] if i["kind"] == "overlap"]
    assert len(overlaps) == 1
    # ... but the scan is honestly flagged incomplete because a schema errored.
    assert data["issues_available"] is False


async def test_conflicts_endpoint_partial_failure_flags_degraded_even_when_clean(app):
    """A partial failure on an otherwise-clean window must NOT read as an honest
    empty all-clear: issues=[] with issues_available=False (the dropped schema
    could have held the only conflict)."""
    a = _ws_row(entry_id="a", title="Standup", start=_DAY, minutes=30)
    app, _ = _build_app(
        app,
        workspace_rows={"general": [a]},
        workspace_failed=["relationship"],
    )

    resp = await _get(app)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["issues"] == []
    assert data["issues_available"] is False


async def test_conflicts_endpoint_dedupes_origin_ref_cluster_no_overlap(app):
    """Regression (bu-hmdqz.10): a 3-member origin_ref duplicate cluster (the
    same provider event synced into 3 butler schemas / cross-calendar copies)
    must be collapsed to one row before conflict detection runs, yielding ZERO
    overlap issues — not the combinatorial 3-choose-2 = 3 phantom overlaps
    (plus a fabricated 'overloaded day' from the duplicate-hour sum) the
    un-deduped radar emitted for a live 8-entry-id/4-visible-entry incident."""
    shared_ref = "shared-origin-ref"
    rows = [
        _ws_row(entry_id=f"dup-{i}", title="Lou Shang — Last Day", start=_DAY) for i in range(3)
    ]
    for row in rows:
        row["origin_ref"] = shared_ref
    app, _ = _build_app(app, workspace_rows={"general": rows})

    resp = await _get(app)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["issues_available"] is True
    assert [i for i in data["issues"] if i["kind"] == "overlap"] == []
    assert [i for i in data["issues"] if i["kind"] == "overloaded_day"] == []


async def test_conflicts_endpoint_unmarked_butler_prefixed_row_preserves_overlap(app):
    provider_event = _ws_row(entry_id="provider", title="BUTLER: Lunch with Sam", start=_DAY)
    human_event = _ws_row(
        entry_id="human", title="Lunch with Sam", start=_DAY + timedelta(minutes=30)
    )
    app, _ = _build_app(app, workspace_rows={"general": [provider_event, human_event]})

    resp = await _get(app)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["issues_available"] is True
    overlaps = [issue for issue in data["issues"] if issue["kind"] == "overlap"]
    assert len(overlaps) == 1
    assert {event["entry_id"] for event in overlaps[0]["events"]} == {"provider", "human"}


async def test_conflicts_endpoint_unmarked_butler_prefixed_row_preserves_back_to_back(app):
    provider_event = _ws_row(entry_id="provider", title="BUTLER: Customer review", start=_DAY)
    human_event = _ws_row(
        entry_id="human", title="Customer review", start=_DAY + timedelta(minutes=65)
    )
    app, _ = _build_app(app, workspace_rows={"general": [provider_event, human_event]})

    resp = await _get(app)
    kinds = {issue["kind"] for issue in resp.json()["data"]["issues"]}

    assert "back_to_back" in kinds


async def test_conflicts_endpoint_unmarked_butler_prefixed_row_preserves_overloaded_day(app):
    provider_event = _ws_row(
        entry_id="provider", title="BUTLER: Client work", start=_DAY, minutes=4 * 60
    )
    human_event = _ws_row(
        entry_id="human",
        title="Client work",
        start=_DAY + timedelta(hours=4, minutes=30),
        minutes=3 * 60,
    )
    app, _ = _build_app(app, workspace_rows={"general": [provider_event, human_event]})

    resp = await _get(app)
    kinds = {issue["kind"] for issue in resp.json()["data"]["issues"]}

    assert "overloaded_day" in kinds


async def test_conflicts_endpoint_still_detects_overlap_between_two_distinct_butler_events(app):
    """Unmarked `BUTLER:` titles remain ordinary timed radar candidates."""
    a = _ws_row(entry_id="a", title="BUTLER: Prep for review", start=_DAY)
    b = _ws_row(entry_id="b", title="BUTLER: Draft follow-up", start=_DAY + timedelta(minutes=30))
    app, _ = _build_app(app, workspace_rows={"general": [a, b]})

    resp = await _get(app)
    data = resp.json()["data"]
    overlaps = [i for i in data["issues"] if i["kind"] == "overlap"]
    assert len(overlaps) == 1
    assert {e["entry_id"] for e in overlaps[0]["events"]} == {"a", "b"}


async def test_conflicts_endpoint_excludes_explicit_generated_metadata_from_overlap_and_overload(
    app,
):
    generated = _ws_row(
        entry_id="generated",
        title="Review draft",
        start=_DAY,
        minutes=7 * 60,
        event_metadata={"butler_generated": True},
    )
    human = _ws_row(
        entry_id="human",
        title="Human 1:1",
        start=_DAY + timedelta(minutes=30),
        minutes=60,
    )
    app, _ = _build_app(app, workspace_rows={"general": [generated, human]})

    resp = await _get(app)
    data = resp.json()["data"]

    assert data["issues_available"] is True
    assert data["issues"] == []


async def test_conflicts_endpoint_excludes_explicit_generated_metadata_from_back_to_back(app):
    generated = _ws_row(
        entry_id="generated",
        title="Review draft",
        start=_DAY,
        event_metadata={"butler_generated": True},
    )
    human = _ws_row(
        entry_id="human",
        title="Human 1:1",
        start=_DAY + timedelta(minutes=65),
    )
    app, _ = _build_app(app, workspace_rows={"general": [generated, human]})

    resp = await _get(app)
    assert resp.json()["data"]["issues"] == []


async def test_conflicts_endpoint_keeps_equivalent_timed_human_overlap_and_overload(app):
    first = _ws_row(
        entry_id="first",
        title="Review draft",
        start=_DAY,
        minutes=7 * 60,
        event_metadata={"butler_generated": False},
    )
    second = _ws_row(
        entry_id="second",
        title="Human 1:1",
        start=_DAY + timedelta(minutes=30),
        minutes=60,
    )
    app, _ = _build_app(app, workspace_rows={"general": [first, second]})

    resp = await _get(app)
    kinds = {issue["kind"] for issue in resp.json()["data"]["issues"]}

    assert "overlap" in kinds
    assert "overloaded_day" in kinds


async def test_conflicts_endpoint_keeps_equivalent_timed_human_back_to_back(app):
    first = _ws_row(
        entry_id="first",
        title="Review draft",
        start=_DAY,
        event_metadata={"butler_generated": False},
    )
    second = _ws_row(
        entry_id="second",
        title="Human 1:1",
        start=_DAY + timedelta(minutes=65),
    )
    app, _ = _build_app(app, workspace_rows={"general": [first, second]})

    resp = await _get(app)
    kinds = {issue["kind"] for issue in resp.json()["data"]["issues"]}

    assert "back_to_back" in kinds


async def test_conflicts_endpoint_excludes_legacy_midnight_row_from_every_detector(app):
    singapore = ZoneInfo("Asia/Singapore")
    legacy_start = datetime(2026, 7, 1, 0, 0, tzinfo=singapore)
    legacy = _ws_row(
        entry_id="legacy",
        title="Imported all-day block",
        start=legacy_start,
        minutes=24 * 60,
        timezone="Asia/Singapore",
        all_day=False,
    )
    human = _ws_row(
        entry_id="human",
        title="Human 1:1",
        start=_DAY + timedelta(minutes=30),
        minutes=60,
    )
    app, _ = _build_app(app, workspace_rows={"general": [legacy, human]})

    resp = await _get(app)
    assert resp.json()["data"]["issues"] == []


async def test_conflicts_endpoint_malformed_metadata_or_timezone_retains_timed_human_event(app):
    first = _ws_row(
        entry_id="first",
        title="Planning",
        start=_DAY,
        event_metadata="not-a-json-object",
        timezone="not/a-timezone",
    )
    second = _ws_row(
        entry_id="second",
        title="Review",
        start=_DAY + timedelta(minutes=30),
        timezone="not/a-timezone",
    )
    app, _ = _build_app(app, workspace_rows={"general": [first, second]})

    resp = await _get(app)
    data = resp.json()["data"]
    assert data["issues_available"] is True
    assert [issue["kind"] for issue in data["issues"] if issue["kind"] == "overlap"] == ["overlap"]


def _self_attendee(status: str, *, key: str = "response_status") -> dict:
    return {"email": "me@example.com", "self": True, key: status}


async def test_conflicts_endpoint_excludes_owner_declined_event(app):
    """bu-q7vx1q.3: an event the owner declined is not a conflict nor busy time."""
    confirmed = _ws_row(entry_id="a", title="Design review", start=_DAY, minutes=7 * 60)
    declined = _ws_row(
        entry_id="b",
        title="Declined sync",
        start=_DAY + timedelta(minutes=30),
        event_metadata={"attendees": [_self_attendee("declined")]},
    )
    app, _ = _build_app(app, workspace_rows={"general": [confirmed, declined]})

    data = (await _get(app)).json()["data"]

    assert data["issues_available"] is True
    assert [i for i in data["issues"] if i["kind"] in ("overlap", "back_to_back")] == []


async def test_conflicts_endpoint_excludes_declined_from_overloaded_day_hours(app):
    kept = _ws_row(entry_id="a", title="Workshop", start=_DAY, minutes=4 * 60)
    declined = _ws_row(
        entry_id="b",
        title="Declined offsite",
        start=_DAY + timedelta(hours=5),
        minutes=4 * 60,
        event_metadata={"attendees": [_self_attendee("declined", key="responseStatus")]},
    )
    app, _ = _build_app(app, workspace_rows={"general": [kept, declined]})

    data = (await _get(app)).json()["data"]

    assert [i for i in data["issues"] if i["kind"] == "overloaded_day"] == []


async def test_conflicts_endpoint_excludes_transparent_event(app):
    busy = _ws_row(entry_id="a", title="Design review", start=_DAY)
    free = _ws_row(
        entry_id="b",
        title="FYI block",
        start=_DAY + timedelta(minutes=30),
        event_metadata={"transparency": "transparent"},
    )
    app, _ = _build_app(app, workspace_rows={"general": [busy, free]})

    data = (await _get(app)).json()["data"]

    assert data["issues"] == []


async def test_conflicts_endpoint_keeps_accepted_tentative_and_other_attendee_declines(app):
    """Only the owner's own decline excludes; a declining guest does not."""
    a = _ws_row(
        entry_id="a",
        title="Accepted",
        start=_DAY,
        event_metadata={"attendees": [_self_attendee("accepted")]},
    )
    b = _ws_row(
        entry_id="b",
        title="Guest declined",
        start=_DAY + timedelta(minutes=30),
        event_metadata={
            "attendees": [
                {"email": "guest@example.com", "response_status": "declined"},
                _self_attendee("tentative"),
            ],
            "transparency": "opaque",
        },
    )
    app, _ = _build_app(app, workspace_rows={"general": [a, b]})

    data = (await _get(app)).json()["data"]

    assert len([i for i in data["issues"] if i["kind"] == "overlap"]) == 1


@pytest.mark.parametrize("event_type", ["outOfOffice", "workingLocation"])
async def test_an_opaque_status_event_still_conflicts_but_adds_no_day_load(app, event_type):
    """bu-s11n0s.4 AC D: a status adds zero meeting-hours; overlap/back-to-back are kept."""
    meeting = _ws_row(entry_id="a", title="Design review", start=_DAY, minutes=3 * 60)
    status = _ws_row(
        entry_id="b",
        title="Out of office" if event_type == "outOfOffice" else "Office",
        start=_DAY + timedelta(minutes=30),
        minutes=8 * 60,
    )
    status["event_type"] = event_type
    # Back to back with the end of the status block.
    after = _ws_row(entry_id="c", title="Wrap-up", start=_DAY + timedelta(minutes=30 + 8 * 60 + 5))
    app, _ = _build_app(app, workspace_rows={"general": [meeting, status, after]})

    data = (await _get(app)).json()["data"]

    kinds = [issue["kind"] for issue in data["issues"]]
    overlap = next(issue for issue in data["issues"] if issue["kind"] == "overlap")
    assert {ref["entry_id"] for ref in overlap["events"]} == {"a", "b"}
    chain = next(issue for issue in data["issues"] if issue["kind"] == "back_to_back")
    assert {ref["entry_id"] for ref in chain["events"]} >= {"b", "c"}
    # 3h + 1h of meetings: the status block's 8 hours never make the day overloaded.
    assert "overloaded_day" not in kinds


@pytest.mark.parametrize("event_type", ["default", "someFutureType"])
async def test_ordinary_and_unknown_types_still_count_toward_day_load(app, event_type):
    meeting = _ws_row(entry_id="a", title="Design review", start=_DAY, minutes=3 * 60)
    other = _ws_row(entry_id="b", title="Workshop", start=_DAY + timedelta(hours=4), minutes=4 * 60)
    other["event_type"] = event_type
    app, _ = _build_app(app, workspace_rows={"general": [meeting, other]})

    data = (await _get(app)).json()["data"]

    assert "overloaded_day" in [issue["kind"] for issue in data["issues"]]


async def test_a_transparent_working_location_is_neither_busy_nor_day_load(app):
    meeting = _ws_row(entry_id="a", title="Design review", start=_DAY, minutes=3 * 60)
    status = _ws_row(
        entry_id="b",
        title="Office",
        start=_DAY + timedelta(minutes=30),
        minutes=8 * 60,
        event_metadata={"source_type": "provider_event", "transparency": "transparent"},
    )
    status["event_type"] = "workingLocation"
    app, _ = _build_app(app, workspace_rows={"general": [meeting, status]})

    data = (await _get(app)).json()["data"]

    assert data["issues_available"] is True
    assert data["issues"] == []


async def test_conflicts_endpoint_keeps_typed_focus_time_as_busy_time(app):
    meeting = _ws_row(entry_id="a", title="Design review", start=_DAY)
    focus = _ws_row(entry_id="b", title="Writing", start=_DAY + timedelta(minutes=30))
    focus["event_type"] = "focusTime"
    app, _ = _build_app(app, workspace_rows={"general": [meeting, focus]})

    data = (await _get(app)).json()["data"]

    assert len([i for i in data["issues"] if i["kind"] == "overlap"]) == 1


async def test_conflicts_endpoint_rejects_inverted_window(app):
    app, _ = _build_app(app)
    resp = await _get(app, params={"start": "2026-07-02T00:00:00Z", "end": "2026-07-01T00:00:00Z"})
    assert resp.status_code == 400


async def test_conflicts_endpoint_rejects_oversized_window(app):
    app, _ = _build_app(app)
    resp = await _get(app, params={"start": "2026-01-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"})
    assert resp.status_code == 400


def _invitation_row(status="needsAction", *, explicit=True, verdict="known_entity", **overrides):
    row = _ws_row(entry_id=uuid4(), title="Invitation", start=_DAY)
    row["title"] = f"Invitation {row['instance_id']}"
    row["event_metadata"] = {
        "source_type": "provider_event",
        "organizer": "organizer@example.test",
        **({"organizer_verdict": verdict} if verdict else {}),
        "attendees": [
            {
                "email": "owner@example.test",
                "self": True,
                "response_status": status,
            }
        ],
        "attendee_status_provenance": [
            {
                "email": "owner@example.test",
                "response_status": status,
                "response_status_explicit": explicit,
            }
        ],
    }
    row.update(overrides)
    return row


async def _get_invitations(app, **params):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/calendar/workspace/invitations", params={**_PARAMS, **params})


async def test_invitations_admit_explicit_provider_truth_and_earned_conflict(app):
    """The actual query/GET excludes default, solo, cancelled and unrelated copies."""
    answered = [_invitation_row(s) for s in ("accepted", "declined", "tentative")]
    invited = _invitation_row()
    unknown = [_invitation_row(None), _invitation_row("invalid"), _invitation_row(explicit=False)]
    no_self = _invitation_row(event_metadata={"source_type": "provider_event", "attendees": []})
    self_organizer = _invitation_row()
    self_organizer["event_metadata"]["attendees"][0]["organizer"] = True
    solo = _invitation_row()
    solo["event_metadata"].pop("organizer")
    cancelled = _invitation_row(instance_status="cancelled")
    duplicate = {**invited, "instance_id": uuid4()}
    partner = _ws_row(entry_id=uuid4(), title="Other meeting", start=_DAY + timedelta(minutes=10))
    app, db = _build_app(
        app,
        workspace_rows={
            "general": [
                invited,
                duplicate,
                partner,
                *answered,
                *unknown,
                no_self,
                self_organizer,
                solo,
                cancelled,
            ]
        },
    )
    response = await _get_invitations(app)
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data["entries"]) == 1
    entry = data["entries"][0]
    assert entry["entry_id"] == str(min(invited["instance_id"], duplicate["instance_id"]))
    assert entry["organizer"] == "organizer@example.test"
    assert entry["organizer_source"] == "event"
    assert entry["butler_name"] == "general"
    assert entry["conflict_issues"]
    assert all(
        any(ref["entry_id"] == entry["entry_id"] for ref in issue["events"])
        for issue in entry["conflict_issues"]
    )
    assert data["issues_available"] is False
    assert "calendar invitation status" in data["sources_degraded"]
    # Both normalized and raw status spellings need actual explicit provenance.
    raw = _invitation_row()
    a = raw["event_metadata"]["attendees"][0]
    a["responseStatus"] = a.pop("response_status")
    app, _ = _build_app(app, workspace_rows={"general": [raw]})
    clean = (await _get_invitations(app)).json()["data"]
    assert clean["issues_available"] is True
    assert clean["entries"][0]["conflict_issues"] == []
    assert clean["conflicts_available"] is True
    malformed_self = _invitation_row()
    malformed_self["event_metadata"]["attendees"][0].pop("email")
    app, _ = _build_app(app, workspace_rows={"general": [malformed_self]})
    malformed = (await _get_invitations(app)).json()["data"]
    assert malformed["entries"] == [] and malformed["issues_available"] is False
    generated = _invitation_row()
    generated["event_metadata"]["butler_generated"] = " TRUE "
    app, _ = _build_app(app, workspace_rows={"general": [generated]})
    suppressed = (await _get_invitations(app)).json()["data"]
    assert suppressed["entries"] == [] and suppressed["issues_available"] is True
    fallback = _invitation_row()
    fallback["event_metadata"].pop("organizer")
    fallback["event_metadata"]["attendees"].append(
        {"email": "host@example.test", "organizer": True, "display_name": "Host"}
    )
    fallback["event_metadata"]["attendee_status_provenance"].append({})
    app, _ = _build_app(app, workspace_rows={"general": [fallback]})
    observed = (await _get_invitations(app)).json()["data"]["entries"][0]
    assert observed["organizer"] == "Host" and observed["organizer_source"] == "attendee"
    fallback["event_metadata"]["attendees"][1].pop("organizer")
    missing = (await _get_invitations(app)).json()["data"]["entries"][0]
    assert missing["organizer"] is None and missing["organizer_source"] == "unknown"
    app, _ = _build_app(app, workspace_rows={"general": [raw]})
    raw["event_metadata"].pop("attendee_status_provenance")
    legacy = (await _get_invitations(app)).json()["data"]
    assert legacy["entries"] == [] and legacy["issues_available"] is False
    # A fresh accepted copy wins over a stale unanswered copy before admission.
    fresh = {
        **invited,
        "instance_id": uuid4(),
        "instance_updated_at": _DAY + timedelta(days=1),
        "event_metadata": _invitation_row("accepted")["event_metadata"],
    }
    app, _ = _build_app(app, workspace_rows={"general": [invited, fresh]})
    settled = (await _get_invitations(app)).json()["data"]
    assert settled["entries"] == [] and settled["issues_available"] is True
    assert any("source_type" in call.args[0] for call in db.fan_out_with_status.await_args_list)

    # Model the emitted predicates, rather than letting canned SQL rows hide an
    # early cancellation/window filter. The real migrated companion owns SQL.
    from copy import deepcopy
    from types import SimpleNamespace

    from butlers.api.read_models.calendar_workspace import query_calendar_invitations
    from butlers.modules.calendar import CalendarModule

    stale = _invitation_row()
    stale["instance_updated_at"] = _DAY
    newest = deepcopy(stale)
    newest.update(
        instance_id=uuid4(),
        event_id=uuid4(),
        source_id=uuid4(),
        source_key="provider:google:copy",
        instance_updated_at=_DAY + timedelta(hours=1),
    )
    records = [stale, newest]
    emitted = []

    async def predicate_rows(query, args=(), butler_names=None):
        emitted.append(query)
        selected = deepcopy(records)
        if "i.starts_at < $1 AND i.ends_at > $2" in query:
            selected = [
                r
                for r in selected
                if r["instance_starts_at"] < args[0] and r["instance_ends_at"] > args[1]
            ]
        elif "i.starts_at < $2" in query:
            selected = [
                r
                for r in selected
                if r["instance_starts_at"] < args[1] and r["instance_ends_at"] > args[2]
            ]
        if "COALESCE(i.status, e.status) != 'cancelled'" in query:
            selected = [
                r for r in selected if (r["instance_status"] or r["event_status"]) != "cancelled"
            ]
        if "e.origin_ref = ANY($1::text[])" in query:
            occurrences = set(zip(args[1], args[2], strict=True))
            selected = [
                r
                for r in selected
                if (not r["recurrence_rule"] and r["origin_ref"] in args[0])
                or (
                    r["recurrence_rule"]
                    and (r["origin_ref"], r["instance_starts_at"]) in occurrences
                )
            ]
        return {"general": selected}, []

    db.fan_out_with_status.side_effect = predicate_rows
    read_args = {"start": _DAY, "end": _DAY + timedelta(days=1)}
    before_cancel = await query_calendar_invitations(db, **read_args)
    assert len(before_cancel.rows) == 1 and before_cancel.status_available is True

    class TombstonePool:
        async def fetchrow(self, query, *args):
            if "to_regclass('calendar_sources')" in query:
                return dict.fromkeys(
                    (
                        "has_sources",
                        "has_events",
                        "has_instances",
                        "has_cursors",
                        "has_action_log",
                        "has_events_body",
                        "has_events_source_butler",
                        "has_events_source_session_id",
                        "has_events_typed_fields",
                    ),
                    True,
                )
            assert "UPDATE calendar_events" in query and "status = 'cancelled'" in query
            assert args[:2] == (newest["source_id"], newest["origin_ref"])
            newest["event_status"] = "cancelled"
            return {"id": newest["event_id"]}

        async def execute(self, query, *args):
            assert "UPDATE calendar_event_instances" in query and "status = 'cancelled'" in query
            assert args[0] == newest["event_id"]
            newest["instance_status"] = "cancelled"
            newest["instance_updated_at"] = _DAY + timedelta(hours=2)
            return "UPDATE 1"

    module = CalendarModule()
    module._db = SimpleNamespace(pool=TombstonePool())
    await module._project_provider_changes(
        source_id=newest["source_id"],
        provider_name="google",
        calendar_id="copy",
        updated_events=[],
        cancelled_ids=[newest["origin_ref"]],
    )
    tombstone = await query_calendar_invitations(db, **read_args)
    assert tombstone.rows == [] and tombstone.status_available is True
    newest.update(instance_status="confirmed", event_status="confirmed")
    restored = await query_calendar_invitations(db, **read_args)
    assert len(restored.rows) == 1 and restored.status_available is True
    newest["event_metadata"]["attendees"][0]["response_status"] = "accepted"
    newest["instance_starts_at"] += timedelta(days=2)
    newest["instance_ends_at"] += timedelta(days=2)
    moved = await query_calendar_invitations(db, **read_args)
    assert moved.rows == [] and moved.status_available is True
    newest["instance_starts_at"] -= timedelta(days=2)
    newest["instance_ends_at"] -= timedelta(days=2)
    # An eligible moved copy is also outside the inbox, not resurrected at the old time.
    newest["event_metadata"]["attendees"][0]["response_status"] = "needsAction"
    newest["instance_starts_at"] += timedelta(days=2)
    newest["instance_ends_at"] += timedelta(days=2)
    moved_unanswered = await query_calendar_invitations(db, **read_args)
    assert moved_unanswered.rows == [] and moved_unanswered.status_available is True
    assert not any("LIMIT" in q.split("WHERE s.lane", 1)[-1] for q in emitted)
    # Companion provenance cannot be transplanted across identities or statuses.
    records[:] = [deepcopy(stale)]
    for malformed in (
        True,
        [],
        [
            {
                "email": "other@example.test",
                "response_status": "needsAction",
                "response_status_explicit": True,
            }
        ],
        [
            {
                "email": "owner@example.test",
                "response_status": "accepted",
                "response_status_explicit": True,
            }
        ],
        [
            {
                "email": "owner@example.test",
                "response_status": "needsAction",
                "response_status_explicit": 1,
            }
        ],
    ):
        records[0]["event_metadata"]["attendee_status_provenance"] = malformed
        refused = await query_calendar_invitations(db, **read_args)
        assert refused.rows == [] and refused.status_available is False
    records[:] = [stale]
    final_restored = await query_calendar_invitations(db, **read_args)
    assert len(final_restored.rows) == 1 and final_restored.status_available is True


async def test_invitations_pagination_validation_and_honest_source_failures(app, monkeypatch):
    from butlers.api.read_models.calendar_workspace import CalendarConflictScan

    rows = [_invitation_row(title=f"Invite {i}") for i in range(3)]
    app, db = _build_app(app, workspace_rows={"general": rows})
    first = (await _get_invitations(app, limit=2)).json()["data"]
    assert len(first["entries"]) == 2 and first["has_more"] is True
    second = (await _get_invitations(app, limit=2, cursor=first["next_cursor"])).json()["data"]
    assert len(second["entries"]) == 1 and second["has_more"] is False
    assert second["next_cursor"] is None
    original_fanout = db.fan_out_with_status.side_effect

    async def related_lookup_failure(query, args=(), butler_names=None):
        if "e.origin_ref = ANY($1::text[])" in query:
            raise RuntimeError("synthetic related-copy query failure")
        return await original_fanout(query, args, butler_names)

    db.fan_out_with_status.side_effect = related_lookup_failure
    related_unknown = (await _get_invitations(app)).json()["data"]
    assert len(related_unknown["entries"]) == 3 and related_unknown["issues_available"] is False
    assert "calendar events" in related_unknown["sources_degraded"]
    db.fan_out_with_status.side_effect = original_fanout
    related_restored = (await _get_invitations(app)).json()["data"]
    assert len(related_restored["entries"]) == 3 and related_restored["issues_available"] is True
    assert not (
        {r["entry_id"] for r in first["entries"]} & {r["entry_id"] for r in second["entries"]}
    )
    for params in (
        {"cursor": "invalid"},
        {"cursor": first["next_cursor"], "timezone": "UTC"},
        {"limit": 201},
        {"end": "2026-12-01T00:00:00Z"},
        {"end": _PARAMS["start"]},
        {"start": "2026-07-01T00:00:00"},
        {"timezone": "invalid"},
    ):
        assert (await _get_invitations(app, **params)).status_code in (400, 422)
    app, _ = _build_app(app, workspace_rows={"general": rows}, workspace_failed=["relationship"])
    partial = (await _get_invitations(app)).json()["data"]
    assert len(partial["entries"]) == 3 and partial["issues_available"] is False
    assert "relationship" in partial["sources_degraded"]
    app, _ = _build_app(app, workspace_failed=["general", "relationship"])
    total = (await _get_invitations(app)).json()["data"]
    assert total["entries"] == [] and total["issues_available"] is False
    app, _ = _build_app(app)
    empty = (await _get_invitations(app)).json()["data"]
    assert empty["entries"] == [] and empty["issues_available"] is True
    app, _ = _build_app(app, workspace_rows={"general": rows})
    from butlers.api.routers import calendar_workspace

    source_reader = calendar_workspace._fetch_sources
    with monkeypatch.context() as patch:
        patch.setattr(calendar_workspace, "_fetch_sources", AsyncMock(side_effect=RuntimeError))
        missing_owner = (await _get_invitations(app)).json()["data"]
        assert len(missing_owner["entries"]) == 3 and missing_owner["issues_available"] is False
        assert all(entry["butler_name"] is None for entry in missing_owner["entries"])
        assert "calendar sources" in missing_owner["sources_degraded"]
    assert calendar_workspace._fetch_sources is source_reader
    restored = (await _get_invitations(app)).json()["data"]
    assert restored["issues_available"] is True
    assert all(entry["butler_name"] == "general" for entry in restored["entries"])
    monkeypatch.setattr(
        "butlers.api.routers.calendar_workspace.query_calendar_conflicts",
        AsyncMock(return_value=CalendarConflictScan([], False)),
    )
    unavailable = (await _get_invitations(app)).json()["data"]
    assert len(unavailable["entries"]) == 3 and unavailable["issues_available"] is False
    assert unavailable["conflicts_available"] is False
    assert "calendar conflict scan" in unavailable["sources_degraded"]


async def test_invitation_response_capability_belongs_to_physical_entry_owner(app):
    # REQ-dashboard-api-069
    # A newer event copy and the canonical source can belong to different schemas.
    finance = _invitation_row(origin_ref="shared", instance_updated_at=_DAY)
    messenger = _invitation_row(
        origin_ref="shared", instance_updated_at=_DAY - timedelta(minutes=5)
    )
    app, physical_db = _build_app(
        app, workspace_rows={"finance": [finance], "messenger": [messenger]}
    )
    physical_db.butlers_with_module.return_value = ["finance", "messenger"]
    mismatched = (await _get_invitations(app)).json()["data"]["entries"]
    assert len(mismatched) == 1
    assert mismatched[0]["entry_id"] == str(finance["instance_id"])
    assert mismatched[0]["butler_name"] == "finance"
    assert mismatched[0]["response_configured"] is False
    messenger["instance_updated_at"] = _DAY + timedelta(minutes=5)
    matching = (await _get_invitations(app)).json()["data"]["entries"][0]
    assert matching["entry_id"] == str(messenger["instance_id"])
    assert matching["butler_name"] == "messenger" and matching["response_configured"] is True
    messenger["writable"] = False
    assert (await _get_invitations(app)).json()["data"]["entries"][0][
        "response_configured"
    ] is False


# -- source roles: whose time is this (bu-5ii4gj.1, REQ-calendar-conflict-overcommitment-radar-002) --

_SELF = {"self": True, "response_status": "accepted"}


async def test_conflicts_endpoint_ignores_another_persons_calendar(app):
    mine = _ws_row(entry_id="mine", title="Owner 1:1", start=_DAY)
    partner = _ws_row(
        entry_id="partner",
        title="Partner appointment",
        start=_DAY + timedelta(minutes=30),
        time_role="household_member",
    )
    unknown = _ws_row(
        entry_id="unknown",
        title="Shared calendar block",
        start=_DAY + timedelta(minutes=15),
        minutes=8 * 60,
        time_role="unclassified",
    )
    app, _ = _build_app(app, workspace_rows={"general": [mine, partner, unknown]})

    data = (await _get(app)).json()["data"]

    assert data["issues_available"] is True
    assert data["issues"] == []


async def test_conflicts_endpoint_counts_an_invited_event_on_another_calendar(app):
    mine = _ws_row(entry_id="mine", title="Owner 1:1", start=_DAY)
    invited = _ws_row(
        entry_id="invited",
        title="Joint review",
        start=_DAY + timedelta(minutes=30),
        time_role="other_person",
        event_metadata={"source_type": "provider_event", "attendees": [_SELF]},
    )
    app, _ = _build_app(app, workspace_rows={"general": [mine, invited]})

    overlaps = [i for i in (await _get(app)).json()["data"]["issues"] if i["kind"] == "overlap"]

    assert len(overlaps) == 1
    assert {e["entry_id"] for e in overlaps[0]["events"]} == {"mine", "invited"}


async def test_conflicts_endpoint_never_counts_holidays_or_birthdays(app):
    mine = _ws_row(entry_id="mine", title="Owner 1:1", start=_DAY)
    holiday = _ws_row(
        entry_id="holiday",
        title="Public holiday",
        start=_DAY,
        time_role="holidays",
        event_metadata={"source_type": "provider_event", "attendees": [_SELF]},
    )
    app, _ = _build_app(app, workspace_rows={"general": [mine, holiday]})

    assert (await _get(app)).json()["data"]["issues"] == []


async def test_conflicts_endpoint_reports_how_many_unclassified_calendars_were_skipped(app):
    """The coverage line: an unclassified source's solo events are left out, and said so."""
    shared_a = _ws_row(entry_id="a", title="Block A", start=_DAY, time_role="unclassified")
    shared_b = _ws_row(
        entry_id="b", title="Block B", start=_DAY + timedelta(hours=3), time_role="unclassified"
    )
    mine = _ws_row(entry_id="mine", title="Owner 1:1", start=_DAY + timedelta(hours=5))
    holiday = _ws_row(
        entry_id="holiday", title="Holiday", start=_DAY + timedelta(hours=7), time_role="holidays"
    )
    app, _ = _build_app(app, workspace_rows={"general": [shared_a, shared_b, mine, holiday]})

    data = (await _get(app)).json()["data"]

    assert data["unclassified_events"] == 2
    # Every synthetic row gets its own source id, so two events are two calendars here.
    assert data["unclassified_sources"] == 2
    assert data["issues"] == []


# -- household context beside the scan (bu-5ii4gj.1 S3, REQ-calendar-conflict-overcommitment-radar-003)


async def test_conflicts_endpoint_lists_household_events_as_opaque_context_not_issues(app):
    subject = uuid4()
    mine = _ws_row(entry_id="mine", title="Owner 1:1", start=_DAY)
    partner = _ws_row(
        entry_id="partner",
        title="Synthetic private appointment",
        start=_DAY + timedelta(minutes=30),
        time_role="household_member",
        subject_entity_id=subject,
        subject_label="Alex",
        event_metadata={
            "source_type": "provider_event",
            "attendees": [{"email": "guest@example.test"}],
        },
    )
    partner["location"] = "12 Synthetic Road"
    unknown = _ws_row(
        entry_id="unknown",
        title="Unclassified block",
        start=_DAY + timedelta(minutes=15),
        time_role="unclassified",
    )
    app, _ = _build_app(app, workspace_rows={"general": [mine, partner, unknown]})

    response = await _get(app)
    data = response.json()["data"]

    assert data["issues"] == []  # context never forms a conflict
    [context] = data["household_context"]
    assert context["subject_entity_id"] == str(subject) and context["subject_label"] == "Alex"
    assert set(context) == {
        "role",
        "subject_entity_id",
        "subject_label",
        "start_at",
        "end_at",
        "all_day",
    }
    for leaked in ("Synthetic private appointment", "Synthetic Road", "guest@example.test"):
        assert leaked not in response.text
    assert data["unclassified_events"] == 1  # the coverage line is unchanged


async def test_household_time_endpoint_groups_by_subject_and_leaks_nothing(app):
    alex, sam = uuid4(), uuid4()
    rows = [
        _ws_row(
            entry_id="a1",
            title="Synthetic A1",
            start=_DAY,
            time_role="household_member",
            subject_entity_id=alex,
            subject_label="Alex",
        ),
        _ws_row(
            entry_id="a2",
            title="Synthetic A2",
            start=_DAY + timedelta(hours=3),
            time_role="household_member",
            subject_entity_id=alex,
            subject_label="Alex",
        ),
        _ws_row(
            entry_id="s1",
            title="Synthetic S1",
            start=_DAY,
            time_role="team",
            subject_entity_id=sam,
            subject_label="Sam",
        ),
        _ws_row(entry_id="mine", title="Owner", start=_DAY),
        _ws_row(entry_id="u", title="Unclassified", start=_DAY, time_role="unclassified"),
    ]
    app, _ = _build_app(app, workspace_rows={"general": rows})
    start = _DAY - timedelta(hours=1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/calendar/workspace/household-time",
            params={"start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()},
        )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is True
    by_subject = {g["subject_label"]: len(g["events"]) for g in data["subjects"]}
    assert by_subject == {"Alex": 2, "Sam": 1}
    for leaked in ("Synthetic A1", "Synthetic S1", "Owner", "Unclassified"):
        assert leaked not in response.text


async def test_untrusted_invitations_are_quarantined_with_time_and_verdict_only(app):
    """REQ-module-calendar-041: no title, organizer or text reaches the quarantine section."""
    trusted = _invitation_row()
    stranger = _invitation_row(verdict="first_contact")
    blocked = _invitation_row(verdict="blocked")
    unstamped = _invitation_row(verdict=None)
    accepted_blocked = _invitation_row("accepted", verdict="blocked")
    app, _db = _build_app(
        app, workspace_rows={"general": [trusted, stranger, blocked, unstamped, accepted_blocked]}
    )
    response = await _get_invitations(app)
    assert response.status_code == 200
    data = response.json()["data"]
    assert [e["entry_id"] for e in data["entries"]] == [str(trusted["instance_id"])]
    quarantined = {q["entry_id"]: q for q in data["quarantined"]}
    assert set(quarantined) == {
        str(stranger["instance_id"]),
        str(blocked["instance_id"]),
        str(unstamped["instance_id"]),
    }
    assert quarantined[str(stranger["instance_id"])]["verdict"] == "first_contact"
    assert quarantined[str(blocked["instance_id"])]["verdict"] == "blocked"
    assert quarantined[str(unstamped["instance_id"])]["verdict"] == "unknown"
    forbidden = {"title", "organizer", "description", "location", "attendees", "summary"}
    for item in quarantined.values():
        assert not forbidden & set(item)
    body = response.text
    for row in (stranger, blocked, unstamped):
        assert row["title"] not in body
    assert "organizer@example.test" not in json.dumps(data["quarantined"])
    # An unstamped row means the verdict could not be read: the envelope says so.
    assert data["issues_available"] is False
    assert "calendar organizer verdict" in data["sources_degraded"]


async def test_a_quarantined_invitation_adds_no_conflict_or_load(app):
    held = _ws_row(entry_id=uuid4(), title="Accepted standup", start=_DAY)
    held["event_metadata"] = {
        "source_type": "provider_event",
        "organizer_verdict": "known_entity",
        "attendees": [{"email": "owner@example.test", "self": True, "response_status": "accepted"}],
    }
    spam = _invitation_row(verdict="blocked")
    spam["instance_starts_at"] = _DAY
    spam["instance_ends_at"] = _DAY + timedelta(hours=1)
    app, _db = _build_app(app, workspace_rows={"general": [held, spam]})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/calendar/workspace/conflicts", params=_PARAMS)
    assert response.status_code == 200
    assert response.json()["data"]["issues"] == []


# ---------------------------------------------------------------------------
# Resolution options on overlap issues (bu-5ii4gj.11 R1b)
# ---------------------------------------------------------------------------

_INVITED = {
    "source_type": "provider_event",
    "attendees": [
        {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
        {"email": "boss@example.com", "organizer": True, "responseStatus": "accepted"},
    ],
}


class _Pool:
    """A butler pool stand-in: the dedup stores are empty and the owner's zone is saved."""

    def __init__(self, zone: str | None = "UTC"):
        self.zone = zone

    async def fetchrow(self, sql, *args):
        return None

    async def fetch(self, sql, *args):
        return []

    async def fetchval(self, sql, *args):
        return {"timezone": self.zone} if self.zone else None


def _options_world(app, **overrides):
    block = _ws_row(entry_id="block", title="Focus", start=_DAY, time_role="butler")
    block["event_etag"] = "etag-block"
    meeting = _ws_row(
        entry_id="mtg",
        title="Planning",
        start=_DAY + timedelta(minutes=30),
        event_metadata=_INVITED,
    )
    meeting["event_etag"] = "etag-mtg"
    for row in (block, meeting):
        row.update(overrides)
    return block, meeting


_WINDOW = {"start": _DAY - timedelta(days=30), "end": _DAY + timedelta(hours=15)}
_NOW = _DAY - timedelta(hours=1)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr("butlers.core.temporal.conflict_resolution.RESOLUTION_OPTIONS_LIVE", True)


async def _scan(app, rows, *, zone="UTC", failed=None, window=None, **extra):
    _, db = _build_app(app, workspace_rows={"general": rows}, workspace_failed=failed)
    db.pool = MagicMock(return_value=_Pool(zone))
    return await query_calendar_conflicts(db, **(window or _WINDOW), now=_NOW, **extra)


async def _overlap(app, rows, **kwargs):
    scan = await _scan(app, rows, **kwargs)
    (overlap,) = [i for i in scan.issues if i.kind == "overlap"]
    return overlap


def _move_start(overlap):
    move = next(o for o in overlap.options if o.kind.startswith("move"))
    return datetime.fromisoformat(move.exact_call["args"]["start_at"])


async def test_options_are_absent_while_the_flag_is_dark(app):
    overlap = await _overlap(app, list(_options_world(app)))
    assert overlap.options == [] and overlap.issue_digest is None


async def test_a_block_against_a_meeting_offers_a_move_into_a_free_window(app, live):
    overlap = await _overlap(app, list(_options_world(app)))

    kinds = [o.kind for o in overlap.options]
    assert kinds[0] == "move_block" and kinds[-1] == "keep_both"
    assert {"decline", "tentative"} <= set(kinds)
    assert overlap.options[0].authority == "no_park_undo"
    # Clear of both overlapping events (09:00-10:30) and the 15-minute gap after them.
    assert _move_start(overlap) == _DAY + timedelta(minutes=105)
    assert overlap.issue_digest


async def test_a_slot_must_be_free_on_every_calendar_not_just_the_two_in_the_overlap(app, live):
    rows = list(_options_world(app))
    rows.append(_ws_row(entry_id="late", title="Review", start=_DAY + timedelta(minutes=105)))

    overlap = await _overlap(app, rows)

    assert _move_start(overlap) == _DAY + timedelta(minutes=105 + 60 + 15)


async def test_an_unclassified_calendars_attended_event_blocks_the_slot(app, live):
    rows = list(_options_world(app))
    rows.append(
        _ws_row(
            entry_id="standup",
            title="Standup",
            start=_DAY + timedelta(minutes=105),
            time_role="unclassified",
            # No accepted seat to assert owner time: only "the owner is not known to decline" holds.
            event_metadata={"source_type": "provider_event"},
        )
    )

    overlap = await _overlap(app, rows)

    assert _move_start(overlap) >= _DAY + timedelta(minutes=105 + 60 + 15)


async def test_an_all_day_leave_blocks_every_slot_that_day(app, live):
    rows = list(_options_world(app))
    midnight = _DAY.replace(hour=0)
    leave = _ws_row(entry_id="leave", title="Leave", start=midnight, minutes=24 * 60, all_day=True)
    leave["last_success_at"] = leave["last_synced_at"] = _NOW  # the source itself is fresh
    rows.append(leave)

    overlap = await _overlap(app, rows)

    assert not [o for o in overlap.options if o.kind.startswith("move")]
    assert overlap.options_reason == "no_free_slot"
    assert overlap.options[-1].kind == "keep_both"


async def test_a_partial_fan_out_attaches_no_options(app, live):
    scan = await _scan(app, list(_options_world(app)), failed=["relationship"])

    (overlap,) = [i for i in scan.issues if i.kind == "overlap"]
    assert overlap.options == [] and overlap.issue_digest is None


async def test_a_butler_filtered_read_attaches_no_options(app, live):
    scan = await _scan(app, list(_options_world(app)), butlers=["general"])

    (overlap,) = [i for i in scan.issues if i.kind == "overlap"]
    assert overlap.options == [] and overlap.issue_digest is None


async def test_a_stale_source_offers_nothing_and_says_so(app, live):
    block, meeting = _options_world(app, last_synced_at=_NOW)  # attempts are recent...
    block["last_success_at"] = meeting["last_success_at"] = _NOW - timedelta(days=1)  # ...not wins

    overlap = await _overlap(app, [block, meeting])

    assert overlap.options == [] and overlap.options_reason == "stale_source"
    assert overlap.issue_digest is None  # keep_both included: nothing is acknowledged while stale


async def test_a_source_that_failed_after_its_last_success_is_stale_now(app, live):
    block, meeting = _options_world(app)
    for row in (block, meeting):
        row["last_error_at"] = _NOW  # a win a moment ago, then a failure
        row["last_success_at"] = _NOW - timedelta(minutes=5)
        row["last_error"] = "auth"

    overlap = await _overlap(app, [block, meeting])

    assert overlap.options == [] and overlap.options_reason == "stale_source"


async def test_a_source_that_needs_a_full_resync_is_stale(app, live):
    block, meeting = _options_world(app, full_sync_required=True)

    overlap = await _overlap(app, [block, meeting])

    assert overlap.options == [] and overlap.options_reason == "stale_source"


async def test_any_stale_provider_source_silences_every_issue(app, live):
    rows = list(_options_world(app))
    other = _ws_row(entry_id="elsewhere", title="Lunch", start=_DAY + timedelta(hours=5))
    other["source_key"] = "provider:google:other"
    other["last_success_at"] = _NOW - timedelta(days=2)
    rows.append(other)

    overlap = await _overlap(app, rows)

    assert overlap.options == [] and overlap.options_reason == "stale_source"


async def test_slots_are_working_hours_in_the_owner_zone_not_utc(app, live):
    from zoneinfo import ZoneInfo

    window = {"start": _DAY - timedelta(days=1), "end": _DAY + timedelta(days=2)}

    overlap = await _overlap(app, list(_options_world(app)), zone="Asia/Tokyo", window=window)

    local = _move_start(overlap).astimezone(ZoneInfo("Asia/Tokyo"))
    assert 8 <= local.hour <= 18  # 10:45 UTC would be 19:45 in Tokyo: outside the day


async def test_no_saved_owner_zone_offers_nothing(app, live):
    overlap = await _overlap(app, list(_options_world(app)), zone=None)

    assert overlap.options == []


async def test_an_event_the_owner_does_not_organise_is_never_moved(app, live):
    block = _ws_row(entry_id="block", title="Focus", start=_DAY, time_role="butler")
    stranger = _ws_row(
        entry_id="mtg",
        title="Planning",
        start=_DAY + timedelta(minutes=30),
        event_metadata={
            "source_type": "provider_event",
            "attendees": [{"email": "other@example.com", "responseStatus": "accepted"}],
        },
    )

    overlap = await _overlap(app, [block, stranger])

    # No seat for the owner: ownership is unproven, so no decline and no move of that event.
    assert [o.kind for o in overlap.options] == ["move_block", "keep_both"]


async def test_the_organizer_address_proves_ownership(app, live):
    block = _ws_row(entry_id="block", title="Focus", start=_DAY, time_role="butler")
    own = _ws_row(
        entry_id="mtg",
        title="Mine",
        start=_DAY + timedelta(minutes=30),
        event_metadata={
            "source_type": "provider_event",
            "organizer": "Me@Example.com",
            "attendees": [
                {"email": "me@example.com", "self": True, "responseStatus": "accepted"},
                {"email": "guest@example.com", "responseStatus": "accepted"},
            ],
        },
    )

    overlap = await _overlap(app, [block, own])

    kinds = {o.kind for o in overlap.options}
    assert "move_own" in kinds and "decline" not in kinds


async def test_the_owner_role_copy_decides_the_digest_not_a_partner_copy(app, live):
    def world(owner_etag, partner_etag):
        block, meeting = _options_world(app)
        partner = dict(meeting, instance_id="a-mtg", source_time_role="household_member")
        partner["event_etag"] = partner_etag  # sorts first, so it survives the dedup
        meeting["event_etag"] = owner_etag
        return [block, meeting, partner]

    base = (await _overlap(app, world("e1", "p1"))).issue_digest

    assert (await _overlap(app, world("e1", "p2"))).issue_digest == base
    assert (await _overlap(app, world("e2", "p1"))).issue_digest != base


async def test_the_digest_follows_an_etag(app, live):
    block, meeting = _options_world(app)
    first = (await _overlap(app, [block, meeting])).issue_digest
    meeting["event_etag"] = "etag-mtg-2"

    assert (await _overlap(app, [block, meeting])).issue_digest != first


async def test_the_endpoint_serialises_options_and_the_digest(app, live):
    block, meeting = _options_world(
        app, last_synced_at=datetime.now(UTC), last_success_at=datetime.now(UTC)
    )
    app, db = _build_app(app, workspace_rows={"general": [block, meeting]})
    db.pool = MagicMock(return_value=_Pool("UTC"))

    resp = await _get(app)

    (overlap,) = [i for i in resp.json()["data"]["issues"] if i["kind"] == "overlap"]
    assert overlap["issue_digest"]
    assert overlap["options"][-1]["kind"] == "keep_both"
    assert all("title" not in o["rationale_fields"] for o in overlap["options"])


# ---------------------------------------------------------------------------
# Keep both: acknowledgement store, suppression, resolve route (bu-5ii4gj.11 R2a)
# ---------------------------------------------------------------------------


class _AckPool(_Pool):
    """In-memory stand-in for ``public.calendar_conflict_acknowledgements`` on the zone pool."""

    def __init__(self):
        super().__init__("UTC")
        self.rows: dict[str, tuple[str, str, str]] = {}

    async def fetch(self, sql, *args):
        if "calendar_conflict_acknowledgements" not in sql:
            return []
        return [
            {"pair_id": k, "issue_digest": v[0], "state_digest": v[1]} for k, v in self.rows.items()
        ]

    async def execute(self, sql, pair_id, digest, state, option_id):
        self.rows[pair_id] = (digest, state, option_id)


def _with_acks(db):
    pool = _AckPool()
    db.pool = MagicMock(return_value=pool)
    return pool


_POST_WINDOW = {"start": "2026-07-01T00:00:00Z", "end": "2026-07-02T00:00:00Z"}


async def _post(app, **body):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.post(
            "/api/calendar/workspace/conflicts/resolve", json={**_POST_WINDOW, **body}
        )


def _fresh_world(app):
    block, meeting = _options_world(
        app, last_synced_at=datetime.now(UTC), last_success_at=datetime.now(UTC)
    )
    app, db = _build_app(app, workspace_rows={"general": [block, meeting]})
    return app, db, _with_acks(db)


async def _offered(app):
    resp = await _get(app)
    (overlap,) = [i for i in resp.json()["data"]["issues"] if i["kind"] == "overlap"]
    keep = next(o for o in overlap["options"] if o["kind"] == "keep_both")
    return overlap["issue_digest"], keep


async def test_keep_both_is_recorded_and_the_overlap_goes_quiet(app, live):
    app, _, pool = _fresh_world(app)
    digest, keep = await _offered(app)

    resp = await _post(
        app,
        pair_id=keep["exact_call"]["args"]["pair_id"],
        option_id=keep["id"],
        issue_digest=digest,
    )

    assert resp.status_code == 200 and resp.json()["data"]["status"] == "acknowledged"
    assert pool.rows[keep["exact_call"]["args"]["pair_id"]][0] == digest
    assert pool.rows[keep["exact_call"]["args"]["pair_id"]][1]  # the events-only digest is stored
    quiet = (await _get(app)).json()["data"]["issues"]
    assert [i for i in quiet if i["kind"] == "overlap"] == []


async def test_a_repeated_keep_both_is_idempotent(app, live):
    app, _, _ = _fresh_world(app)
    digest, keep = await _offered(app)
    body = {
        "pair_id": keep["exact_call"]["args"]["pair_id"],
        "option_id": keep["id"],
        "issue_digest": digest,
    }
    await _post(app, **body)

    again = await _post(app, **body)

    assert again.status_code == 200
    assert again.json()["data"]["status"] == "already_acknowledged"


async def test_a_changed_overlap_is_never_acknowledged_on_a_stale_digest(app, live):
    app, _, pool = _fresh_world(app)
    _, keep = await _offered(app)

    resp = await _post(
        app,
        pair_id=keep["exact_call"]["args"]["pair_id"],
        option_id=keep["id"],
        issue_digest="stale",
    )

    assert resp.status_code == 409 and resp.json()["detail"] == "issue_changed"
    assert pool.rows == {}


async def _kept_world(app):
    """A scan of one overlap, then its acknowledgement stored as the route would store it."""
    block, meeting = _options_world(app, last_synced_at=_NOW, last_success_at=_NOW)
    rows = [block, meeting]
    _, db = _build_app(app, workspace_rows={"general": rows})
    pool = _with_acks(db)
    scan = await query_calendar_conflicts(db, **_WINDOW, now=_NOW)
    (issue,) = [i for i in scan.issues if i.kind == "overlap"]
    pool.rows[str(issue.pair_id)] = (issue.issue_digest, issue.state_digest, "keep_both")
    return db, rows, meeting


async def _overlaps(db, now=_NOW):
    scan = await query_calendar_conflicts(db, **_WINDOW, now=now)
    return [i for i in scan.issues if i.kind == "overlap"]


async def test_an_acknowledgement_lapses_when_an_event_changes(app, live):
    db, _, meeting = await _kept_world(app)
    assert await _overlaps(db) == []

    meeting["event_etag"] = "etag-mtg-moved"

    assert len(await _overlaps(db)) == 1


async def test_keep_both_still_suppresses_on_a_filtered_read(app, live):
    db, _, _ = await _kept_world(app)

    scan = await query_calendar_conflicts(db, **_WINDOW, now=_NOW, butlers=["general"])

    assert [i for i in scan.issues if i.kind == "overlap"] == []


async def test_the_passage_of_time_does_not_void_keep_both(app, live):
    db, _, _ = await _kept_world(app)

    # The option slots move with the clock, so the issue digest differs; the events do not.
    assert await _overlaps(db, now=_NOW + timedelta(minutes=50)) == []


async def test_an_unrelated_event_in_the_free_window_does_not_void_keep_both(app, live):
    db, rows, _ = await _kept_world(app)

    rows.append(_ws_row(entry_id="late", title="Review", start=_DAY + timedelta(minutes=105)))

    assert await _overlaps(db) == []


async def test_a_provider_writing_option_is_not_accepted_here(app, live):
    app, _, pool = _fresh_world(app)
    resp0 = await _get(app)
    (overlap,) = [i for i in resp0.json()["data"]["issues"] if i["kind"] == "overlap"]
    move = next(o for o in overlap["options"] if o["kind"] != "keep_both")

    resp = await _post(
        app,
        pair_id=overlap["options"][-1]["exact_call"]["args"]["pair_id"],
        option_id=move["id"],
        issue_digest=overlap["issue_digest"],
    )

    assert resp.status_code == 422 and pool.rows == {}


def _future_world(app, *, synced_at=None):
    """The overlap two days from now, so a move has somewhere to go ("now" is real here)."""
    day = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    synced = synced_at or datetime.now(UTC)
    block, meeting = _options_world(app, last_synced_at=synced, last_success_at=synced)
    for row, offset in ((block, 0), (meeting, 30)):
        row["instance_starts_at"] = day + timedelta(minutes=offset)
        row["instance_ends_at"] = day + timedelta(minutes=offset + 60)
    app, db = _build_app(app, workspace_rows={"general": [block, meeting]})
    pool = _with_acks(db)
    window = {
        "start": (day - timedelta(days=1)).isoformat(),
        "end": (day + timedelta(days=1)).isoformat(),
    }
    return app, pool, window


async def _offered_option(app, kind, window):
    resp = await _get(app, window)
    (overlap,) = [i for i in resp.json()["data"]["issues"] if i["kind"] == "overlap"]
    option = next(o for o in overlap["options"] if o["kind"] == kind)
    pair = overlap["options"][-1]["exact_call"]["args"]["pair_id"]
    return overlap, option, pair


async def test_the_route_is_absent_while_the_flag_is_dark(app):
    app, _, _ = _fresh_world(app)

    resp = await _post(app, pair_id="p", option_id="o", issue_digest="d")

    assert resp.status_code == 404


async def test_the_dark_route_answers_404_even_for_an_invalid_body(app):
    app, _, _ = _fresh_world(app)

    resp = await _post(app, pair_id="", option_id="o")  # no digest, empty pair id: would be 422

    assert resp.status_code == 404


async def test_a_post_a_few_seconds_after_the_get_still_matches_its_digest(app, live):
    """The digest must not drift with the clock inside one grid step (GET then POST -> 200)."""
    from butlers.api.routers import calendar_workspace as router

    # The morning of the overlap, mid grid step: "now" is what the first slot starts after.
    base = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=8, minute=2, second=0, microsecond=0
    )
    app, pool, window = _future_world(app, synced_at=base)
    real_scan = router.query_calendar_conflicts
    clock = {"now": base}

    async def scan(db, **kwargs):
        return await real_scan(db, now=clock["now"], **kwargs)

    with patch.object(router, "query_calendar_conflicts", scan):
        overlap, _, pair = await _offered_option(app, "keep_both", window)
        clock["now"] = base + timedelta(seconds=7)
        resp = await _post(
            app,
            pair_id=pair,
            option_id="keep_both",
            issue_digest=overlap["issue_digest"],
            **window,
        )

    assert resp.status_code == 200 and resp.json()["data"]["status"] == "acknowledged"
    assert pair in pool.rows


# ---------------------------------------------------------------------------
# Declared meeting hours (bu-5ii4gj.9 S4)
# ---------------------------------------------------------------------------

_HOURS = SchedulingPreferences.from_row(
    {
        "timezone": "UTC",
        "earliest_meeting_time": "09:00",
        "latest_meeting_time": "17:00",
        "meeting_days": ["MO", "TU", "WE", "TH", "FR"],
        "no_meeting_blocks": [{"start": "12:00", "end": "13:00"}],
    }
)


def _h(start: datetime, minutes: int = 60, **kw) -> ConflictCandidate:
    return replace(_c(start=start, minutes=minutes), hours_apply=True, **kw)


@pytest.mark.parametrize(
    ("start", "minutes", "reason"),
    [
        (datetime(2026, 7, 1, 7, 30, tzinfo=UTC), 60, "before_earliest"),  # a Wednesday
        (datetime(2026, 7, 1, 16, 30, tzinfo=UTC), 60, "after_latest"),
        (datetime(2026, 7, 4, 10, 0, tzinfo=UTC), 60, "off_day"),  # a Saturday
        (datetime(2026, 7, 1, 12, 30, tzinfo=UTC), 30, "no_meeting_block"),
    ],
)
def test_an_event_breaking_a_declared_limit_names_which(start, minutes, reason):
    (issue,) = detect_conflict_issues([_h(start, minutes)], preferences=_HOURS)

    assert issue.kind == "outside_declared_hours" and issue.reasons == [reason]
    assert [e.entry_id for e in issue.events] == [issue.events[0].entry_id]


def test_an_event_inside_the_declared_hours_is_clean():
    assert (
        detect_conflict_issues([_h(datetime(2026, 7, 1, 10, 0, tzinfo=UTC))], preferences=_HOURS)
        == []
    )


def test_without_declared_hours_nothing_is_flagged():
    early = _h(datetime(2026, 7, 1, 5, 0, tzinfo=UTC))
    assert detect_conflict_issues([early], preferences=None) == []
    assert detect_conflict_issues([early], preferences=SchedulingPreferences()) == []


def test_an_event_the_hours_do_not_apply_to_is_never_flagged():
    early = _c(start=datetime(2026, 7, 1, 5, 0, tzinfo=UTC))  # hours_apply defaults to False
    assert detect_conflict_issues([early], preferences=_HOURS) == []


@pytest.mark.parametrize(
    ("owner_response", "recurring", "severity"),
    [
        ("needsAction", False, "warning"),
        ("accepted", True, "warning"),
        ("accepted", False, "info"),
        ("tentative", False, "info"),
    ],
)
def test_severity_is_a_warning_for_an_unanswered_or_repeating_event(
    owner_response, recurring, severity
):
    event = _h(
        datetime(2026, 7, 1, 7, 0, tzinfo=UTC), owner_response=owner_response, recurring=recurring
    )
    (issue,) = detect_conflict_issues([event], preferences=_HOURS)
    assert issue.severity == severity


def test_the_hours_are_judged_in_the_owners_zone_not_the_events():
    # 07:30 UTC is 16:30 in Tokyo: inside the hours when they are Tokyo hours.
    tokyo = SchedulingPreferences.from_row(
        {"timezone": "Asia/Tokyo", "earliest_meeting_time": "09:00", "latest_meeting_time": "18:00"}
    )
    event = _h(datetime(2026, 7, 1, 7, 30, tzinfo=UTC))
    assert detect_conflict_issues([event], preferences=tokyo) == []


def _hours_patch(monkeypatch, row, general_zone=None):
    monkeypatch.setattr(
        "butlers.core.temporal.scheduling.get_scheduling_preferences",
        AsyncMock(return_value=row),
    )
    monkeypatch.setattr(
        "butlers.core.temporal.scheduling.load_configured_general_timezone",
        AsyncMock(return_value=general_zone),
    )


_ROW = {
    "timezone": "UTC",
    "earliest_meeting_time": "09:00",
    "latest_meeting_time": "17:00",
    "meeting_days": [],
    "no_meeting_blocks": [],
}


async def test_the_endpoint_flags_an_unanswered_invitation_outside_the_hours(app, monkeypatch):
    _hours_patch(monkeypatch, _ROW)
    early = _ws_row(
        entry_id="a",
        title="Early sync",
        start=datetime(2026, 7, 1, 7, 0, tzinfo=UTC),
        # a trusted organizer: an invitation from an untrusted one is quarantined and holds no time
        event_metadata={
            "attendees": [_self_attendee("needsAction")],
            "organizer_verdict": "known_entity",
        },
    )
    app, _ = _build_app(app, workspace_rows={"general": [early]})

    data = (await _get(app)).json()["data"]

    (issue,) = [i for i in data["issues"] if i["kind"] == "outside_declared_hours"]
    assert issue["severity"] == "warning" and issue["reasons"] == ["before_earliest"]
    assert [e["entry_id"] for e in issue["events"]] == ["a"]


async def test_the_endpoint_never_flags_declined_free_or_status_events(app, monkeypatch):
    _hours_patch(monkeypatch, _ROW)
    when = datetime(2026, 7, 1, 7, 0, tzinfo=UTC)
    declined = _ws_row(
        entry_id="d",
        title="x",
        start=when,
        event_metadata={"attendees": [_self_attendee("declined")]},
    )
    free = _ws_row(
        entry_id="f", title="x", start=when, event_metadata={"transparency": "transparent"}
    )
    ooo = _ws_row(entry_id="o", title="x", start=when)
    ooo["event_type"] = "outOfOffice"
    focus = _ws_row(entry_id="c", title="x", start=when)
    focus["event_type"] = "focusTime"
    app, _ = _build_app(app, workspace_rows={"general": [declined, free, ooo, focus]})

    data = (await _get(app)).json()["data"]

    assert [i for i in data["issues"] if i["kind"] == "outside_declared_hours"] == []


async def test_no_declared_hours_row_flags_nothing_and_is_a_genuine_read(app, monkeypatch):
    _hours_patch(monkeypatch, None)
    early = _ws_row(entry_id="a", title="x", start=datetime(2026, 7, 1, 3, 0, tzinfo=UTC))
    app, _ = _build_app(app, workspace_rows={"general": [early]})

    data = (await _get(app)).json()["data"]

    assert [i for i in data["issues"] if i["kind"] == "outside_declared_hours"] == []


async def test_an_unreadable_hours_store_flags_nothing_and_says_it_could_not_judge(
    app, monkeypatch
):
    monkeypatch.setattr(
        "butlers.core.temporal.scheduling.get_scheduling_preferences",
        AsyncMock(side_effect=RuntimeError("store down")),
    )
    early = _ws_row(entry_id="a", title="x", start=datetime(2026, 7, 1, 3, 0, tzinfo=UTC))
    app, mock_db = _build_app(app, workspace_rows={"general": [early]})

    scan = await query_calendar_conflicts(
        mock_db, start=datetime(2026, 7, 1, tzinfo=UTC), end=datetime(2026, 7, 2, tzinfo=UTC)
    )

    assert scan.hours_available is False
    assert [i for i in scan.issues if i.kind == "outside_declared_hours"] == []


async def test_a_quarantined_invitation_outside_the_hours_has_no_flag_and_no_entry(
    app, monkeypatch
):
    _hours_patch(monkeypatch, _ROW)
    stranger = _ws_row(
        entry_id="q",
        title="Free prize",
        start=datetime(2026, 7, 1, 7, 0, tzinfo=UTC),
        event_metadata={"attendees": [_self_attendee("needsAction")]},
    )
    app, _ = _build_app(app, workspace_rows={"general": [stranger]})

    data = (await _get(app)).json()["data"]

    assert data["issues"] == []


async def test_a_stored_utc_default_is_judged_in_the_owner_general_zone(app, monkeypatch):
    # 02:00 UTC is 11:00 in Tokyo: inside 09:00-17:00 once the stored UTC reads as unset.
    _hours_patch(monkeypatch, _ROW, general_zone="Asia/Tokyo")
    event = _ws_row(entry_id="a", title="x", start=datetime(2026, 7, 1, 2, 0, tzinfo=UTC))
    app, mock_db = _build_app(app, workspace_rows={"general": [event]})
    scan = await query_calendar_conflicts(
        mock_db, start=datetime(2026, 7, 1, tzinfo=UTC), end=datetime(2026, 7, 2, tzinfo=UTC)
    )
    assert [i for i in scan.issues if i.kind == "outside_declared_hours"] == []


def test_a_recurring_meeting_outside_the_hours_is_one_flag_on_its_first_occurrence():
    series = "series\x01origin:1"
    days = [datetime(2026, 7, d, 7, 30, tzinfo=UTC) for d in (1, 2, 3)]
    events = [
        _h(start, entry_id=f"occ{n}", recurring=True, series_key=series)
        for n, start in enumerate(days)
    ]
    issues = detect_conflict_issues(events, preferences=_HOURS)
    (issue,) = [i for i in issues if i.kind == "outside_declared_hours"]
    assert issue.events[0].entry_id == "occ0"
    assert issue.series_key == series and issue.occurrences == 3


def test_two_distinct_series_are_two_flags():
    early = datetime(2026, 7, 1, 7, 30, tzinfo=UTC)
    events = [
        _h(early, entry_id="a", recurring=True, series_key="series\x01one"),
        _h(early + timedelta(days=1), entry_id="b", recurring=True, series_key="series\x01two"),
    ]
    issues = detect_conflict_issues(events, preferences=_HOURS)
    assert len([i for i in issues if i.kind == "outside_declared_hours"]) == 2


async def test_an_unreadable_general_zone_makes_the_hours_unavailable(app, monkeypatch):
    _hours_patch(monkeypatch, _ROW)
    monkeypatch.setattr(
        "butlers.core.temporal.scheduling.load_configured_general_timezone",
        AsyncMock(side_effect=RuntimeError("state down")),
    )
    early = _ws_row(entry_id="a", title="x", start=datetime(2026, 7, 1, 3, 0, tzinfo=UTC))
    app, mock_db = _build_app(app, workspace_rows={"general": [early]})
    scan = await query_calendar_conflicts(
        mock_db, start=datetime(2026, 7, 1, tzinfo=UTC), end=datetime(2026, 7, 2, tzinfo=UTC)
    )
    assert scan.hours_available is False
    assert [i for i in scan.issues if i.kind == "outside_declared_hours"] == []
