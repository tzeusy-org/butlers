"""The owner agenda line on /now: next, or free until (bu-5ii4gj.2 P3, REQ-module-calendar-052).

A pure reducer from the day verdict to one ``NowLine``. No model, no write. It never claims the
owner is free from a calendar it could not read or one that is stale: those render as unknown,
carrying the last known text when there is one. Titles are not part of any text, so the line is
safe for any audience.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from butlers.core.temporal.day_verdict import DayVerdict
from butlers.perception.now import NowLine
from butlers.perception.situation import Audience

AGENDA_KIND = "owner.agenda"
AGENDA_LABEL = "Next"
_SOURCE = "calendar"
#: A last-known value older than this is forgotten: "last known" must not outlive being useful.
MAX_LAST_KNOWN_AGE = timedelta(hours=6)


@dataclass(frozen=True, slots=True)
class LastKnownAgenda:
    """The last agenda text read from a current calendar, and when it was read."""

    text: str
    read_at: datetime


def _hhmm(value: datetime, zone: ZoneInfo) -> str:
    return f"{value.astimezone(zone):%H:%M}"


def _unknown(
    last: LastKnownAgenda | None, zone: ZoneInfo, *, reason: str, fresh_until: datetime
) -> NowLine:
    if last is not None:
        text = f"Unknown (last known: {last.text}, as of {_hhmm(last.read_at, zone)})"
        return NowLine(
            AGENDA_KIND, AGENDA_LABEL, "unknown", text, True, _SOURCE, None, None, fresh_until
        )
    text = f"Unknown ({reason})"
    return NowLine(
        AGENDA_KIND, AGENDA_LABEL, "unknown", text, False, _SOURCE, None, None, fresh_until
    )


def _midnight_after(now: datetime, zone: ZoneInfo) -> datetime:
    local = now.astimezone(zone)
    return datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=zone)


def agenda_text(verdict: DayVerdict, zone: ZoneInfo) -> tuple[str, str | None, datetime | None]:
    """``(text, door, boundary)`` for a readable verdict; ``boundary`` is when the text changes."""
    if verdict.current is not None:
        text = f"In meeting until {_hhmm(verdict.current.end, zone)}"
        if verdict.next is not None:
            text += f"; next {_hhmm(verdict.next.start, zone)}"
        return text, verdict.current.door, verdict.current.end
    if verdict.next is not None:
        return (
            f"Free until {_hhmm(verdict.next.start, zone)}",
            verdict.next.door,
            verdict.next.start,
        )
    return "No more today", None, None


def agenda_line(
    verdict: DayVerdict | None,
    *,
    now: datetime,
    zone: ZoneInfo,
    stale_after: timedelta,
    last_known: LastKnownAgenda | None = None,
    audience: Audience = Audience.OWNER,
) -> tuple[NowLine, LastKnownAgenda | None]:
    """The agenda line at ``now`` and the last-known value to carry forward.

    ``verdict`` is ``None`` when the calendar could not be asked at all. The returned last-known is
    replaced only by a read from a current calendar, so unknown never overwrites what was known, and
    it is dropped once older than ``MAX_LAST_KNOWN_AGE`` or at owner-local midnight. For any
    audience but the owner the line carries no door, which would name a third party's event; the
    text never carries a title.
    """
    if last_known is not None and (
        now - last_known.read_at > MAX_LAST_KNOWN_AGE
        or last_known.read_at.astimezone(zone).date() != now.astimezone(zone).date()
    ):
        # Too old, or from another owner-local day: yesterday's agenda is not last known today.
        last_known = None
    bound = now + stale_after
    if verdict is None or not verdict.available:
        return _unknown(
            last_known, zone, reason="calendar unreadable", fresh_until=bound
        ), last_known
    if verdict.freshness.state == "stale":
        # A free-until claim from a stale sync is a guess; say so instead.
        return _unknown(
            last_known, zone, reason="calendar out of date", fresh_until=bound
        ), last_known

    text, door, boundary = agenda_text(verdict, zone)
    # Current until the text next changes (a meeting ends or starts, or the day rolls over), or the
    # sync staleness bound, whichever is sooner.
    synced = verdict.freshness.as_of
    sync_bound = synced.astimezone(now.tzinfo) + stale_after if synced is not None else bound
    fresh_until = min(sync_bound, boundary or _midnight_after(now, zone))
    if audience is not Audience.OWNER:
        door = None
    line = NowLine(AGENDA_KIND, AGENDA_LABEL, "known", text, False, _SOURCE, 0.0, door, fresh_until)
    return line, LastKnownAgenda(text=text, read_at=now)
