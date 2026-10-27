"""Shared medication frequency and adherence-denominator utilities.

This module is the single source of truth for:

1. Converting a textual dosing frequency string into a doses-per-day float
   (``frequency_to_doses_per_day``).
2. Turning that rate into an expected-dose count over a window
   (``expected_dose_count``), capped at the medication's own age so a
   medication created partway through the window is never scored against
   doses expected before it existed.

It is imported by:

- ``roster/health/api/router.py``  (GET /medications/{id}/adherence route)
- ``roster/health/jobs/health_jobs.py``  (insight-scan job: refill estimation
  and adherence-dip correlation)
- ``roster/health/tools/reports.py``  (``trend_report``'s medication_adherence)

Having one module ensures all three call sites always agree on the
expected-dose denominator (spec requirement: "Shared denominator with insight
job") instead of drifting into inconsistent adherence math.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

# Textual frequency → doses per day conversion table.
# All keys are normalised to lowercase with leading/trailing whitespace stripped.
_FREQUENCY_DOSES_PER_DAY: dict[str, float] = {
    "daily": 1.0,
    "once daily": 1.0,
    "twice daily": 2.0,
    "twice a day": 2.0,
    "bid": 2.0,
    "three times daily": 3.0,
    "three times a day": 3.0,
    "tid": 3.0,
    "four times daily": 4.0,
    "four times a day": 4.0,
    "qid": 4.0,
    "weekly": 1 / 7,
    "once a week": 1 / 7,
    "once weekly": 1 / 7,
    "every other day": 0.5,
    "as needed": 1.0,  # fallback: assume once daily
    "prn": 1.0,
}


def frequency_to_doses_per_day(frequency: str) -> float:
    """Convert a textual frequency to doses per day.

    Normalises *frequency* to lowercase and strips surrounding whitespace
    before lookup.  Returns ``1.0`` (once daily) for any unrecognised value
    so callers always receive a positive denominator.

    Parameters
    ----------
    frequency:
        Human-readable frequency string as stored in the medication fact
        metadata (e.g. ``"twice daily"``, ``"bid"``).

    Returns
    -------
    float
        Doses per day (always > 0).
    """
    normalized = frequency.strip().lower()
    return _FREQUENCY_DOSES_PER_DAY.get(normalized, 1.0)


def expected_dose_count(
    *,
    doses_per_day: float,
    window_start: datetime,
    window_end: datetime,
    medication_created_at: datetime | None = None,
) -> int:
    """Return the expected dose count over ``[window_start, window_end]``.

    The effective window start is capped at ``medication_created_at`` when the
    medication is younger than the requested window — otherwise a medication
    created 3 days ago would be scored against a full 30-day expectation and
    read as ~10% adherent no matter how faithfully every dose was logged.

    Parameters
    ----------
    doses_per_day:
        From ``frequency_to_doses_per_day``.
    window_start, window_end:
        The requested lookback window.
    medication_created_at:
        When the medication fact was created, if known. ``None`` skips the cap.

    Returns
    -------
    int
        Expected dose count, always >= 0.
    """
    effective_start = window_start
    if medication_created_at is not None and medication_created_at > effective_start:
        effective_start = medication_created_at
    window_days = max((window_end - effective_start).total_seconds() / 86400, 0.0)
    return round(doses_per_day * window_days)


# ---------------------------------------------------------------------------
# Due-dose calculation (daily briefing missed/due-later split)
# ---------------------------------------------------------------------------

_AS_NEEDED_MARKERS = ("as needed", "prn", "when needed", "if needed")

# Frequencies whose doses fall on a weekday the schedule must name, or whose
# phase cannot be derived from frequency text alone.  Both are derived from
# ``_FREQUENCY_DOSES_PER_DAY`` (one-dose-a-week entries, and the other sub-daily
# entries) so adherence and ``dose_status_today`` classify a frequency alike.
_WEEKLY_FREQUENCIES = frozenset(
    key for key, doses in _FREQUENCY_DOSES_PER_DAY.items() if doses == 1 / 7
)
_PHASE_UNKNOWN_FREQUENCIES = frozenset(
    key for key, doses in _FREQUENCY_DOSES_PER_DAY.items() if doses < 1 and doses != 1 / 7
)

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}  # fmt: skip

# Named day parts, resolved to the clock time they are treated as due.
_DAY_PART_TIMES: dict[str, time] = {
    "morning": time(8, 0),
    "noon": time(12, 0),
    "midday": time(12, 0),
    "afternoon": time(15, 0),
    "evening": time(18, 0),
    "night": time(21, 0),
    "bedtime": time(21, 0),
}

_CLOCK_RE = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")


@dataclass(frozen=True)
class DoseStatus:
    """Doses of one medication that are overdue vs still to come today."""

    missed: int = 0
    due_later: int = 0


def is_as_needed(frequency: str | None) -> bool:
    """True for as-needed (PRN) frequencies, which have no scheduled doses."""
    normalized = (frequency or "").strip().lower()
    return any(marker in normalized for marker in _AS_NEEDED_MARKERS)


def _parse_dose_time(text: str) -> time | None:
    """Parse ``"08:00"``, ``"8am"``, ``"6:30 pm"`` or a day-part word; else None."""
    token = text.strip().lower()
    if token in _DAY_PART_TIMES:
        return _DAY_PART_TIMES[token]
    match = _CLOCK_RE.match(token)
    if match is None:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if meridiem is None and match.group(2) is None:
        return None  # a bare number is ambiguous
    if meridiem is not None:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def dose_status_today(
    *,
    frequency: str | None,
    schedule: list[str] | None,
    now_local: datetime,
    doses_taken: int,
) -> DoseStatus:
    """Classify today's doses of one medication as missed or due later.

    ``now_local`` is the owner-local current time; ``doses_taken`` the non-skipped
    doses already logged today.  Conservative by design: anything whose due time
    cannot be derived is never reported as a *scheduled-and-late* dose beyond what
    the data supports.

    - As-needed (PRN) medications have no scheduled doses: ``DoseStatus()``.
    - ``weekly`` frequencies are due only on a weekday named in *schedule*; with no
      weekday named (or an unrecognised/phase-less frequency such as ``every other
      day``) nothing is reported.
    - Daily-type frequencies (``daily``, ``twice daily``, ``bid``, ...) expect
      ``doses_per_day`` doses.  Times come from *schedule* (``"08:00"``, ``"8am"``,
      ``"evening"``) or from a day-part word in *frequency*.  A dose whose time has
      passed and has not been taken is missed; a later one is due later.
    - A dose with no known time keeps the legacy behaviour and counts as missed if
      untaken (the briefing cannot tell it is not yet due).
    """
    normalized = (frequency or "").strip().lower()
    if not normalized or is_as_needed(normalized) or normalized in _PHASE_UNKNOWN_FREQUENCIES:
        return DoseStatus()

    entries = [str(s) for s in (schedule or [])]
    if normalized in _WEEKLY_FREQUENCIES:
        days = {_WEEKDAYS[e.strip().lower()] for e in entries if e.strip().lower() in _WEEKDAYS}
        if now_local.weekday() not in days:
            return DoseStatus()
        count = 1
    elif normalized in _FREQUENCY_DOSES_PER_DAY and _FREQUENCY_DOSES_PER_DAY[normalized] >= 1:
        count = round(_FREQUENCY_DOSES_PER_DAY[normalized])
    else:
        # Day-part daily frequencies like "every evening" / "at bedtime".
        part = next((w for w in _DAY_PART_TIMES if w in normalized.split()), None)
        if part is None and "bedtime" not in normalized:
            return DoseStatus()
        entries = entries or [part or "bedtime"]
        count = 1

    times = sorted(t for t in (_parse_dose_time(e) for e in entries) if t is not None)[:count]
    now_time = now_local.time().replace(second=0, microsecond=0)
    undated = count - len(times)
    due_now = sum(1 for t in times if t <= now_time) + undated
    later = len(times) - sum(1 for t in times if t <= now_time)
    taken = max(doses_taken, 0)
    missed = max(due_now - taken, 0)
    later = max(later - max(taken - due_now, 0), 0)
    return DoseStatus(missed=missed, due_later=later)
