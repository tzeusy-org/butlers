"""The owner day verdict: what the day holds, as a few deterministic facts (bu-5ii4gj.2 P2).

A projection of the day sheet, so the opener and the sheet agree by construction and no second
day-load computation exists. It never says a day is clear when the calendar could not be read:
an unreadable sheet gives ``available=False`` with no facts, and a stale one gives no free window.
Pure functions, no I/O, no model. REQ-module-calendar-051.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from butlers.core.temporal.day_sheet import MIN_GAP, DayItem, DaySheet, Freshness, Gap


@dataclass(frozen=True)
class DayVerdict:
    #: ``False`` when the calendar could not be read: no fact below may be shown as true.
    available: bool
    date: str
    zone: str
    freshness: Freshness
    #: The attended meeting in progress at ``now``, when there is one.
    current: DayItem | None
    #: The next attended meeting that starts after ``now``.
    next: DayItem | None
    committed_minutes: int
    committed_count: int
    tentative_count: int
    #: The longest free stretch still ahead of ``now``; ``None`` when stale or none is left.
    free_window: Gap | None
    #: Readable and current with nothing on the day at all (the one case "nothing on" is earned).
    empty: bool


def _timed_attended(sheet: DaySheet) -> list[DayItem]:
    return [i for i in sheet.items if i.kind == "attended" and not i.all_day]


def build_day_verdict(sheet: DaySheet, *, now: datetime) -> DayVerdict:
    """The verdict for ``sheet`` as seen at ``now`` (timezone-aware)."""
    if now.tzinfo is None:
        raise ValueError("day verdict now must be timezone-aware")
    freshness = sheet.freshness
    base = {"date": sheet.date.isoformat(), "zone": sheet.zone, "freshness": freshness}
    if freshness.state == "unreadable":
        return DayVerdict(
            available=False,
            current=None,
            next=None,
            committed_minutes=0,
            committed_count=0,
            tentative_count=0,
            free_window=None,
            empty=False,
            **base,
        )

    meetings = _timed_attended(sheet)
    current = next((i for i in meetings if i.start <= now < i.end), None)
    upcoming = next((i for i in meetings if i.start > now), None)

    ahead: list[Gap] = []
    for gap in sheet.gaps:
        if gap.end <= now:
            continue
        start = max(gap.start, now)
        if gap.end.timestamp() - start.timestamp() >= MIN_GAP.total_seconds():
            ahead.append(Gap(start, gap.end))
    free_window = max(
        ahead,
        key=lambda g: (g.end.timestamp() - g.start.timestamp(), -g.start.timestamp()),
        default=None,
    )

    return DayVerdict(
        available=True,
        current=current,
        next=upcoming,
        committed_minutes=sheet.load.attended_minutes,
        committed_count=sheet.load.count,
        tentative_count=sum(1 for i in sheet.items if i.kind == "tentative"),
        free_window=free_window,
        empty=freshness.state == "current" and not sheet.items,
        **base,
    )
