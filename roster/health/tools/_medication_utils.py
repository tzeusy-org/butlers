"""Shared medication frequency utilities.

This module is the single source of truth for converting a textual dosing
frequency string into a doses-per-day float.  It is imported by:

- ``roster/health/api/router.py``  (GET /medications/{id}/adherence route)
- ``roster/health/jobs/health_jobs.py``  (insight-scan job: refill estimation
  and adherence-dip correlation)

Having one module ensures that the adherence route and the insight-scan job
always agree on the expected-dose denominator (spec requirement: "Shared
denominator with insight job").
"""

from __future__ import annotations

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
