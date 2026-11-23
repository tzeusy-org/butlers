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
