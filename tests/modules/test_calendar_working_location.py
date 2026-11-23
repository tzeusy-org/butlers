"""Today's declared working location is a typed read, not a signal (bu-s11n0s.4 S3, task 2.5).

``current_working_location`` reads the persisted declaration (core_341) and picks one by the
design's rule: timed over all-day, then latest start, then shortest, then the stable event id.
The answer is bounded to the owner-local day. It writes nothing and nothing calls it yet;
publishing it as a perception kind (``owner.work_location``) waits for the M7/.21 switch.
Real-Postgres selection lives in ``tests/integration/test_calendar_status_event_selection_pg.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from butlers.modules.calendar.working_location import (
    CurrentWorkingLocation,
    current_working_location,
    select_working_location,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 7, 1, 3, 0, tzinfo=UTC)  # 11:00 in Asia/Singapore
ZONE = "Asia/Singapore"
SGT_MIDNIGHT = datetime(2026, 7, 1, 16, 0, tzinfo=UTC)  # 2026-07-02 00:00 +08:00


def _row(
    *,
    starts_at: datetime,
    ends_at: datetime,
    all_day: bool = False,
    location_type: str | None = "customLocation",
    label: str | None = "Client site",
    event_id: uuid.UUID | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": event_id or uuid.uuid4(),
        "status": "confirmed",
        "event_type": "workingLocation",
        "starts_at": starts_at,
        "ends_at": ends_at,
        "all_day": all_day,
        "metadata": {},
        "time_role": "owner",
        "working_location_type": location_type,
        "working_location_label": label,
        **extra,
    }


def _all_day(**extra: Any) -> dict[str, Any]:
    # A one-day all-day event in the owner's zone: local midnight to local midnight.
    fields_ = {"location_type": "homeOffice", "label": "home office", **extra}
    return _row(
        starts_at=datetime(2026, 6, 30, 16, 0, tzinfo=UTC),
        ends_at=SGT_MIDNIGHT,
        all_day=True,
        **fields_,
    )


def _timed(start_h: int, end_h: int, **extra: Any) -> dict[str, Any]:
    return _row(
        starts_at=datetime(2026, 7, 1, start_h, 0, tzinfo=UTC),
        ends_at=datetime(2026, 7, 1, end_h, 0, tzinfo=UTC),
        **extra,
    )


def _select(rows: list[dict[str, Any]], **kwargs: Any) -> CurrentWorkingLocation | None:
    return select_working_location(rows, now=kwargs.pop("now", NOW), zone=kwargs.pop("zone", ZONE))


def test_no_declaration_is_no_location() -> None:
    assert _select([]) is None


def test_an_all_day_declaration_is_today_until_local_midnight() -> None:
    assert _select([_all_day()]) == CurrentWorkingLocation(
        type="homeOffice", label="home office", until=SGT_MIDNIGHT
    )


def test_a_timed_declaration_beats_an_all_day_one_and_keeps_its_own_end() -> None:
    chosen = _select([_all_day(), _timed(1, 5, label="Client site")])
    assert chosen == CurrentWorkingLocation(
        type="customLocation", label="Client site", until=datetime(2026, 7, 1, 5, tzinfo=UTC)
    )


def test_the_latest_start_wins_then_the_shortest_then_the_stable_id() -> None:
    assert _select([_timed(0, 6, label="early"), _timed(2, 6, label="late")]).label == "late"
    assert _select([_timed(2, 8, label="long"), _timed(2, 4, label="short")]).label == "short"
    low, high = uuid.UUID(int=1), uuid.UUID(int=2)
    tied = [_timed(2, 4, label="high", event_id=high), _timed(2, 4, label="low", event_id=low)]
    assert _select(tied).label == "low"
    assert _select(list(reversed(tied))).label == "low"


def test_a_multi_day_declaration_is_bounded_to_the_owner_local_day() -> None:
    week = _row(
        starts_at=datetime(2026, 6, 28, 16, tzinfo=UTC),
        ends_at=datetime(2026, 7, 3, 16, tzinfo=UTC),
        all_day=True,
    )
    assert _select([week]).until == SGT_MIDNIGHT


@pytest.mark.parametrize(
    "row",
    [
        _all_day(metadata={"butler_generated": True}),
        _all_day(metadata={"attendees": [{"self": True, "response_status": "declined"}]}),
        _all_day(status="cancelled"),
        _all_day(event_type="outOfOffice"),
        _all_day(location_type=None, label=None),
        _all_day(label=""),
        _timed(4, 6),  # not started
        _timed(0, 3),  # ended exactly now: end-exclusive
    ],
    ids=[
        "butler-generated",
        "self-declined",
        "cancelled",
        "not-working-location",
        "no-normalized-location",
        "empty-label",
        "future",
        "ended",
    ],
)
def test_ineligible_rows_never_declare_a_location(row: dict[str, Any]) -> None:
    assert _select([row]) is None


def test_spring_forward_day_ends_at_the_real_local_midnight() -> None:
    # 2026-03-08 is a 23-hour day in New York; the next midnight is EDT, not now + 24h.
    now = datetime(2026, 3, 8, 15, 0, tzinfo=UTC)
    row = _row(starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=3), all_day=True)
    chosen = _select([row], now=now, zone="America/New_York")
    assert chosen is not None
    assert chosen.until == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)


def test_fall_back_day_ends_at_the_real_local_midnight() -> None:
    # 2026-11-01 is a 25-hour day in New York: 00:30 EDT now, next midnight is 00:00 EST.
    now = datetime(2026, 11, 1, 4, 30, tzinfo=UTC)
    row = _row(starts_at=now - timedelta(hours=1), ends_at=now + timedelta(days=3), all_day=True)
    chosen = _select([row], now=now, zone="America/New_York")
    assert chosen is not None
    assert chosen.until == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "zone"),
    [(NOW, "Not/AZone"), (NOW, ""), (NOW.replace(tzinfo=None), ZONE)],
    ids=["unknown-zone", "empty-zone", "naive-now"],
)
def test_an_unusable_clock_is_refused(now: datetime, zone: str) -> None:
    with pytest.raises(ValueError):
        select_working_location([_all_day()], now=now, zone=zone)


def test_the_answer_carries_only_the_declaration_and_its_bound() -> None:
    assert [field.name for field in fields(CurrentWorkingLocation)] == ["type", "label", "until"]


class _ReadOnlyConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        return self.rows


async def test_the_read_is_one_bounded_select_and_applies_the_rule() -> None:
    conn = _ReadOnlyConn([_all_day(), _timed(1, 5, label="Client site")])
    chosen = await current_working_location(conn, now=NOW, zone=ZONE)
    assert chosen is not None and chosen.label == "Client site"
    [(sql, args)] = conn.calls
    assert sql.lstrip().upper().startswith("SELECT")
    assert "calendar_events" in sql and "workingLocation" in sql
    assert "JOIN calendar_sources cs ON cs.id = ce.source_id" in sql
    assert args == (NOW,)


async def test_an_unusable_clock_is_refused_before_any_read() -> None:
    conn = _ReadOnlyConn([_all_day()])
    with pytest.raises(ValueError):
        await current_working_location(conn, now=NOW, zone="Not/AZone")
    assert conn.calls == []


# -- source roles: another person's declared location is not the owner's (bu-5ii4gj.1) ----------


@pytest.mark.parametrize("role", ["unclassified", "household_member", "other_person", "team"])
def test_another_persons_declared_location_is_not_the_owners(role: str) -> None:
    row = _row(starts_at=NOW - timedelta(hours=1), ends_at=NOW + timedelta(hours=3), time_role=role)
    assert select_working_location([row], now=NOW, zone=ZONE) is None


def test_an_invited_declaration_on_another_calendar_counts() -> None:
    row = _row(
        starts_at=NOW - timedelta(hours=1),
        ends_at=NOW + timedelta(hours=3),
        time_role="household_member",
        metadata={"attendees": [{"self": True, "response_status": "accepted"}]},
    )
    chosen = select_working_location([row], now=NOW, zone=ZONE)
    assert chosen is not None and chosen.label == "Client site"
