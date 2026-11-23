# Spec: REQ-module-calendar-036
"""The owner day sheet: one deterministic projection of a local day (bu-5ii4gj.2 S1).

Pure functions only. Fixtures are synthetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.temporal.day_sheet import (
    DayOccurrence,
    SourceRead,
    build_day_sheet,
    render_day_sheet_text,
)

pytestmark = pytest.mark.unit

ZONE = "Asia/Singapore"
DAY = date(2026, 11, 24)
NOW = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)  # 23:00 local the night before
TZ = ZoneInfo(ZONE)


def at(hour: int, minute: int = 0, *, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


def occ(ref: str, start: datetime, end: datetime, **kw) -> DayOccurrence:
    kw.setdefault("title", f"Title {ref}")
    kw.setdefault("time_role", "owner")
    return DayOccurrence(ref=ref, starts_at=start, ends_at=end, **kw)


def sources(*, unreadable: tuple[str, ...] = (), age: timedelta = timedelta(minutes=5)):
    names = ("general", "travel")
    return [
        SourceRead(
            name=name,
            readable=name not in unreadable,
            as_of=None if name in unreadable else NOW - age,
            stale_after=timedelta(minutes=30),
        )
        for name in names
    ]


def sheet(occurrences, srcs=None, **kw):
    return build_day_sheet(
        occurrences,
        srcs if srcs is not None else sources(),
        zone=ZONE,
        local_date=DAY,
        now=NOW,
        **kw,
    )


def self_attendee(status: str) -> dict:
    return {"attendees": [{"self": True, "response_status": status}, {"email": "a@example.test"}]}


def test_only_the_owners_own_time_is_listed_in_local_order() -> None:
    result = sheet(
        [
            occ("late", at(15), at(16), metadata=self_attendee("accepted")),
            occ("declined", at(10), at(11), metadata=self_attendee("declined")),
            occ("free", at(11), at(12), metadata={"transparency": "transparent"}),
            occ("hold", at(9), at(10), time_role="butler"),
            occ("partner", at(13), at(14), time_role="unclassified"),
            occ("cancelled", at(12), at(13), status="cancelled"),
            occ("ooo", at(8), at(18), event_type="outOfOffice"),
        ]
    )

    assert [(i.ref, i.kind) for i in result.items] == [("hold", "butler"), ("late", "attended")]
    assert result.load.attended_minutes == 60 and result.load.count == 1


def test_an_invited_event_on_another_calendar_counts() -> None:
    result = sheet(
        [
            occ(
                "invited",
                at(10),
                at(11),
                time_role="other_person",
                metadata=self_attendee("accepted"),
            )
        ]
    )
    assert [i.ref for i in result.items] == ["invited"]


def test_tentative_is_listed_but_not_counted_and_blocks_free_time() -> None:
    result = sheet([occ("maybe", at(9), at(10), metadata=self_attendee("tentative"))])

    assert [(i.ref, i.kind) for i in result.items] == [("maybe", "tentative")]
    assert result.load.attended_minutes == 0
    assert [(g.start, g.end) for g in result.gaps] == [(at(8), at(9)), (at(10), at(20))]


def test_overlapping_attended_events_count_once_and_clip_to_the_day() -> None:
    result = sheet(
        [
            occ("a", at(9), at(11)),
            occ("b", at(10), at(12)),
            occ("overnight", at(22, day=date(2026, 11, 23)), at(1)),
        ]
    )

    assert result.load.attended_minutes == 3 * 60 + 60
    assert result.load.count == 3


def test_an_event_outside_the_day_is_ignored() -> None:
    result = sheet(
        [occ("yesterday", at(9, day=date(2026, 11, 23)), at(10, day=date(2026, 11, 23)))]
    )
    assert result.items == []


def test_all_day_items_come_first_and_carry_no_load() -> None:
    result = sheet(
        [
            occ("meeting", at(9), at(10)),
            occ("birthday-trip", at(0), at(0, day=date(2026, 11, 25)), all_day=True),
        ]
    )

    assert [i.ref for i in result.items] == ["birthday-trip", "meeting"]
    assert result.load.attended_minutes == 60


def test_gaps_are_inside_the_window_and_the_longest_is_reported() -> None:
    result = sheet([occ("a", at(9), at(10)), occ("b", at(13), at(14))])

    assert [(g.start, g.end) for g in result.gaps] == [
        (at(8), at(9)),
        (at(10), at(13)),
        (at(14), at(20)),
    ]
    assert result.longest_gap is not None and result.longest_gap.start == at(14)


def test_the_day_is_the_owner_local_day_across_dst() -> None:
    zone = "America/New_York"
    local = date(2026, 11, 1)  # fall back: 25 hours
    start = datetime.combine(local, time(0), tzinfo=ZoneInfo(zone))
    result = build_day_sheet(
        [occ("all", start, start + timedelta(hours=25))],
        sources(),
        zone=zone,
        local_date=local,
        now=NOW,
        window=(time(0), time(23, 59)),
    )
    assert result.load.attended_minutes == 25 * 60


def test_any_unreadable_source_lists_nothing_and_says_since_when() -> None:
    srcs = [
        SourceRead("general", True, as_of=NOW, stale_after=timedelta(minutes=30)),
        SourceRead("travel", False, as_of=NOW - timedelta(hours=1)),
    ]
    result = sheet([occ("a", at(9), at(10))], srcs)
    text = render_day_sheet_text(result)

    assert result.freshness.state == "unreadable"
    assert result.freshness.sources_unreadable == ["travel"]
    assert "Calendar unreadable since 22:00 (1 of 2 sources: travel)." in text  # 14:00 UTC
    assert "Title a" not in text and "Longest free" not in text
    assert result.items == [] and result.gaps == [] and result.longest_gap is None
    assert result.load.attended_minutes == 0


def test_all_sources_unreadable_lists_nothing_and_never_says_clear() -> None:
    result = sheet([], sources(unreadable=("general", "travel")))
    text = render_day_sheet_text(result)

    assert result.freshness.state == "unreadable"
    assert "Calendar unreadable (2 of 2 sources" in text
    assert "Nothing on your calendar" not in text


def test_no_known_source_is_unreadable_not_clear() -> None:
    result = sheet([], [])
    assert result.freshness.state == "unreadable"
    assert "Nothing on your calendar" not in render_day_sheet_text(result)


def test_empty_but_current_says_nothing_on_your_calendar() -> None:
    result = sheet([])
    assert result.freshness.state == "current"
    assert "Nothing on your calendar" in render_day_sheet_text(result)


def test_a_stale_read_is_prefixed_with_its_as_of_time() -> None:
    result = sheet([occ("a", at(9), at(10))], sources(age=timedelta(hours=3)))

    assert result.freshness.state == "stale"
    assert result.gaps == [] and result.longest_gap is None  # no free window from stale data
    text = render_day_sheet_text(result)
    assert text.splitlines()[0].startswith("As of 20:00")  # 12:00 UTC is 20:00 local
    assert "Nothing on your calendar" not in text


def test_a_stale_empty_day_is_not_called_clear() -> None:
    assert "Nothing on your calendar" not in render_day_sheet_text(
        sheet([], sources(age=timedelta(hours=3)))
    )


def test_the_text_defangs_third_party_titles_and_marks_tentative() -> None:
    result = sheet(
        [
            occ("a", at(9), at(10), title="Visit http://evil.example/x"),
            occ("m", at(11), at(12), metadata=self_attendee("tentative")),
        ]
    )
    text = render_day_sheet_text(result)

    assert "http://" not in text and "[:]" in text
    assert "09:00-10:00" in text and "? Title m" in text
    assert "Committed 1h" in text


def test_the_digest_is_order_free_and_changes_with_content() -> None:
    a, b = occ("a", at(9), at(10)), occ("b", at(11), at(12))

    assert sheet([a, b]).digest == sheet([b, a]).digest
    assert sheet([a, b]).digest != sheet([a]).digest
    assert (
        sheet([a]).digest
        != build_day_sheet(
            [a], sources(), zone=ZONE, local_date=DAY + timedelta(days=1), now=NOW
        ).digest
    )


def test_a_conflict_ref_is_attached_to_both_events() -> None:
    result = sheet(
        [occ("a", at(9), at(11)), occ("b", at(10), at(12))],
        conflicts={"a": "pair-1", "b": "pair-1"},
    )
    assert [i.conflict_ref for i in result.items] == ["pair-1", "pair-1"]


def test_each_item_has_a_door_and_an_invalid_zone_is_refused() -> None:
    assert sheet([occ("a", at(9), at(10))]).items[0].door == "/calendar?entry=a"
    with pytest.raises(ValueError):
        build_day_sheet([], sources(), zone="Not/AZone", local_date=DAY, now=NOW)


def test_the_digest_changes_when_a_title_or_conflict_changes() -> None:
    base = occ("a", at(9), at(10), title="Review")
    renamed = occ("a", at(9), at(10), title="Review (moved room)")
    # Same first 80 characters, different tail: truncation must not hide a re-title.
    long_a = occ("a", at(9), at(10), title="x" * 90 + "one")
    long_b = occ("a", at(9), at(10), title="x" * 90 + "two")

    assert sheet([base]).digest != sheet([renamed]).digest
    assert sheet([long_a]).digest != sheet([long_b]).digest
    assert sheet([base], conflicts={"a": "p1"}).digest != sheet([base]).digest


def test_an_all_day_event_matches_on_its_date_in_any_zone() -> None:
    # A Pacific all-day event for 24 Nov is still 24 Nov for an owner in Singapore.
    pacific = ZoneInfo("America/Los_Angeles")
    start = datetime(2026, 11, 24, 0, 0, tzinfo=pacific)
    end = datetime(2026, 11, 25, 0, 0, tzinfo=pacific)
    holiday = occ("trip", start, end, all_day=True)

    assert [i.ref for i in sheet([holiday]).items] == ["trip"]
    next_day = build_day_sheet(
        [holiday], sources(), zone=ZONE, local_date=DAY + timedelta(days=1), now=NOW
    )
    assert next_day.items == []


def test_all_day_items_are_not_meetings() -> None:
    result = sheet(
        [occ("trip", at(0), at(0, day=date(2026, 11, 25)), all_day=True), occ("m", at(9), at(10))]
    )
    assert result.load.count == 1
    assert "across 1 meeting." in render_day_sheet_text(result)


def test_butler_titles_are_defanged_too() -> None:
    result = sheet([occ("hold", at(9), at(10), time_role="butler", title="Call a@b.example")])
    assert "a[@]b[.]example" in render_day_sheet_text(result)


def test_a_recovered_empty_day_does_not_dedup_against_the_unreadable_one() -> None:
    unreadable = sheet([], sources(unreadable=("travel",)))
    recovered = sheet([], sources())
    stale = sheet([], sources(age=timedelta(hours=3)))

    assert len({unreadable.digest, recovered.digest, stale.digest}) == 3


def test_an_all_day_event_is_dated_in_its_own_zone_even_when_stored_in_utc() -> None:
    # Singapore all-day for 24 Nov, as a database returns it: UTC instants on the 23rd.
    start = datetime(2026, 11, 23, 16, 0, tzinfo=UTC)
    end = datetime(2026, 11, 24, 16, 0, tzinfo=UTC)
    holiday = occ("trip", start, end, all_day=True, timezone="Asia/Singapore")

    assert [i.ref for i in sheet([holiday]).items] == ["trip"]
    assert (
        build_day_sheet(
            [holiday], sources(), zone=ZONE, local_date=DAY - timedelta(days=1), now=NOW
        ).items
        == []
    )


def test_unreadable_source_names_are_cleaned_and_a_distant_since_shows_its_date() -> None:
    srcs = [
        SourceRead("general", True, as_of=NOW, stale_after=timedelta(minutes=30)),
        SourceRead("a@b.example", False, as_of=NOW - timedelta(days=2)),
    ]
    text = render_day_sheet_text(sheet([], srcs))

    assert "a[@]b[.]example" in text
    assert "since 21 Nov 23:00" in text  # 15:00 UTC two days earlier
