# Spec: REQ-module-calendar-051
"""GET /api/calendar/workspace/day-verdict (bu-5ii4gj.2 P2): a few facts about one owner-local day.

Fake fan-out only; the verdict is derived from the same day sheet the sheet route serves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.api.test_calendar_conflict_radar import _build_app, _ws_row

pytestmark = pytest.mark.unit

# A future day, so the verdict is read before any of its meetings have started.
_DAY = (datetime.now(UTC) + timedelta(days=3)).date()
MIDNIGHT = datetime(_DAY.year, _DAY.month, _DAY.day, tzinfo=UTC)
SELF = {"self": True, "response_status": "accepted"}


def row(entry_id: str, hour: float, *, minutes: int = 60, fresh: bool = True, **kw) -> dict:
    result = _ws_row(
        entry_id=entry_id,
        title=f"Title {entry_id}",
        start=MIDNIGHT + timedelta(hours=hour),
        minutes=minutes,
        **kw,
    )
    result["last_success_at"] = (
        datetime.now(UTC) if fresh else datetime.now(UTC) - timedelta(days=1)
    )
    return result


async def get_verdict(app, **params):
    params = {"date": _DAY.isoformat(), "timezone": "UTC", **params}
    params = {k: v for k, v in params.items() if v is not None}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/calendar/workspace/day-verdict", params=params)


def six_meetings() -> list[dict]:
    return [
        row("standup", 9, minutes=30),
        row("maybe", 10, event_metadata={"attendees": [{**SELF, "response_status": "tentative"}]}),
        row(
            "declined", 11, event_metadata={"attendees": [{**SELF, "response_status": "declined"}]}
        ),
        row("review", 13),
        row("one-on-one", 15),
        {**row("ooo", 0, minutes=24 * 60, all_day=True), "event_type": "outOfOffice"},
    ]


async def test_committed_minutes_next_and_free_window_over_a_busy_day(app):
    app, _ = _build_app(app, workspace_rows={"general": six_meetings()})

    response = await get_verdict(app)
    data = response.json()["data"]

    assert response.status_code == 200
    assert data["available"] is True and data["empty"] is False
    assert data["relative"] == "other"
    assert data["next"]["ref"] == "standup"
    assert data["next"]["door"] == "/calendar?entry=standup"
    assert data["current"] is None
    assert data["committed_minutes"] == 30 + 60 + 60
    assert data["committed_count"] == 3
    assert data["tentative_count"] == 1
    assert data["free_window"]["minutes"] == 4 * 60
    assert data["free_window"]["start"].startswith(f"{_DAY.isoformat()}T16:00")
    assert data["freshness"]["state"] == "current"


async def test_a_failed_read_is_unavailable_not_clear(app):
    app, _ = _build_app(
        app,
        workspace_rows={"general": six_meetings(), "relationship": [row("b", 9)]},
        workspace_failed=["relationship"],
    )

    data = (await get_verdict(app)).json()["data"]

    assert data["available"] is False
    assert data["freshness"]["state"] == "unreadable"
    assert data["freshness"]["sources_unreadable"] == ["relationship"]
    assert data["next"] is None and data["free_window"] is None
    assert data["committed_minutes"] == 0 and data["empty"] is False


async def test_a_day_with_no_known_source_is_unavailable(app):
    app, _ = _build_app(app, workspace_rows={})

    data = (await get_verdict(app)).json()["data"]

    assert data["available"] is False and data["empty"] is False


async def test_a_genuinely_empty_current_day_is_earned_empty(app):
    elsewhere = row("elsewhere", 9)
    elsewhere["instance_starts_at"] = elsewhere["instance_starts_at"] + timedelta(days=4)
    elsewhere["instance_ends_at"] = elsewhere["instance_ends_at"] + timedelta(days=4)
    app, _ = _build_app(app, workspace_rows={"general": [elsewhere]})

    data = (await get_verdict(app)).json()["data"]

    assert data["available"] is True and data["empty"] is True
    assert data["next"] is None and data["committed_minutes"] == 0


async def test_a_stale_sync_keeps_meetings_but_claims_no_free_window(app):
    app, _ = _build_app(app, workspace_rows={"general": [row("a", 9, fresh=False)]})

    data = (await get_verdict(app)).json()["data"]

    assert data["freshness"]["state"] == "stale"
    assert data["next"]["ref"] == "a" and data["free_window"] is None
    assert data["empty"] is False


async def test_an_invalid_timezone_is_refused(app):
    app, _ = _build_app(app, workspace_rows={})

    assert (await get_verdict(app, timezone="Not/AZone")).status_code == 400


@pytest.mark.parametrize("bad", ["0001-01-01", "9999-12-31"])
async def test_an_out_of_range_date_is_refused(app, bad):
    app, _ = _build_app(app, workspace_rows={})

    assert (await get_verdict(app, date=bad)).status_code == 400


async def test_without_a_date_the_day_is_today_or_tomorrow_by_local_hour(app):
    app, _ = _build_app(app, workspace_rows={"general": [row("a", 9)]})

    data = (await get_verdict(app, date=None)).json()["data"]

    assert data["relative"] in {"today", "tomorrow"}


def _pin_clock(monkeypatch, instant: datetime) -> None:
    from butlers.api.routers import calendar_workspace as routes

    monkeypatch.setattr(routes, "_now", lambda: instant)


@pytest.mark.parametrize(
    ("instant", "relative", "day"),
    [
        (datetime(2026, 11, 26, 9, 59, tzinfo=UTC), "today", "2026-11-26"),  # 17:59 SGT
        (datetime(2026, 11, 26, 10, 0, tzinfo=UTC), "tomorrow", "2026-11-27"),  # 18:00 SGT
        (datetime(2026, 11, 26, 15, 59, tzinfo=UTC), "tomorrow", "2026-11-27"),  # 23:59 SGT
        (datetime(2026, 11, 26, 16, 0, tzinfo=UTC), "today", "2026-11-27"),  # 00:00 SGT next day
    ],
)
async def test_the_verdict_day_flips_at_18_local_and_is_named_by_its_real_date(
    app, monkeypatch, instant, relative, day
):
    _pin_clock(monkeypatch, instant)
    app, _ = _build_app(app, workspace_rows={"general": [row("a", 9)]})

    data = (await get_verdict(app, date=None, timezone="Asia/Singapore")).json()["data"]

    assert (data["relative"], data["date"]) == (relative, day)


async def test_the_18_local_flip_uses_the_owners_zone_on_a_daylight_saving_day(app, monkeypatch):
    # 2026-03-08 is spring-forward in New York: 18:00 local is 22:00Z (UTC-4), not 23:00Z.
    _pin_clock(monkeypatch, datetime(2026, 3, 8, 22, 0, tzinfo=UTC))
    app, _ = _build_app(app, workspace_rows={"general": [row("a", 9)]})

    data = (await get_verdict(app, date=None, timezone="America/New_York")).json()["data"]
    before = datetime(2026, 3, 8, 21, 59, tzinfo=UTC)
    _pin_clock(monkeypatch, before)
    earlier = (await get_verdict(app, date=None, timezone="America/New_York")).json()["data"]

    assert (data["relative"], data["date"]) == ("tomorrow", "2026-03-09")
    assert (earlier["relative"], earlier["date"]) == ("today", "2026-03-08")


async def test_committed_minutes_on_a_spring_forward_day_are_elapsed_time(app):
    # 01:00 EST to 04:00 EDT spans the skipped 02:00 hour: two elapsed hours, not three.
    start = datetime(2026, 3, 8, 6, 0, tzinfo=UTC)
    spanning = _ws_row(entry_id="dst", title="Overnight", start=start, minutes=120)
    spanning["last_success_at"] = datetime.now(UTC)
    app, _ = _build_app(app, workspace_rows={"general": [spanning]})

    data = (await get_verdict(app, date="2026-03-08", timezone="America/New_York")).json()["data"]

    assert data["committed_minutes"] == 120 and data["committed_count"] == 1
