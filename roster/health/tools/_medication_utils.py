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

from datetime import datetime

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
