# Spec: REQ-module-calendar-036
"""GET /api/calendar/workspace/day-sheet (bu-5ii4gj.2 S2): the one projection of an owner-local day.

Fake fan-out only; the real-schema seam is covered by the Docker integration test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.api.test_calendar_conflict_radar import _build_app, _ws_row

pytestmark = pytest.mark.unit

DAY = datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
SELF = {"self": True, "response_status": "accepted"}


def row(entry_id: str, start: datetime, *, fresh: bool = True, **kw) -> dict:
    result = _ws_row(entry_id=entry_id, title=f"Title {entry_id}", start=start, **kw)
    result["last_success_at"] = (
        datetime.now(UTC) if fresh else datetime.now(UTC) - timedelta(days=1)
    )
    return result


async def get_sheet(app, **params):
    params = {"date": "2026-07-01", "timezone": "UTC", **params}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/calendar/workspace/day-sheet", params=params)


async def test_only_the_owners_own_time_is_listed_in_order(app):
    rows = [
        row("late", DAY + timedelta(hours=6)),
        row(
            "declined", DAY, event_metadata={"attendees": [{**SELF, "response_status": "declined"}]}
        ),
        row("free", DAY + timedelta(hours=1), event_metadata={"transparency": "transparent"}),
        row("hold", DAY + timedelta(hours=2), time_role="butler"),
        row("partner", DAY + timedelta(hours=3), time_role="unclassified"),
        row("early", DAY + timedelta(hours=4)),
    ]
    app, _ = _build_app(app, workspace_rows={"general": rows})

    data = (await get_sheet(app)).json()["data"]

    assert [(i["ref"], i["kind"]) for i in data["items"]] == [
        ("hold", "butler"),
        ("early", "attended"),
        ("late", "attended"),
    ]
    assert data["freshness"]["state"] == "current"
    assert data["attended_count"] == 2
    assert data["digest"]


async def test_an_unreadable_schema_is_never_a_clear_day(app):
    app, _ = _build_app(
        app,
        workspace_rows={"general": [row("a", DAY)], "relationship": [row("b", DAY)]},
        workspace_failed=["relationship"],
    )

    data = (await get_sheet(app)).json()["data"]

    assert data["freshness"]["state"] == "unreadable"
    assert data["freshness"]["sources_unreadable"] == ["relationship"]
    assert "Calendar unreadable" in data["text"] and "1 of 2 sources" in data["text"]
    # Nothing is listed or counted from a partly unreadable read.
    assert data["items"] == [] and data["gaps"] == [] and data["attended_count"] == 0
    assert "Nothing on your calendar" not in data["text"]


async def test_a_day_with_no_known_source_is_unreadable_not_clear(app):
    app, _ = _build_app(app, workspace_rows={})

    data = (await get_sheet(app)).json()["data"]

    assert data["freshness"]["state"] == "unreadable"
    assert "Nothing on your calendar" not in data["text"]


async def test_a_genuinely_empty_current_day_says_so(app):
    elsewhere = row("elsewhere", DAY + timedelta(days=4))
    app, _ = _build_app(app, workspace_rows={"general": [elsewhere]})

    data = (await get_sheet(app)).json()["data"]

    assert data["items"] == []
    assert data["freshness"]["state"] == "current"
    assert "Nothing on your calendar" in data["text"]


async def test_a_stale_sync_is_reported_not_hidden(app):
    app, _ = _build_app(app, workspace_rows={"general": [row("a", DAY, fresh=False)]})

    data = (await get_sheet(app)).json()["data"]

    assert data["freshness"]["state"] == "stale"
    assert data["text"].startswith("As of ")


async def test_an_invalid_timezone_is_refused(app):
    app, _ = _build_app(app, workspace_rows={})
    assert (await get_sheet(app, timezone="Not/AZone")).status_code == 400


async def test_an_all_day_row_from_utc_is_dated_in_its_own_zone(app):
    """asyncpg returns timestamptz in UTC: a Singapore all-day for 24 Nov is 16:00 UTC on the 23rd."""
    start = datetime(2026, 11, 23, 16, 0, tzinfo=UTC)
    trip = row("trip", start, all_day=True, timezone="Asia/Singapore")
    trip["instance_ends_at"] = start + timedelta(days=1)
    app, _ = _build_app(app, workspace_rows={"general": [trip]})

    on_day = (await get_sheet(app, date="2026-11-24", timezone="Asia/Singapore")).json()["data"]
    day_before = (await get_sheet(app, date="2026-11-23", timezone="Asia/Singapore")).json()["data"]

    assert [i["ref"] for i in on_day["items"]] == ["trip"]
    assert day_before["items"] == []


@pytest.mark.parametrize("bad", ["0001-01-01", "9999-12-31"])
async def test_an_out_of_range_date_is_refused_not_a_500(app, bad):
    app, _ = _build_app(app, workspace_rows={})
    assert (await get_sheet(app, date=bad)).status_code == 400


async def test_a_malformed_row_makes_the_day_unreadable_not_a_500(app):
    broken = row("broken", DAY)
    del broken["instance_ends_at"]
    app, _ = _build_app(app, workspace_rows={"general": [broken]})

    response = await get_sheet(app)

    assert response.status_code == 200
    assert response.json()["data"]["freshness"]["state"] == "unreadable"


async def test_the_instance_timezone_wins_over_the_event_timezone(app):
    start = datetime(2026, 11, 23, 16, 0, tzinfo=UTC)
    trip = row("trip", start, all_day=True, timezone="Asia/Singapore")
    trip["instance_timezone"] = "Asia/Singapore"
    trip["event_timezone"] = "America/New_York"
    trip["instance_ends_at"] = start + timedelta(days=1)
    app, _ = _build_app(app, workspace_rows={"general": [trip]})

    data = (await get_sheet(app, date="2026-11-24", timezone="Asia/Singapore")).json()["data"]

    assert [i["ref"] for i in data["items"]] == ["trip"]


async def test_a_none_end_makes_the_day_unreadable_not_a_500(app):
    broken = row("broken", DAY)
    broken["instance_ends_at"] = None
    app, _ = _build_app(app, workspace_rows={"general": [broken]})

    response = await get_sheet(app)

    assert response.status_code == 200
    assert response.json()["data"]["freshness"]["state"] == "unreadable"


async def test_the_read_is_not_scoped_by_butler_attribution_and_spans_a_wide_window(app):
    """No ``butlers=`` row filter (it drops provider rows) and an all-day-safe window."""
    app, db = _build_app(app, workspace_rows={"general": [row("a", DAY)]})
    await get_sheet(app)
    window = [
        c for c in db.fan_out_with_status.await_args_list if "calendar_event_instances" in c.args[0]
    ]
    assert window
    for call in window:
        assert call.kwargs.get("butler_names") is not None  # default calendar-schema targets
        assert "butler_name" not in call.args[0].split("WHERE", 1)[1]
        start, end = call.args[1][2], call.args[1][1]
        assert end - start >= timedelta(hours=24 + 52)


async def test_the_telegram_push_equals_the_dashboard_sheet_for_the_same_date(app, monkeypatch):
    """One projection: a two-schema fixture yields the same text and digest on both surfaces."""
    from unittest.mock import AsyncMock
    from zoneinfo import ZoneInfo

    from butlers.jobs import day_sheet_push as job

    meeting = row("m1", DAY)
    other = row("m1", DAY)  # the same provider event in the second schema
    solo = row("m2", DAY + timedelta(hours=3))
    app, db = _build_app(app, workspace_rows={"general": [meeting, solo], "relationship": [other]})
    dashboard = (await get_sheet(app, date="2026-07-01", timezone="UTC")).json()["data"]

    sent = AsyncMock(return_value="delivered")
    monkeypatch.setenv(job.ENABLED_ENV, "1")
    monkeypatch.setattr(job, "owner_zoneinfo", AsyncMock(return_value=ZoneInfo("UTC")))
    monkeypatch.setattr(job, "_date_delivered", AsyncMock(return_value=False))
    monkeypatch.setattr(job, "_claim", AsyncMock(return_value=True))
    monkeypatch.setattr(job, "_release", AsyncMock())
    monkeypatch.setattr(job, "failed_attempts", AsyncMock(return_value=(0, None)))
    monkeypatch.setattr(
        job.owner_notify, "check_owner_notify_suppression", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(job.owner_notify, "send_owner_telegram", sent)
    monkeypatch.setattr(job, "state_set", AsyncMock())

    result = await job.run_day_sheet_push(db, now=datetime(2026, 6, 30, 22, 40, tzinfo=UTC))

    assert result["digest"] == dashboard["digest"]
    assert sent.await_args.kwargs["message"] == f"Tomorrow Prep\n{dashboard['text']}"
    assert dashboard["items"], "the fixture must actually list events"
