"""Unit tests for the shared medication frequency helper.

The ``butlers.tools.health._medication_utils`` module is the single source of
truth for converting a textual dosing-frequency string to a doses-per-day
float.  It is imported by both the adherence route and the insight-scan job so
both callers agree on the expected-dose denominator (spec: "Shared denominator
with insight job", bu-ve69q).
"""

from __future__ import annotations

import pytest

from butlers.tools.health._medication_utils import (
    _FREQUENCY_DOSES_PER_DAY,
    frequency_to_doses_per_day,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# frequency_to_doses_per_day — table coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frequency, expected",
    [
        ("daily", 1.0),
        ("once daily", 1.0),
        ("twice daily", 2.0),
        ("twice a day", 2.0),
        ("bid", 2.0),
        ("three times daily", 3.0),
        ("three times a day", 3.0),
        ("tid", 3.0),
        ("four times daily", 4.0),
        ("four times a day", 4.0),
        ("qid", 4.0),
        ("weekly", 1 / 7),
        ("once a week", 1 / 7),
        ("every other day", 0.5),
        ("as needed", 1.0),
        ("prn", 1.0),
    ],
)
def test_known_frequencies(frequency: str, expected: float) -> None:
    """Every entry in the lookup table returns the documented value."""
    assert frequency_to_doses_per_day(frequency) == pytest.approx(expected)


def test_unknown_frequency_defaults_to_once_daily() -> None:
    """Unrecognised frequency strings default to 1.0 (once daily)."""
    assert frequency_to_doses_per_day("monthly") == 1.0
    assert frequency_to_doses_per_day("") == 1.0
    assert frequency_to_doses_per_day("take with food") == 1.0


def test_normalises_case_and_whitespace() -> None:
    """Input is normalised to lowercase with surrounding whitespace stripped."""
    assert frequency_to_doses_per_day("  DAILY  ") == 1.0
    assert frequency_to_doses_per_day("Twice Daily") == 2.0
    assert frequency_to_doses_per_day("BID") == 2.0


def test_return_value_always_positive() -> None:
    """Result is always > 0 so callers never divide by zero."""
    for freq in list(_FREQUENCY_DOSES_PER_DAY) + ["unknown", "", "   "]:
        assert frequency_to_doses_per_day(freq) > 0


# ---------------------------------------------------------------------------
# Denominator parity with insight-scan job (spec: "Shared denominator")
# ---------------------------------------------------------------------------


def test_shared_helper_parity_with_health_jobs() -> None:
    """health_jobs._frequency_to_doses_per_day and frequency_to_doses_per_day
    are the SAME function (imported alias), so they always agree.
    """
    from butlers.jobs._roster_loader import load_roster_jobs

    health_jobs = load_roster_jobs("health")
    job_fn = health_jobs._frequency_to_doses_per_day

    test_pairs: list[tuple[str, int]] = [
        ("daily", 30),
        ("twice daily", 7),
        ("weekly", 30),
        ("every other day", 14),
        ("prn", 30),
        ("unknown", 10),
    ]
    for freq, days in test_pairs:
        route_result = round(frequency_to_doses_per_day(freq) * days)
        job_result = round(job_fn(freq) * days)
        assert route_result == job_result, (
            f"Parity failure for freq={freq!r}, days={days}: route={route_result}, job={job_result}"
        )
