"""Pure unit tests for butlers.core.seasonal (no DB required).

Tests:
- validate_month_day: valid dates accepted, invalid dates rejected
- _is_date_in_range: same-year, cross-year, and boundary conditions
- SEASONAL_PRESETS: spot-check preset definitions and year-boundary correctness
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
]


# ---------------------------------------------------------------------------
# validate_month_day
# ---------------------------------------------------------------------------


class TestValidateMonthDay:
    """Unit tests for validate_month_day."""

    def test_valid_dates(self):
        """Common valid month/day combinations are accepted."""
        from butlers.core.seasonal import validate_month_day

        validate_month_day(1, 1)
        validate_month_day(1, 31)
        validate_month_day(2, 28)
        validate_month_day(4, 30)
        validate_month_day(12, 31)

    def test_rejects_feb_30(self):
        """February 30 is not a valid date."""
        from butlers.core.seasonal import validate_month_day

        with pytest.raises(ValueError, match="February"):
            validate_month_day(2, 30)

    def test_rejects_feb_29(self):
        """February 29 is rejected (use stable annual boundaries)."""
        from butlers.core.seasonal import validate_month_day

        with pytest.raises(ValueError, match="February"):
            validate_month_day(2, 29)

    def test_rejects_apr_31(self):
        """April 31 is not a valid date."""
        from butlers.core.seasonal import validate_month_day

        with pytest.raises(ValueError, match="April"):
            validate_month_day(4, 31)

    def test_rejects_invalid_month(self):
        """Month 0 and 13 are rejected."""
        from butlers.core.seasonal import validate_month_day

        with pytest.raises(ValueError, match="month"):
            validate_month_day(0, 1)
        with pytest.raises(ValueError, match="month"):
            validate_month_day(13, 1)

    def test_rejects_day_zero(self):
        """Day 0 is rejected."""
        from butlers.core.seasonal import validate_month_day

        with pytest.raises(ValueError, match="day"):
            validate_month_day(3, 0)

    def test_rejects_negative_day(self):
        """Negative days are rejected."""
        from butlers.core.seasonal import validate_month_day

        with pytest.raises(ValueError, match="day"):
            validate_month_day(5, -1)

    def test_nov_30_valid(self):
        """November 30 (last day of November) is valid."""
        from butlers.core.seasonal import validate_month_day

        validate_month_day(11, 30)  # Should not raise

    def test_nov_31_invalid(self):
        """November 31 is invalid."""
        from butlers.core.seasonal import validate_month_day

        with pytest.raises(ValueError, match="November"):
            validate_month_day(11, 31)


# ---------------------------------------------------------------------------
# _is_date_in_range
# ---------------------------------------------------------------------------


class TestIsDateInRange:
    """Unit tests for _is_date_in_range."""

    # Same-year periods
    def test_same_year_active(self):
        """Period start <= today <= end (same year) is active."""
        from butlers.core.seasonal import _is_date_in_range

        assert _is_date_in_range(3, 15, 1, 1, 4, 15)  # tax season, mid-March

    def test_same_year_inactive(self):
        """Today outside the same-year period is inactive."""
        from butlers.core.seasonal import _is_date_in_range

        assert not _is_date_in_range(6, 1, 1, 1, 4, 15)  # June is outside

    def test_same_year_start_boundary(self):
        """Period start date is inclusive."""
        from butlers.core.seasonal import _is_date_in_range

        assert _is_date_in_range(1, 1, 1, 1, 4, 15)

    def test_same_year_end_boundary(self):
        """Period end date is inclusive."""
        from butlers.core.seasonal import _is_date_in_range

        assert _is_date_in_range(4, 15, 1, 1, 4, 15)

    def test_same_year_before_start(self):
        """Date before start is inactive."""
        from butlers.core.seasonal import _is_date_in_range

        # Dec 31 is before Jan 1 (which is the start)
        # In same-year logic this means Dec 31 > Apr 15 (end), so inactive.
        # But here start=Jan, end=Apr; Dec is after end and before start in
        # same-year ordering: Dec > Apr, so inactive.
        assert not _is_date_in_range(12, 31, 1, 1, 4, 15)

    # Cross-year periods (year-boundary wrapping)
    def test_cross_year_active_after_start(self):
        """Cross-year period: today >= start is active."""
        from butlers.core.seasonal import _is_date_in_range

        # Dec 20 is inside Nov 15 – Jan 10
        assert _is_date_in_range(12, 20, 11, 15, 1, 10)

    def test_cross_year_active_before_end(self):
        """Cross-year period: today <= end is active."""
        from butlers.core.seasonal import _is_date_in_range

        # Jan 5 is inside Nov 15 – Jan 10
        assert _is_date_in_range(1, 5, 11, 15, 1, 10)

    def test_cross_year_inactive_middle(self):
        """Cross-year period: mid-year is outside the wrapped range."""
        from butlers.core.seasonal import _is_date_in_range

        # June is outside Nov 15 – Jan 10
        assert not _is_date_in_range(6, 15, 11, 15, 1, 10)

    def test_cross_year_start_boundary(self):
        """Cross-year period: start boundary is active."""
        from butlers.core.seasonal import _is_date_in_range

        assert _is_date_in_range(11, 15, 11, 15, 1, 10)

    def test_cross_year_end_boundary(self):
        """Cross-year period: end boundary is active."""
        from butlers.core.seasonal import _is_date_in_range

        assert _is_date_in_range(1, 10, 11, 15, 1, 10)

    def test_cross_year_inactive_just_after_end(self):
        """Cross-year period: one day past the end is inactive."""
        from butlers.core.seasonal import _is_date_in_range

        # Jan 11 (one past end=Jan 10) is outside Nov 15 – Jan 10
        assert not _is_date_in_range(1, 11, 11, 15, 1, 10)

    def test_cross_year_inactive_just_before_start(self):
        """Cross-year period: one day before start is inactive."""
        from butlers.core.seasonal import _is_date_in_range

        # Nov 14 (one before start=Nov 15) is outside Nov 15 – Jan 10
        assert not _is_date_in_range(11, 14, 11, 15, 1, 10)


# ---------------------------------------------------------------------------
# SEASONAL_PRESETS
# ---------------------------------------------------------------------------


class TestSeasonalPresets:
    """Spot-check built-in preset definitions."""

    def test_all_presets_present(self):
        """All five presets are defined."""
        from butlers.core.seasonal import SEASONAL_PRESETS

        expected = {
            "us-tax-season",
            "year-end-holidays",
            "back-to-school",
            "spring-semester",
            "fall-semester",
        }
        assert set(SEASONAL_PRESETS.keys()) == expected

    def test_us_tax_season_dates(self):
        """us-tax-season covers Jan 1 – Apr 15."""
        from butlers.core.seasonal import SEASONAL_PRESETS

        p = SEASONAL_PRESETS["us-tax-season"]
        assert p["start_month"] == 1
        assert p["start_day"] == 1
        assert p["end_month"] == 4
        assert p["end_day"] == 15
        assert p["period_type"] == "fiscal"

    def test_year_end_holidays_wraps_year(self):
        """year-end-holidays wraps the year boundary (Dec 15 – Jan 5)."""
        from butlers.core.seasonal import SEASONAL_PRESETS, _is_date_in_range

        p = SEASONAL_PRESETS["year-end-holidays"]
        assert p["start_month"] == 12
        assert p["start_day"] == 15
        assert p["end_month"] == 1
        assert p["end_day"] == 5
        # Dec 25 is inside
        assert _is_date_in_range(
            12, 25, p["start_month"], p["start_day"], p["end_month"], p["end_day"]
        )
        # March is outside
        assert not _is_date_in_range(
            3, 1, p["start_month"], p["start_day"], p["end_month"], p["end_day"]
        )

    def test_back_to_school_dates(self):
        """back-to-school covers Aug 1 – Sep 15."""
        from butlers.core.seasonal import SEASONAL_PRESETS

        p = SEASONAL_PRESETS["back-to-school"]
        assert p["start_month"] == 8
        assert p["start_day"] == 1
        assert p["end_month"] == 9
        assert p["end_day"] == 15

    def test_spring_semester_dates(self):
        """spring-semester covers Jan 15 – May 15."""
        from butlers.core.seasonal import SEASONAL_PRESETS

        p = SEASONAL_PRESETS["spring-semester"]
        assert p["start_month"] == 1
        assert p["start_day"] == 15
        assert p["end_month"] == 5
        assert p["end_day"] == 15

    def test_fall_semester_dates(self):
        """fall-semester covers Aug 25 – Dec 15."""
        from butlers.core.seasonal import SEASONAL_PRESETS

        p = SEASONAL_PRESETS["fall-semester"]
        assert p["start_month"] == 8
        assert p["start_day"] == 25
        assert p["end_month"] == 12
        assert p["end_day"] == 15

    def test_all_presets_have_metadata_with_context_hint(self):
        """All presets provide a non-empty context_hint in metadata."""
        from butlers.core.seasonal import SEASONAL_PRESETS

        for name, preset in SEASONAL_PRESETS.items():
            assert "metadata" in preset, f"Preset {name!r} missing metadata"
            meta = preset["metadata"]
            assert "context_hint" in meta, f"Preset {name!r} missing context_hint"
            assert meta["context_hint"].strip(), f"Preset {name!r} has empty context_hint"

    def test_all_presets_have_valid_dates(self):
        """All preset dates pass validate_month_day."""
        from butlers.core.seasonal import SEASONAL_PRESETS, validate_month_day

        for name, preset in SEASONAL_PRESETS.items():
            validate_month_day(preset["start_month"], preset["start_day"])
            validate_month_day(preset["end_month"], preset["end_day"])
