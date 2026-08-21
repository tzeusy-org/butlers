"""Fixed-clock tests for the schedule cadence estimator (bu-6jv4m.2).

``_estimate_runs_per_month`` is the single source of the monthly cadence that
``/api/spend/by-schedule`` projects cost from. Every test here pins an explicit
reference instant -- a cadence test that only passes in some weeks is not a
test, and that is precisely the defect being fixed: the previous estimator
counted occurrences in the next 24 hours, so a weekly cron read as 1 run/day on
Mondays and 0 on every other day, which the API then multiplied by a hardcoded
30.

Pure unit tests: no database, no Docker, no container fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from butlers.core.sessions import (
    AVERAGE_MONTH_DAYS,
    CADENCE_BASIS_DESCRIPTION,
    _estimate_runs_per_month,
)

# Four fixed clocks spread across the week, the month boundary, and a leap year.
# The estimate must be materially the same at every one of them.
FIXED_CLOCKS = [
    datetime(2026, 3, 2, 0, 0, tzinfo=UTC),  # a Monday, start of a month-ish
    datetime(2026, 3, 5, 13, 47, tzinfo=UTC),  # a Thursday, mid-afternoon
    datetime(2026, 8, 22, 23, 59, tzinfo=UTC),  # a Saturday, last minute of the day
    datetime(2024, 2, 29, 6, 15, tzinfo=UTC),  # a leap day
]


def test_average_month_basis_is_the_gregorian_mean():
    """The documented basis is the mean Gregorian calendar month, not 30."""
    assert AVERAGE_MONTH_DAYS == pytest.approx(365.2425 / 12)
    assert AVERAGE_MONTH_DAYS != 30
    assert "30.436875" in CADENCE_BASIS_DESCRIPTION


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_weekly_cron_is_about_four_point_three_runs_per_month(reference: datetime):
    """The live regression: ``0 9 * * 1`` is ~4.3 runs/month, never 30.

    A weekly schedule fires 52.1775 times a year, i.e. 4.348 times in an average
    month. The old path reported ``runs_per_day=1`` and the router multiplied by
    30 -- a sevenfold overstatement of the projected monthly cost.
    """
    runs = _estimate_runs_per_month("0 9 * * 1", reference=reference)
    assert runs == pytest.approx(4.348, abs=0.05)
    assert runs < 5


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_daily_cron_matches_the_average_month_length(reference: datetime):
    runs = _estimate_runs_per_month("0 8 * * *", reference=reference)
    assert runs == pytest.approx(AVERAGE_MONTH_DAYS, abs=0.05)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_monthly_cron_is_one_run_per_month(reference: datetime):
    runs = _estimate_runs_per_month("0 9 1 * *", reference=reference)
    assert runs == pytest.approx(1.0, abs=0.02)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_irregular_multi_day_cron_uses_the_same_basis(reference: datetime):
    """Mon/Wed/Fri is three weekly firings: 3 * 30.436875 / 7 ~= 13.04."""
    runs = _estimate_runs_per_month("0 9 * * 1,3,5", reference=reference)
    assert runs == pytest.approx(3 * AVERAGE_MONTH_DAYS / 7, abs=0.15)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_yearly_cron_is_a_twelfth_of_a_run_per_month(reference: datetime):
    """An irregular low-frequency schedule must not round to zero or to one."""
    runs = _estimate_runs_per_month("0 9 1 1 *", reference=reference)
    assert runs == pytest.approx(1 / 12, abs=0.01)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_high_frequency_cron_stays_accurate_under_the_occurrence_cap(reference: datetime):
    """Hourly and per-minute crons exit on the occurrence cap, not the horizon.

    The cap bounds the cost of estimating a pathological expression; the rate is
    then read off the span actually sampled, so the answer stays correct.
    """
    hourly = _estimate_runs_per_month("0 * * * *", reference=reference)
    assert hourly == pytest.approx(24 * AVERAGE_MONTH_DAYS, rel=0.01)

    per_minute = _estimate_runs_per_month("* * * * *", reference=reference)
    assert per_minute == pytest.approx(1440 * AVERAGE_MONTH_DAYS, rel=0.01)


def test_estimate_is_independent_of_when_the_owner_looks():
    """AC5: the same cron must project the same cadence at any reference instant.

    The default estimate is anchored to a fixed instant precisely so a forecast
    never changes because the dashboard was opened on a different day.
    """
    for cron in ("0 9 * * 1", "0 8 * * *", "0 9 1 * *", "0 9 * * 1,3,5"):
        estimates = [_estimate_runs_per_month(cron, reference=ref) for ref in FIXED_CLOCKS]
        spread = max(estimates) - min(estimates)
        assert spread <= 0.05 * max(estimates), f"{cron} drifts with the clock: {estimates}"

    # And the zero-argument default -- what production calls -- is stable.
    assert _estimate_runs_per_month("0 9 * * 1") == _estimate_runs_per_month("0 9 * * 1")


@pytest.mark.parametrize("cron", ["", "not a cron", "0 9 * *", "99 99 * * *"])
def test_invalid_cron_projects_no_runs(cron: str):
    """An unparseable expression must project zero, never a fabricated cadence."""
    assert _estimate_runs_per_month(cron) == 0.0


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_seasonal_high_frequency_cron_is_not_measured_over_its_dense_season(reference: datetime):
    """`0 * * 1 *` -- hourly, but only in January -- is ~62 runs/month, not ~81.

    This is the case the occurrence cap gets wrong on its own. The expression
    fires 744 times each January, so the cap is exhausted partway through a
    third January; the raw span sampled is bounded by a dense stretch and ends
    inside one, and dividing by it reports about 81 runs a month against a true
    744/12 = 62 -- roughly 31% high, and entirely plausible-looking on a page
    whose whole purpose is forecast honesty. Truncating the window to whole
    years before recounting puts every January and every empty month back in
    proportion.
    """
    runs = _estimate_runs_per_month("0 * * 1 *", reference=reference)
    assert runs == pytest.approx(744 / 12, rel=0.01)
    assert runs < 70  # the untruncated-window answer was ~81


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_seasonal_cron_with_two_active_months_scales_with_the_season(reference: datetime):
    """January + July hourly is exactly twice the January-only cadence.

    Both months have 31 days, so the season is 2 x 744 firings a year.
    """
    runs = _estimate_runs_per_month("0 * * 1,7 *", reference=reference)
    assert runs == pytest.approx(2 * 744 / 12, rel=0.01)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_weekly_seasonal_high_frequency_cron_uses_a_whole_week(reference: datetime):
    """A per-minute Monday cron exhausts the cap in days, not years.

    Whole years do not fit, so the window snaps to whole weeks instead -- a
    truncation to whole days would end mid-Monday and over-report by about 20%.
    """
    runs = _estimate_runs_per_month("* * * * 1", reference=reference)
    assert runs == pytest.approx(1440 * AVERAGE_MONTH_DAYS / 7, rel=0.01)
