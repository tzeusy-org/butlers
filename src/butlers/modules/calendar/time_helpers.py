"""Calendar time helpers implementation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from croniter import croniter as _croniter

from .constants import DEFAULT_SCHEDULED_TASK_DURATION_MINUTES


def _cron_next_occurrence(cron: str, *, now: datetime | None = None) -> datetime:
    """Return the next UTC datetime at which cron fires after *now*.

    Uses croniter (already a project dependency) to parse and advance the
    cron expression.  The result is always timezone-aware (UTC).
    """
    anchor = now if now is not None else datetime.now(UTC)
    return _croniter(cron, anchor).get_next(datetime).replace(tzinfo=UTC)


def _cron_occurrences_in_window(
    cron: str,
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int = DEFAULT_SCHEDULED_TASK_DURATION_MINUTES,
) -> list[tuple[datetime, datetime]]:
    """Return a list of (starts_at, ends_at) pairs for all cron occurrences in the window.

    Uses croniter to iterate occurrences.  The cron fires at or after window_start up to
    (and including) window_end.  Returns an empty list when the expression has no occurrences
    in the window.
    """
    results: list[tuple[datetime, datetime]] = []
    it = _croniter(cron, window_start - timedelta(seconds=1))
    while True:
        occ = it.get_next(datetime).replace(tzinfo=UTC)
        if occ > window_end:
            break
        ends = occ + timedelta(minutes=duration_minutes)
        results.append((occ, ends))
    return results


def _rrule_occurrences_in_window(
    recurrence_rule: str,
    dtstart: datetime,
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int = DEFAULT_SCHEDULED_TASK_DURATION_MINUTES,
) -> list[tuple[datetime, datetime]]:
    """Return (starts_at, ends_at) pairs for RRULE occurrences within [window_start, window_end].

    *recurrence_rule* is an RFC-5545 RRULE string (with or without the "RRULE:" prefix).
    *dtstart* is the reference start datetime used to anchor the rule.
    Only occurrences that fall within [window_start, window_end] are returned.
    """
    from dateutil.rrule import rrulestr

    normalized = recurrence_rule.strip()
    if not normalized.startswith("RRULE:"):
        normalized = f"RRULE:{normalized}"
    # dateutil expects DTSTART to be embedded or passed as dtstart= kwarg.
    if dtstart.tzinfo is not None:
        dtstart_utc = dtstart.astimezone(UTC)
    else:
        dtstart_utc = dtstart.replace(tzinfo=UTC)
    try:
        rule_set = rrulestr(normalized, dtstart=dtstart_utc, ignoretz=False)
    except Exception:
        return []

    results: list[tuple[datetime, datetime]] = []
    for occ in rule_set:
        if isinstance(occ, datetime):
            occ_utc = occ.astimezone(UTC) if occ.tzinfo else occ.replace(tzinfo=UTC)
        else:
            # date object — convert to midnight UTC
            occ_utc = datetime(occ.year, occ.month, occ.day, tzinfo=UTC)
        if occ_utc > window_end:
            break
        if occ_utc >= window_start:
            ends = occ_utc + timedelta(minutes=duration_minutes)
            results.append((occ_utc, ends))
    return results


def _format_ical_utc(dt: datetime) -> str:
    """Format *dt* as an RFC-5545 UTC value (``YYYYMMDDTHHMMSSZ``).

    Used to build EXDATE / RRULE UNTIL values for occurrence-scoped recurrence
    edits.  Naive datetimes are assumed to already be UTC.
    """
    dt_utc = dt.astimezone(UTC) if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return dt_utc.strftime("%Y%m%dT%H%M%SZ")


def _recurrence_lines_append_exdate(
    recurrence_lines: list[str], occurrence_start: datetime
) -> list[str]:
    """Append a UTC ``EXDATE`` for *occurrence_start* to a provider recurrence array.

    The series RRULE (and any RDATE entries) are preserved.  When a UTC EXDATE
    line already exists the new value is folded into it; an already-excluded
    occurrence is a no-op so the call is idempotent.
    """
    exdate_value = _format_ical_utc(occurrence_start)
    result = list(recurrence_lines)
    for index, line in enumerate(result):
        upper = line.upper()
        if upper.startswith("EXDATE") and "TZID" not in upper and ":" in line:
            prefix, _, values = line.partition(":")
            existing = [value for value in values.split(",") if value]
            if exdate_value in existing:
                return result
            existing.append(exdate_value)
            result[index] = f"{prefix}:{','.join(existing)}"
            return result
    result.append(f"EXDATE:{exdate_value}")
    return result


def _recurrence_lines_bound_until(recurrence_lines: list[str], boundary: datetime) -> list[str]:
    """Bound the series RRULE with an ``UNTIL`` at *boundary* (a this-and-following split).

    Any pre-existing ``UNTIL`` / ``COUNT`` on the RRULE is replaced (they are
    mutually exclusive with the new bound).  Non-RRULE lines are passed through.
    """
    until_value = _format_ical_utc(boundary)
    result: list[str] = []
    for line in recurrence_lines:
        if not line.upper().startswith("RRULE"):
            result.append(line)
            continue
        _, _, components = line.partition(":")
        parts = [
            part
            for part in components.split(";")
            if part and not part.upper().startswith(("UNTIL=", "COUNT="))
        ]
        parts.append(f"UNTIL={until_value}")
        result.append("RRULE:" + ";".join(parts))
    return result
