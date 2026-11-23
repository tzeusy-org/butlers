"""The owner day sheet: one deterministic projection of a local day (bu-5ii4gj.2).

Every surface that says what the owner's day holds (dashboard, nightly message, ``/now`` card)
reads this projection, so they agree by construction and none can say "clear" when the
calendar could not be read. Pure functions, no I/O, no model. REQ-module-calendar-036.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.core.delivery_rendering import defang_delivery_text
from butlers.core.temporal.calendar_provenance import (
    _metadata_object,
    is_explicit_butler_generated,
    is_owner_attending,
    is_status_event,
    is_transparent,
)
from butlers.core.temporal.calendar_source_roles import asserts_owner_time

ItemKind = Literal["attended", "tentative", "butler"]
FreshnessState = Literal["current", "stale", "unreadable"]

DEFAULT_WINDOW: tuple[time, time] = (time(8, 0), time(20, 0))
MIN_GAP = timedelta(minutes=15)
_TITLE_LIMIT = 80


@dataclass(frozen=True)
class DayOccurrence:
    """One calendar occurrence as read, with its source's role."""

    ref: str
    title: str
    starts_at: datetime
    ends_at: datetime
    all_day: bool = False
    status: str = "confirmed"
    event_type: str = "default"
    time_role: str = "unclassified"
    metadata: Any = None


@dataclass(frozen=True)
class SourceRead:
    """Whether one calendar source could be read, and how recently it synced."""

    name: str
    readable: bool
    as_of: datetime | None = None
    stale_after: timedelta | None = None


@dataclass(frozen=True)
class DayItem:
    ref: str
    start: datetime
    end: datetime
    all_day: bool
    kind: ItemKind
    title: str
    label_class: Literal["owner_authored", "third_party"]
    people_count: int
    conflict_ref: str | None
    door: str


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class Load:
    attended_minutes: int
    count: int


@dataclass(frozen=True)
class Freshness:
    state: FreshnessState
    as_of: datetime | None
    sources_unreadable: list[str]
    sources_total: int


@dataclass(frozen=True)
class DaySheet:
    date: date
    zone: str
    freshness: Freshness
    items: list[DayItem]
    gaps: list[Gap]
    load: Load
    digest: str
    longest_gap: Gap | None = field(default=None)


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValueError(f"unknown owner zone: {name!r}") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("day sheet datetimes must be timezone-aware")
    return value.astimezone(UTC)


def _self_response(metadata: Any) -> str | None:
    obj = _metadata_object(metadata)
    attendees = obj.get("attendees") if obj is not None else None
    if not isinstance(attendees, list):
        return None
    for attendee in attendees:
        if isinstance(attendee, Mapping) and attendee.get("self") is True:
            raw = attendee.get("response_status", attendee.get("responseStatus"))
            return raw.strip().lower() if isinstance(raw, str) else None
    return None


def _people_count(metadata: Any) -> int:
    obj = _metadata_object(metadata)
    attendees = obj.get("attendees") if obj is not None else None
    if not isinstance(attendees, list):
        return 0
    return sum(1 for a in attendees if isinstance(a, Mapping) and a.get("self") is not True)


def _kind(occurrence: DayOccurrence) -> ItemKind:
    if occurrence.time_role == "butler" or is_explicit_butler_generated(occurrence.metadata):
        return "butler"
    return "tentative" if _self_response(occurrence.metadata) == "tentative" else "attended"


def _eligible(occurrence: DayOccurrence) -> bool:
    return (
        occurrence.status != "cancelled"
        and not is_status_event(occurrence.event_type)
        and asserts_owner_time(occurrence.time_role, occurrence.metadata)
        and is_owner_attending(occurrence.metadata)
        and not is_transparent(occurrence.metadata)
    )


def _merge(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _freshness(sources: Sequence[SourceRead], now: datetime) -> Freshness:
    unreadable = sorted(s.name for s in sources if not s.readable)
    readable = [s for s in sources if s.readable]
    as_of_values = [_utc(s.as_of) for s in readable if s.as_of is not None]
    as_of = min(as_of_values) if as_of_values else None
    if not sources or unreadable:
        state: FreshnessState = "unreadable"
    elif any(
        s.as_of is None or (s.stale_after is not None and _utc(now) - _utc(s.as_of) > s.stale_after)
        for s in readable
    ):
        state = "stale"
    else:
        state = "current"
    return Freshness(
        state=state, as_of=as_of, sources_unreadable=unreadable, sources_total=len(sources)
    )


def build_day_sheet(
    occurrences: Iterable[DayOccurrence],
    sources: Sequence[SourceRead],
    *,
    zone: str,
    local_date: date,
    now: datetime,
    window: tuple[time, time] = DEFAULT_WINDOW,
    conflicts: Mapping[str, str] | None = None,
) -> DaySheet:
    """Project the owner's local ``local_date`` from the occurrences and per-source read state."""
    tz = _zone(zone)
    day_start = _utc(datetime.combine(local_date, time(0), tzinfo=tz))
    day_end = _utc(datetime.combine(local_date + timedelta(days=1), time(0), tzinfo=tz))
    conflicts = conflicts or {}

    entries: list[tuple[DayOccurrence, datetime, datetime, ItemKind]] = []
    for occurrence in occurrences:
        if not _eligible(occurrence):
            continue
        start, end = _utc(occurrence.starts_at), _utc(occurrence.ends_at)
        end = max(end, start)
        if start >= day_end or (end <= day_start and start < day_start):
            continue
        entries.append((occurrence, start, end, _kind(occurrence)))
    entries.sort(key=lambda e: (not e[0].all_day, e[1], e[0].ref))

    items = [
        DayItem(
            ref=o.ref,
            start=start.astimezone(tz),
            end=end.astimezone(tz),
            all_day=o.all_day,
            kind=kind,
            title=" ".join(str(o.title or "").split())[:_TITLE_LIMIT],
            label_class="owner_authored" if kind == "butler" else "third_party",
            people_count=_people_count(o.metadata),
            conflict_ref=conflicts.get(o.ref),
            door=f"/calendar?entry={o.ref}",
        )
        for o, start, end, kind in entries
    ]

    def clipped(kinds: set[str]) -> list[tuple[datetime, datetime]]:
        return _merge(
            (max(start, day_start), min(end, day_end))
            for o, start, end, kind in entries
            if kind in kinds and not o.all_day and min(end, day_end) > max(start, day_start)
        )

    attended = clipped({"attended"})
    attended_minutes = sum(int((e - s).total_seconds() // 60) for s, e in attended)
    count = sum(1 for _o, _s, _e, kind in entries if kind == "attended")

    win_start = max(_utc(datetime.combine(local_date, window[0], tzinfo=tz)), day_start)
    win_end = min(_utc(datetime.combine(local_date, window[1], tzinfo=tz)), day_end)
    gaps: list[Gap] = []
    cursor = win_start
    for start, end in clipped({"attended", "tentative", "butler"}):
        if end <= cursor:
            continue
        if start > cursor and min(start, win_end) - cursor >= MIN_GAP:
            gaps.append(Gap(cursor.astimezone(tz), min(start, win_end).astimezone(tz)))
        cursor = max(cursor, end)
        if cursor >= win_end:
            break
    if win_end - cursor >= MIN_GAP:
        gaps.append(Gap(cursor.astimezone(tz), win_end.astimezone(tz)))
    longest = max(gaps, key=lambda g: (g.end - g.start, -g.start.timestamp()), default=None)

    digest = hashlib.sha256(
        json.dumps(
            [
                local_date.isoformat(),
                zone,
                [
                    [i.ref, _utc(i.start).isoformat(), _utc(i.end).isoformat(), i.kind, i.all_day]
                    for i in items
                ],
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    return DaySheet(
        date=local_date,
        zone=zone,
        freshness=_freshness(sources, now),
        items=items,
        gaps=gaps,
        load=Load(attended_minutes=attended_minutes, count=count),
        digest=digest,
        longest_gap=longest,
    )


def _duration(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    return " ".join(
        part for part in (f"{hours}h" if hours else "", f"{rest}m" if rest else "") if part
    )


def render_day_sheet_text(sheet: DaySheet) -> str:
    """The sheet as a fixed-template plain-text message. Never sent to a model."""
    tz = _zone(sheet.zone)
    state = sheet.freshness
    lines: list[str] = []
    if state.state == "stale" and state.as_of is not None:
        lines.append(f"As of {state.as_of.astimezone(tz):%H:%M} (the calendar may be out of date)")
    lines.append(f"Your day: {sheet.date:%a %d %b}")

    unreadable = len(state.sources_unreadable)
    if state.sources_total == 0:
        lines.append("Calendar unreadable: no calendar source is known.")
        return "\n".join(lines)
    if unreadable == state.sources_total:
        lines.append(f"Calendar unreadable ({unreadable} of {state.sources_total} sources).")
        return "\n".join(lines)
    if unreadable:
        names = ", ".join(state.sources_unreadable)
        lines.append(
            f"{unreadable} of {state.sources_total} calendar sources unreadable ({names}); "
            "this list may be incomplete."
        )

    if not sheet.items:
        lines.append(
            "Nothing on your calendar."
            if state.state == "current"
            else "No events found, but this read is not current."
        )
        return "\n".join(lines)

    for item in sheet.items:
        title = (
            item.title if item.label_class == "owner_authored" else defang_delivery_text(item.title)
        )
        mark = "? " if item.kind == "tentative" else ""
        suffix = " (butler hold)" if item.kind == "butler" else ""
        when = "All day" if item.all_day else f"{item.start:%H:%M}-{item.end:%H:%M}"
        lines.append(f"{when} {mark}{title}{suffix}")
    if sheet.load.attended_minutes:
        lines.append(
            f"Committed {_duration(sheet.load.attended_minutes)} across {sheet.load.count} "
            f"{'meeting' if sheet.load.count == 1 else 'meetings'}."
        )
    if sheet.longest_gap is not None:
        gap = sheet.longest_gap
        lines.append(f"Longest free window {gap.start:%H:%M}-{gap.end:%H:%M}.")
    return "\n".join(lines)
