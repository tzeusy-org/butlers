# Spec: REQ-module-calendar-052
"""The owner's agenda line on /now: in a meeting, free until, none left, or unknown (bu-5ii4gj.2 P3).

Pure reducer over the day verdict. Fixtures are synthetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.temporal.day_sheet import DayOccurrence, SourceRead, build_day_sheet
from butlers.core.temporal.day_verdict import build_day_verdict
from butlers.perception.agenda import (
    AGENDA_KIND,
    MAX_LAST_KNOWN_AGE,
    LastKnownAgenda,
    agenda_line,
)
from butlers.perception.situation import Audience

pytestmark = pytest.mark.unit

ZONE = "Asia/Singapore"
TZ = ZoneInfo(ZONE)
DAY = date(2026, 11, 24)
STALE_AFTER = timedelta(minutes=30)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(DAY, time(hour, minute), tzinfo=TZ)


def occ(ref: str, start: datetime, end: datetime, **kw) -> DayOccurrence:
    kw.setdefault("title", f"Secret title {ref}")
    kw.setdefault("time_role", "owner")
    return DayOccurrence(ref=ref, starts_at=start, ends_at=end, **kw)


def line_for(
    occurrences,
    *,
    now: datetime,
    age: timedelta = timedelta(minutes=5),
    unreadable=False,
    last=None,
    sources=None,
):
    srcs = (
        sources
        if sources is not None
        else [
            SourceRead(
                name="general",
                readable=not unreadable,
                as_of=None if unreadable else now - age,
                stale_after=STALE_AFTER,
            )
        ]
    )
    sheet = build_day_sheet(occurrences, srcs, zone=ZONE, local_date=DAY, now=now)
    verdict = build_day_verdict(sheet, now=now)
    return agenda_line(verdict, now=now, zone=TZ, stale_after=STALE_AFTER, last_known=last)


def test_in_a_meeting_says_until_when_and_what_is_next() -> None:
    line, last = line_for([occ("a", at(14), at(15)), occ("b", at(16), at(17))], now=at(14, 20))

    assert line.kind == AGENDA_KIND and line.state == "known" and not line.stale
    assert line.text == "In meeting until 15:00; next 16:00"
    assert line.door == "/calendar?entry=a"
    assert line.fresh_until == at(14, 50)  # the sync staleness bound is sooner than 15:00
    assert last is not None and last.text == line.text


def test_in_the_last_meeting_has_no_next_clause() -> None:
    line, _ = line_for([occ("a", at(14), at(15))], now=at(14, 50))
    assert line.text == "In meeting until 15:00"


def test_between_meetings_says_free_until_the_next_start() -> None:
    line, _ = line_for([occ("b", at(16), at(17))], now=at(14, 0))

    assert line.text == "Free until 16:00"
    assert line.door == "/calendar?entry=b"


def test_nothing_left_today() -> None:
    line, _ = line_for([occ("a", at(9), at(10))], now=at(14, 0))

    assert line.text == "No more today" and line.state == "known" and line.door is None
    assert line.fresh_until == at(14, 30)


def test_an_empty_current_day_is_no_more_today_not_unknown() -> None:
    line, _ = line_for([], now=at(8, 0))
    assert line.text == "No more today" and line.state == "known"


def test_declined_and_transparent_events_are_ignored() -> None:
    declined = {"attendees": [{"self": True, "response_status": "declined"}]}
    line, _ = line_for(
        [
            occ("d", at(14), at(15), metadata=declined),
            occ("t", at(14), at(15), metadata={"transparency": "transparent"}),
        ],
        now=at(14, 20),
    )
    assert line.text == "No more today"


def test_an_unreadable_calendar_is_unknown_with_no_free_claim() -> None:
    line, last = line_for([occ("b", at(16), at(17))], now=at(14, 0), unreadable=True)

    assert line.state == "unknown" and line.stale is False
    assert line.text == "Unknown (calendar unreadable)"
    assert "free" not in line.text.lower() and line.door is None
    assert last is None


def test_unreadable_carries_the_last_known_value_marked_stale() -> None:
    previous = LastKnownAgenda(text="Free until 16:00", read_at=at(14, 10))
    line, last = line_for([], now=at(14, 40), unreadable=True, last=previous)

    assert line.state == "unknown" and line.stale is True
    assert line.text == "Unknown (last known: Free until 16:00, as of 14:10)"
    assert last == previous  # unknown never overwrites what was known


def test_a_stale_sync_is_unknown_rather_than_a_free_claim() -> None:
    line, last = line_for([occ("b", at(16), at(17))], now=at(14, 0), age=timedelta(hours=3))

    assert line.state == "unknown"
    assert line.text == "Unknown (calendar out of date)"
    assert last is None


def test_no_known_source_is_unknown() -> None:
    line, _ = line_for([], now=at(8), sources=[])
    assert line.state == "unknown" and "free" not in line.text.lower()


def test_a_missing_verdict_is_unknown() -> None:
    line, _ = agenda_line(None, now=at(8), zone=TZ, stale_after=STALE_AFTER)
    assert line.text == "Unknown (calendar unreadable)"


def test_no_text_carries_an_event_title() -> None:
    for now in (at(14, 20), at(14, 0)):
        line, _ = line_for([occ("a", at(14), at(15)), occ("b", at(16), at(17))], now=now)
        assert "Secret" not in line.text


def test_the_text_is_in_the_owner_zone_not_utc() -> None:
    line, _ = line_for([occ("b", at(16), at(17))], now=at(14, 0).astimezone(UTC))
    assert line.text == "Free until 16:00"


def test_an_expired_last_known_value_is_forgotten_not_carried() -> None:
    old = LastKnownAgenda(text="Free until 16:00", read_at=at(14, 0) - MAX_LAST_KNOWN_AGE * 2)
    line, last = line_for([], now=at(14, 0), unreadable=True, last=old)

    assert line.text == "Unknown (calendar unreadable)" and line.stale is False
    assert last is None


def test_with_no_cache_an_unreadable_calendar_never_yields_a_value() -> None:
    line, last = line_for([occ("a", at(14), at(15))], now=at(14, 20), unreadable=True, last=None)

    assert line.state == "unknown" and line.text == "Unknown (calendar unreadable)"
    assert last is None
    assert not any(ch.isdigit() for ch in line.text)


def test_owner_and_non_owner_renders_both_omit_titles_and_the_non_owner_gets_no_door() -> None:
    occurrences = [occ("a", at(14), at(15)), occ("b", at(16), at(17))]
    sheet = build_day_sheet(
        occurrences,
        [SourceRead(name="g", readable=True, as_of=at(14), stale_after=STALE_AFTER)],
        zone=ZONE,
        local_date=DAY,
        now=at(14, 20),
    )
    verdict = build_day_verdict(sheet, now=at(14, 20))

    owner, _ = agenda_line(verdict, now=at(14, 20), zone=TZ, stale_after=STALE_AFTER)
    other, _ = agenda_line(
        verdict, now=at(14, 20), zone=TZ, stale_after=STALE_AFTER, audience=Audience.AVAILABILITY
    )

    assert owner.text == other.text == "In meeting until 15:00; next 16:00"
    assert "Secret" not in owner.text and "Secret" not in other.text
    assert owner.door == "/calendar?entry=a" and other.door is None
