"""Fixed-clock tests for the schedule cadence estimator (bu-6jv4m.2).

``_estimate_monthly_runs`` is the single source of the monthly cadence that
``/api/spend/by-schedule`` projects cost from. Every test here pins an explicit
reference instant -- a cadence test that only passes in some weeks is not a
test, and that is precisely the defect being fixed: the previous estimator
counted occurrences in the next 24 hours, so a weekly cron read as 1 run/day on
Mondays and 0 on every other day, which the API then multiplied by a hardcoded
30.

The estimator's correctness rests on one property: the counting window is a
whole number of the expression's *own* cycle. Several tests below exist only to
pin that, because measuring a seasonal expression over a slice of its season
produces a wrong number that looks entirely reasonable.

Pure unit tests: no database, no Docker, no container fixtures.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from butlers.core.sessions import (
    AVERAGE_MONTH_DAYS,
    CADENCE_BASIS_DESCRIPTION,
    _cadence_cycle_days,
    _estimate_monthly_runs,
)

# Four fixed clocks spread across the week, the month boundary, and a leap year.
# The estimate must be materially the same at every one of them.
FIXED_CLOCKS = [
    datetime(2026, 3, 2, 0, 0, tzinfo=UTC),  # a Monday, start of a month-ish
    datetime(2026, 3, 5, 13, 47, tzinfo=UTC),  # a Thursday, mid-afternoon
    datetime(2026, 8, 22, 23, 59, tzinfo=UTC),  # a Saturday, last minute of the day
    datetime(2024, 2, 29, 6, 15, tzinfo=UTC),  # a leap day
]

#: A per-minute expression fires 1440 times a day; scaled to an average month
#: that is 43,829.1 runs, and a seventh of that when confined to one weekday.
PER_MINUTE_MONTHLY = 1440 * AVERAGE_MONTH_DAYS
PER_MINUTE_WEEKDAY_MONTHLY = PER_MINUTE_MONTHLY / 7


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
    runs = _estimate_monthly_runs("0 9 * * 1", reference=reference)
    assert runs == pytest.approx(AVERAGE_MONTH_DAYS / 7, rel=1e-9)
    assert runs < 5


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_daily_cron_matches_the_average_month_length(reference: datetime):
    runs = _estimate_monthly_runs("0 8 * * *", reference=reference)
    assert runs == pytest.approx(AVERAGE_MONTH_DAYS, rel=1e-9)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_monthly_cron_is_one_run_per_month(reference: datetime):
    runs = _estimate_monthly_runs("0 9 1 * *", reference=reference)
    assert runs == pytest.approx(1.0, abs=0.01)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_irregular_multi_day_cron_uses_the_same_basis(reference: datetime):
    """Mon/Wed/Fri is three weekly firings: 3 * 30.436875 / 7 ~= 13.04."""
    runs = _estimate_monthly_runs("0 9 * * 1,3,5", reference=reference)
    assert runs == pytest.approx(3 * AVERAGE_MONTH_DAYS / 7, rel=1e-9)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_yearly_cron_is_a_twelfth_of_a_run_per_month(reference: datetime):
    """An irregular low-frequency schedule must not round to zero or to one."""
    runs = _estimate_monthly_runs("0 9 1 1 *", reference=reference)
    assert runs == pytest.approx(1 / 12, abs=0.01)


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_high_frequency_cron_stays_exact(reference: datetime):
    """Hourly and per-minute expressions repeat daily, so a day measures them exactly."""
    hourly = _estimate_monthly_runs("0 * * * *", reference=reference)
    assert hourly == pytest.approx(24 * AVERAGE_MONTH_DAYS, rel=1e-9)

    per_minute = _estimate_monthly_runs("* * * * *", reference=reference)
    assert per_minute == pytest.approx(PER_MINUTE_MONTHLY, rel=1e-9)


def test_estimate_is_independent_of_when_the_owner_looks():
    """AC5: the same cron must project the same cadence at any reference instant.

    The default estimate is anchored to a fixed instant precisely so a forecast
    never changes because the dashboard was opened on a different day.
    """
    for cron in ("0 9 * * 1", "0 8 * * *", "0 9 1 * *", "0 9 * * 1,3,5"):
        estimates = [_estimate_monthly_runs(cron, reference=ref) for ref in FIXED_CLOCKS]
        spread = max(estimates) - min(estimates)
        assert spread <= 0.05 * max(estimates), f"{cron} drifts with the clock: {estimates}"

    # And the zero-argument default -- what production calls -- is stable.
    assert _estimate_monthly_runs("0 9 * * 1") == _estimate_monthly_runs("0 9 * * 1")


@pytest.mark.parametrize("cron", ["", "not a cron", "0 9 * *", "99 99 * * *"])
def test_invalid_cron_projects_no_runs(cron: str):
    """An unparseable expression must project zero, never a fabricated cadence."""
    assert _estimate_monthly_runs(cron) == 0.0


# ---------------------------------------------------------------------------
# Whole-cycle measurement. Each expression below is measured over a window that
# is a whole number of its own period; taking the rate over anything else is
# what produces a plausible wrong number.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cron", "expected_cycle_days"),
    [
        ("* * * * *", 1),  # unrestricted: repeats every day
        ("0 * * * *", 1),
        ("@hourly", 1),
        ("0 9 * * 1", 7),  # day-of-week restricted: repeats every week
        ("* * * * 3", 7),
        ("0 9 1 * *", 365),  # day-of-month restricted: repeats annually
        ("0 * * 1 *", 365),  # month restricted: repeats annually
        ("0 9 * * 5#3", 365),  # third Friday: a monthly form, so annual
    ],
)
def test_cycle_length_is_taken_from_the_expression_not_from_the_sample(
    cron: str, expected_cycle_days: int
):
    """The window length is decided by which calendar fields the cron restricts.

    This is the load-bearing classification. Sizing the window from whatever
    happened to fit in a capped sample instead is exactly how a seasonal
    expression gets measured over its dense season alone.
    """
    assert _cadence_cycle_days(cron) == expected_cycle_days


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_seasonal_high_frequency_cron_is_not_measured_over_its_dense_season(reference: datetime):
    """``0 * * 1 *`` -- hourly, but only in January -- is ~62 runs/month, not ~81.

    Measured over a window that stops inside a January, this expression reports
    about 81 runs a month against a true 744/12 = 62: roughly 31% high, and
    entirely plausible-looking on a page whose whole purpose is forecast
    honesty. A whole calendar year puts every January and every empty month back
    in proportion.
    """
    runs = _estimate_monthly_runs("0 * * 1 *", reference=reference)
    assert runs == pytest.approx(744 / 12, rel=0.01)
    assert runs < 70  # the measured-over-a-dense-stretch answer was ~81


@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_seasonal_cron_with_two_active_months_scales_with_the_season(reference: datetime):
    """January + July hourly is exactly twice the January-only cadence.

    Both months have 31 days, so the season is 2 x 744 firings a year.
    """
    runs = _estimate_monthly_runs("0 * * 1,7 *", reference=reference)
    assert runs == pytest.approx(2 * 744 / 12, rel=0.01)


@pytest.mark.parametrize("weekday", [0, 1, 2, 3, 4, 5, 6])
@pytest.mark.parametrize("reference", FIXED_CLOCKS)
def test_per_minute_weekday_cron_is_measured_over_a_whole_week(weekday: int, reference: datetime):
    """Every weekday must give the same answer, including ones off the anchor.

    This is the case that discriminates a whole-week window from a whole-day
    one. A day-length window measures a weekday-restricted expression over a
    fraction of its period and under-reports badly -- ``* * * * 3`` reads about
    4873 against a true 6261 (22% low) and ``* * * * 0`` about 3374 (46% low) --
    while the anchor's own weekday happens to come out right either way. Pinning
    only the anchor weekday would leave the week-length window untested.
    """
    runs = _estimate_monthly_runs(f"* * * * {weekday}", reference=reference)
    assert runs == pytest.approx(PER_MINUTE_WEEKDAY_MONTHLY, rel=1e-9)


def test_per_minute_cron_terminates_cheaply():
    """``* * * * *`` must not try to enumerate four years of minutes.

    It repeats daily, so exactly one day (1440 firings) is counted. A naive
    horizon-length enumeration would walk ~2.1 million occurrences.
    """
    assert _cadence_cycle_days("* * * * *") == 1

    started = time.perf_counter()
    runs = _estimate_monthly_runs("* * * * *")
    elapsed = time.perf_counter() - started

    assert runs == pytest.approx(PER_MINUTE_MONTHLY, rel=1e-9)
    # Measured at ~90 ms; the ceiling is loose enough not to be a timing test,
    # tight enough to catch a regression to horizon-length enumeration.
    assert elapsed < 5.0, f"per-minute estimate took {elapsed:.1f}s"


@pytest.mark.parametrize("cron", ["* * * 1 *", "* * 1 * *"])
def test_cadence_too_dense_to_sample_reports_unknown_rather_than_guessing(cron: str):
    """An annual expression too dense to enumerate a whole year of projects nothing.

    ``* * * 1 *`` is per-minute confined to January: its period is a year, and a
    year of it is 44,640 firings. Rather than take the rate over the fraction it
    can afford to sample -- which reports 43,829 runs a month against a true
    3,720, eleven times high -- the estimator declines. Zero here means "cadence
    unknown", which the dashboard renders as not-forecastable rather than free.
    """
    assert _estimate_monthly_runs(cron) == 0.0


# ---------------------------------------------------------------------------
# The estimator serves every row of /api/spend/by-schedule from one helper, so
# a single pathological cron string must never take the whole response down.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cron", ["0 0 30 2 *", "0 0 31 4 *", "0 0 31 2 *"])
def test_impossible_but_well_formed_cron_returns_zero_instead_of_raising(cron: str):
    """30 February parses fine and never occurs; enumerating it raises in croniter.

    ``croniter.is_valid`` accepts these, so the validity guard does not catch
    them. Left unhandled, ``CroniterBadDateError`` propagates out of the row
    helper and fails the entire by-schedule response.
    """
    assert _estimate_monthly_runs(cron) == 0.0


def test_leap_day_cron_is_found_by_widening_past_a_single_year():
    """``0 9 29 2 *`` fires once every four years -- a single year would miss it.

    A year of sampling finds nothing, which must not be reported as "never
    fires"; the window widens to a whole leap cycle, where it fires once.
    """
    runs = _estimate_monthly_runs("0 9 29 2 *")
    assert runs == pytest.approx(0.25 / 12, rel=0.05)
    assert runs > 0


@pytest.mark.parametrize(
    "cron",
    [
        "@yearly",
        "@monthly",
        "@weekly",
        "@daily",
        "@hourly",
        "*/15 * * * *",
        "0 0 L * *",
        "0 9 * * 5#3",
        "0 0 1 */3 *",
        "30 2 29 2 *",
        "0 0 * * 7",
    ],
)
def test_estimator_never_raises_on_any_well_formed_expression(cron: str):
    """Whatever a schedule's cron says, the row it belongs to must still render."""
    runs = _estimate_monthly_runs(cron)
    assert runs >= 0.0
