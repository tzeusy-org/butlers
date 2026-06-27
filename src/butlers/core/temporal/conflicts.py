"""Pure conflict & overcommitment detection for the calendar radar.

Backs the ``calendar-conflict-overcommitment-radar`` capability: scans a set of
calendar events in a forward window for three issue kinds —

  - ``overlap``         — two timed events whose ranges intersect,
  - ``back_to_back``    — a chain of timed events on one day with sub-threshold
                          gaps between them,
  - ``overloaded_day``  — a calendar day whose total meeting time exceeds a
                          configured budget.

The detection is **pure and deterministic** — no DB, no provider, no LLM. The
windowed-event fetch (served by the GIST(tstzrange) index) and the proposal join
live in the read-model / MCP layers that call :func:`detect_conflict_issues`;
this module only computes the issues from already-fetched events so both the API
read-model and the calendar module's ``calendar_scan_conflicts`` tool can share
one detector.

All-day events are excluded from every detector: they are not "meetings", and a
24h all-day block would skew overlap, density, and overloaded-day signals.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

#: UUID5 namespace seed for canonical overlap-pair ids. The LLM fix-proposal
#: session derives a proposal's ``source_event_id`` from the same value so the
#: endpoint can re-attach the proposal to its issue (and so re-runs stay
#: idempotent).
_OVERLAP_PAIR_PREFIX = "calendar-conflict-overlap"

ConflictKind = str  # "overlap" | "back_to_back" | "overloaded_day"
ConflictSeverity = str  # "info" | "warning"

# Issue-kind ordering for deterministic output.
_KIND_ORDER: dict[str, int] = {"overlap": 0, "back_to_back": 1, "overloaded_day": 2}


@dataclass(frozen=True)
class ConflictCandidate:
    """A single calendar event considered by the radar.

    Neutral input shape so both the API read-model (building from workspace rows)
    and the calendar module tool (building from its own synced rows) can feed the
    same detector without importing each other's types.
    """

    entry_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str = "confirmed"
    all_day: bool = False


@dataclass
class ConflictEventRef:
    """An event contributing to a detected issue (matches the API model)."""

    entry_id: str
    title: str
    start_at: datetime
    end_at: datetime
    timezone: str
    status: str


@dataclass
class DetectedIssue:
    """A detected scheduling issue (pre proposal-join).

    ``pair_id`` is set only for ``overlap`` issues — the read-model uses it to
    attach any matching ``pending`` proposal ids. Other kinds carry ``None``.
    """

    kind: ConflictKind
    date: str  # YYYY-MM-DD in the display timezone
    summary: str
    severity: ConflictSeverity
    events: list[ConflictEventRef] = field(default_factory=list)
    pair_id: UUID | None = None
    #: UUIDs of ``pending`` fix proposals attached by the read-model's proposal
    #: join. Empty until the LLM fix-proposal session has run for the window.
    proposal_ids: list[str] = field(default_factory=list)


def overlap_pair_id(entry_id_a: str, entry_id_b: str) -> UUID:
    """Canonical, order-independent id for an overlapping event pair.

    Deterministic UUID5 of the sorted ``entry_id`` pair so a re-run of the
    fix-proposal session never duplicates a proposal and the endpoint can match a
    ``pending`` proposal's ``source_event_id`` back to its overlap issue.
    """
    a, b = sorted((str(entry_id_a), str(entry_id_b)))
    return uuid5(NAMESPACE_URL, f"{_OVERLAP_PAIR_PREFIX}:{a}:{b}")


def _norm_status(raw: str) -> str:
    """Collapse a workspace status to the radar's confirmed/tentative pair."""
    return "tentative" if str(raw).strip().lower() == "tentative" else "confirmed"


def _local_date(moment: datetime, display_tz: ZoneInfo | None, event_tz: str) -> str:
    """The calendar date (YYYY-MM-DD) a moment falls on, in the display tz.

    Falls back to the event's own timezone when no display tz is supplied, so a
    caller that does not pin a render timezone still groups each event by its own
    local day rather than UTC.
    """
    tz = display_tz
    if tz is None:
        try:
            tz = ZoneInfo(event_tz) if event_tz else None
        except Exception:
            tz = None
    local = moment.astimezone(tz) if tz is not None else moment
    return local.date().isoformat()


def _to_ref(c: ConflictCandidate) -> ConflictEventRef:
    return ConflictEventRef(
        entry_id=str(c.entry_id),
        title=c.title,
        start_at=c.start_at,
        end_at=c.end_at,
        timezone=c.timezone,
        status=_norm_status(c.status),
    )


def _humanize_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes / 60
    # 1.5h reads better than "90 min"; trim a trailing ".0".
    text = f"{hours:.1f}".rstrip("0").rstrip(".")
    return f"{text} h"


def detect_conflict_issues(
    events: list[ConflictCandidate],
    *,
    display_tz: ZoneInfo | None = None,
    back_to_back_gap_minutes: int = 15,
    overloaded_day_hours: float = 6.0,
) -> list[DetectedIssue]:
    """Detect overlap / back-to-back / overloaded-day issues over ``events``.

    Pure and deterministic. Cancelled and all-day events are ignored. Output is
    ordered by ``(date, kind, earliest start)`` so the response is stable.
    """
    timed = [
        c
        for c in events
        if not c.all_day and str(c.status).strip().lower() != "cancelled" and c.end_at > c.start_at
    ]
    timed.sort(key=lambda c: (c.start_at, c.end_at, str(c.entry_id)))

    issues: list[DetectedIssue] = []
    issues.extend(_detect_overlaps(timed, display_tz))
    issues.extend(_detect_back_to_back(timed, display_tz, max(0, int(back_to_back_gap_minutes))))
    issues.extend(_detect_overloaded_days(timed, display_tz, float(overloaded_day_hours)))

    issues.sort(
        key=lambda i: (
            i.date,
            _KIND_ORDER.get(i.kind, 9),
            i.events[0].start_at.timestamp() if i.events else 0.0,
        )
    )
    return issues


def _detect_overlaps(
    timed: list[ConflictCandidate], display_tz: ZoneInfo | None
) -> list[DetectedIssue]:
    """One issue per pair of intersecting half-open ``[start, end)`` ranges."""
    issues: list[DetectedIssue] = []
    n = len(timed)
    for i in range(n):
        a = timed[i]
        for j in range(i + 1, n):
            b = timed[j]
            # Sorted by start: once b starts at/after a ends, no later b overlaps a.
            if b.start_at >= a.end_at:
                break
            # Half-open overlap: a.start < b.end and b.start < a.end.
            if a.start_at < b.end_at and b.start_at < a.end_at:
                overlap = min(a.end_at, b.end_at) - max(a.start_at, b.start_at)
                minutes = max(0, int(overlap.total_seconds() // 60))
                summary = f"“{a.title}” and “{b.title}” overlap by {_humanize_minutes(minutes)}"
                issues.append(
                    DetectedIssue(
                        kind="overlap",
                        date=_local_date(a.start_at, display_tz, a.timezone),
                        summary=summary,
                        severity="warning",
                        events=[_to_ref(a), _to_ref(b)],
                        pair_id=overlap_pair_id(a.entry_id, b.entry_id),
                    )
                )
    return issues


def _detect_back_to_back(
    timed: list[ConflictCandidate],
    display_tz: ZoneInfo | None,
    gap_minutes: int,
) -> list[DetectedIssue]:
    """Maximal chains of same-day events separated by sub-threshold gaps."""
    by_day: dict[str, list[ConflictCandidate]] = defaultdict(list)
    for c in timed:
        by_day[_local_date(c.start_at, display_tz, c.timezone)].append(c)

    gap = timedelta(minutes=gap_minutes)
    issues: list[DetectedIssue] = []
    for day in sorted(by_day):
        day_events = sorted(by_day[day], key=lambda c: (c.start_at, c.end_at))
        chain: list[ConflictCandidate] = []
        running_end: datetime | None = None
        for c in day_events:
            # Keep this detector orthogonal to ``_detect_overlaps``: an event that
            # starts *before* the running end overlaps a chain member, so it is an
            # overlap (reported there) — not a back-to-back gap. Only a genuinely
            # adjacent event (``start >= running_end``) with a sub-threshold gap
            # extends the chain, so two overlapping meetings never also surface as
            # a redundant back-to-back card.
            if (
                chain
                and running_end is not None
                and c.start_at >= running_end
                and (c.start_at - running_end) < gap
            ):
                chain.append(c)
                running_end = max(running_end, c.end_at)
            else:
                if len(chain) >= 2:
                    issues.append(_back_to_back_issue(chain, day))
                chain = [c]
                running_end = c.end_at
        if len(chain) >= 2:
            issues.append(_back_to_back_issue(chain, day))
    return issues


def _back_to_back_issue(chain: list[ConflictCandidate], day: str) -> DetectedIssue:
    count = len(chain)
    severity = "warning" if count >= 3 else "info"
    summary = f"{count} back-to-back meetings with no real break"
    return DetectedIssue(
        kind="back_to_back",
        date=day,
        summary=summary,
        severity=severity,
        events=[_to_ref(c) for c in chain],
    )


def _detect_overloaded_days(
    timed: list[ConflictCandidate],
    display_tz: ZoneInfo | None,
    overloaded_day_hours: float,
) -> list[DetectedIssue]:
    """Days whose summed meeting duration exceeds the hours budget."""
    by_day: dict[str, list[ConflictCandidate]] = defaultdict(list)
    for c in timed:
        by_day[_local_date(c.start_at, display_tz, c.timezone)].append(c)

    issues: list[DetectedIssue] = []
    for day in sorted(by_day):
        day_events = sorted(by_day[day], key=lambda c: (c.start_at, c.end_at))
        total = sum((c.end_at - c.start_at for c in day_events), timedelta())
        hours = total.total_seconds() / 3600
        if hours > overloaded_day_hours:
            summary = f"{hours:.1f} h of meetings in one day"
            issues.append(
                DetectedIssue(
                    kind="overloaded_day",
                    date=day,
                    summary=summary,
                    severity="warning",
                    events=[_to_ref(c) for c in day_events],
                )
            )
    return issues
