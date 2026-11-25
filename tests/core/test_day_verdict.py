# Spec: REQ-module-calendar-051
"""The owner day verdict: facts derived from the day sheet (bu-5ii4gj.2 P2).

Pure functions only. Fixtures are synthetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.temporal.day_sheet import DayOccurrence, SourceRead, build_day_sheet
from butlers.core.temporal.day_verdict import build_day_verdict

pytestmark = pytest.mark.unit

ZONE = "Asia/Singapore"
TZ = ZoneInfo(ZONE)
DAY = date(2026, 11, 24)
CHECKED = datetime(2026, 11, 24, 0, 30, tzinfo=TZ)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute), tzinfo=TZ)


def occ(ref: str, start: datetime, end: datetime, **kw) -> DayOccurrence:
    kw.setdefault("title", f"Title {ref}")
    kw.setdefault("time_role", "owner")
    return DayOccurrence(ref=ref, starts_at=start, ends_at=end, **kw)


def self_attendee(status: str) -> dict:
    return {"attendees": [{"self": True, "response_status": status}, {"email": "a@example.test"}]}


def sources(*, unreadable: bool = False, age: timedelta = timedelta(minutes=5)):
    return [
        SourceRead(
            name="general",
            readable=not unreadable,
            as_of=None if unreadable else CHECKED - age,
            stale_after=timedelta(minutes=30),
        )
    ]


def verdict(occurrences, *, now: datetime, srcs=None):
    sheet = build_day_sheet(
        occurrences,
        srcs if srcs is not None else sources(),
        zone=ZONE,
        local_date=DAY,
        now=CHECKED,
    )
    return build_day_verdict(sheet, now=now)


def busy_day() -> list[DayOccurrence]:
    return [
        occ("standup", at(9), at(9, 30)),
        occ("maybe", at(10), at(11), metadata=self_attendee("tentative")),
        occ("declined", at(11), at(12), metadata=self_attendee("declined")),
        occ("review", at(13), at(14)),
        occ("one-on-one", at(15), at(16)),
        occ(
            "ooo",
            at(0),
            datetime.combine(DAY + timedelta(days=1), time(0), tzinfo=TZ),
            all_day=True,
            event_type="outOfOffice",
        ),
    ]


def test_committed_minutes_next_and_free_window_for_a_busy_day() -> None:
    result = verdict(busy_day(), now=at(8, 0))

    assert result.available and not result.empty
    # Tentative, declined and the all-day out-of-office add no committed time.
    assert result.committed_minutes == 30 + 60 + 60
    assert result.committed_count == 3
    assert result.tentative_count == 1
    assert result.current is None
    assert result.next is not None and result.next.ref == "standup"
    # 08:00-09:00, 11:00-13:00 (tentative holds 10-11), 14:00-15:00, 16:00-20:00.
    assert result.free_window is not None
    assert (result.free_window.start, result.free_window.end) == (at(16), at(20))


def test_in_a_meeting_names_it_and_the_one_after() -> None:
    result = verdict(busy_day(), now=at(13, 30))

    assert result.current is not None and result.current.ref == "review"
    assert result.next is not None and result.next.ref == "one-on-one"
    assert result.free_window is not None
    assert (result.free_window.start, result.free_window.end) == (at(16), at(20))


def test_the_free_window_is_clipped_to_now() -> None:
    result = verdict([occ("a", at(9), at(10))], now=at(14, 0))

    assert result.next is None and result.current is None
    assert result.free_window is not None
    assert (result.free_window.start, result.free_window.end) == (at(14), at(20))


def test_nothing_left_today_has_no_next_and_no_window() -> None:
    result = verdict([occ("a", at(9), at(10))], now=at(21, 0))

    assert result.available and result.next is None and result.free_window is None


def test_a_window_shorter_than_the_minimum_is_not_offered() -> None:
    result = verdict([occ("a", at(9), at(19, 50))], now=at(19, 40))

    assert result.free_window is None


def test_a_butler_hold_is_neither_next_nor_committed() -> None:
    result = verdict([occ("hold", at(9), at(10), time_role="butler")], now=at(8))

    assert result.next is None and result.committed_minutes == 0
    assert not result.empty


def test_an_all_day_item_is_never_the_next_commitment() -> None:
    result = verdict(
        [
            occ(
                "trip",
                at(0),
                datetime.combine(DAY + timedelta(days=1), time(0), tzinfo=TZ),
                all_day=True,
            )
        ],
        now=at(8),
    )

    assert result.next is None and result.current is None


def test_an_unreadable_calendar_has_no_facts_and_is_not_available() -> None:
    result = verdict(busy_day(), now=at(8), srcs=sources(unreadable=True))

    assert result.available is False
    assert result.freshness.state == "unreadable"
    assert result.next is None and result.free_window is None
    assert result.committed_minutes == 0 and not result.empty


def test_no_known_source_is_unreadable_not_empty() -> None:
    result = verdict([], now=at(8), srcs=[])

    assert result.available is False and not result.empty


def test_a_stale_read_keeps_the_meetings_but_claims_no_free_window() -> None:
    result = verdict(busy_day(), now=at(8), srcs=sources(age=timedelta(hours=3)))

    assert result.available and result.freshness.state == "stale"
    assert result.next is not None and result.free_window is None
    assert not result.empty


def test_a_current_day_with_nothing_on_is_earned_empty() -> None:
    result = verdict([], now=at(8))

    assert result.available and result.empty
    assert result.next is None and result.committed_minutes == 0


def test_a_stale_empty_day_is_not_earned_empty() -> None:
    result = verdict([], now=at(8), srcs=sources(age=timedelta(hours=3)))

    assert result.available and not result.empty


def test_a_naive_now_is_refused() -> None:
    sheet = build_day_sheet([], sources(), zone=ZONE, local_date=DAY, now=CHECKED)
    with pytest.raises(ValueError):
        build_day_verdict(sheet, now=datetime(2026, 11, 24, 8, 0))


def test_a_day_that_has_not_started_names_its_first_meeting() -> None:
    result = verdict(busy_day(), now=datetime(2026, 11, 23, 20, 0, tzinfo=UTC))

    assert result.next is not None and result.next.ref == "standup"
