"""Unit tests for butlers.tools.finance.budgets — spending_trends and spending_forecast.

These tests use AsyncMock to simulate the asyncpg pool so they run without
a real database (no Docker required). All tests are marked unit.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.asyncio(loop_scope="session"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(data: dict) -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg Record."""
    m = MagicMock()
    m.__getitem__ = MagicMock(side_effect=lambda key: data[key])
    return m


def _make_pool(
    *,
    fetchrow_side_effect=None,
    fetchrow_return=None,
    fetch_return=None,
    fetch_side_effect=None,
) -> AsyncMock:
    """Build a mock asyncpg pool with configurable fetchrow/fetch returns."""
    pool = AsyncMock()
    if fetchrow_side_effect is not None:
        pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effect)
    elif fetchrow_return is not None:
        pool.fetchrow = AsyncMock(return_value=fetchrow_return)
    else:
        pool.fetchrow = AsyncMock(return_value=_make_row({"total": Decimal("0")}))

    if fetch_side_effect is not None:
        pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    elif fetch_return is not None:
        pool.fetch = AsyncMock(return_value=fetch_return)
    else:
        pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# spending_trends — helpers
# ---------------------------------------------------------------------------

TODAY_MID_MONTH = date(2026, 3, 15)
TODAY_FIRST = date(2026, 3, 1)


def _mom_pool(monthly_spends: list[Decimal]) -> AsyncMock:
    """Build pool whose fetchrow returns monthly totals in sequence."""
    pool = AsyncMock()
    responses = [_make_row({"total": amt}) for amt in monthly_spends]
    pool.fetchrow = AsyncMock(side_effect=responses)
    pool.fetch = AsyncMock(return_value=[])
    return pool


# ---------------------------------------------------------------------------
# spending_trends — MoM
# ---------------------------------------------------------------------------


class TestSpendingTrendsMoM:
    async def test_mom_returns_correct_shape(self):
        """MoM response includes comparison, category, and periods list."""
        from butlers.tools.finance.budgets import spending_trends

        # months=3: provide 3 monthly totals (with non-zero values for ≥2 months)
        pool = _mom_pool([Decimal("100"), Decimal("150"), Decimal("200")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        assert result["comparison"] == "mom"
        assert result["category"] is None
        assert len(result["periods"]) == 3

    async def test_mom_first_period_has_no_change(self):
        """First period in MoM has no change_amount/change_pct and direction=flat."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("120")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        first = result["periods"][0]
        assert first["change_amount"] is None
        assert first["change_pct"] is None
        assert first["direction"] == "flat"

    async def test_mom_direction_up(self):
        """Spending increase > 5% produces direction='up'."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("120")])  # +20%
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        second = result["periods"][1]
        assert second["direction"] == "up"
        assert second["change_amount"] == "20"
        assert Decimal(second["change_pct"]) == Decimal("20.00")

    async def test_mom_direction_down(self):
        """Spending decrease > 5% produces direction='down'."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("200"), Decimal("100")])  # -50%
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        second = result["periods"][1]
        assert second["direction"] == "down"

    async def test_mom_direction_flat_within_5pct(self):
        """Change < 5% (absolute) produces direction='flat'."""
        from butlers.tools.finance.budgets import spending_trends

        # 100 -> 103 = +3%, within flat threshold
        pool = _mom_pool([Decimal("100"), Decimal("103")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        second = result["periods"][1]
        assert second["direction"] == "flat"

    async def test_mom_period_labels(self):
        """Period labels are YYYY-MM strings in ascending chronological order."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("200"), Decimal("300")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        periods = [p["period"] for p in result["periods"]]
        # Should be in chronological order
        assert periods == sorted(periods)
        # Most recent period should be the current month
        assert periods[-1] == "2026-03"

    async def test_mom_category_filter_passed(self):
        """Category filter is passed to the SQL query."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("50"), Decimal("60")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2, category="dining")

        assert result["category"] == "dining"
        # Verify category was included in SQL params (3rd arg in fetchrow calls)
        calls = pool.fetchrow.call_args_list
        for call in calls:
            args = call[0]
            assert "dining" in args, "category should be passed as SQL parameter"

    async def test_mom_insufficient_data_only_zeros(self):
        """If all months have zero spend, returns insufficient_data status."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("0"), Decimal("0"), Decimal("0")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        assert result["status"] == "insufficient_data"
        assert "message" in result

    async def test_mom_insufficient_data_only_one_nonzero(self):
        """If only 1 month has non-zero spend, returns insufficient_data."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("0"), Decimal("0"), Decimal("100")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        assert result["status"] == "insufficient_data"

    async def test_mom_min_months_clamped_to_2(self):
        """months=1 is clamped to 2 to allow at least one comparison."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("120")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=1)

        assert "periods" in result
        assert len(result["periods"]) == 2

    async def test_mom_zero_prior_month_change_pct_is_none(self):
        """When the immediately-prior month spend is zero, change_pct is None (no div by zero).

        Uses 3 months [100, 0, 100] so there are 2 non-zero months (sufficient data).
        The second→third period has prior=0, so change_pct must be None.
        """
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("0"), Decimal("100")])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        # periods[2] has prior = 0 → change_pct is None
        third = result["periods"][2]
        assert third["change_pct"] is None


# ---------------------------------------------------------------------------
# spending_trends — YoY
# ---------------------------------------------------------------------------


class TestSpendingTrendsYoY:
    async def _yoy_pool(
        self, current_spend: Decimal, prior_spend: Decimal
    ) -> tuple[AsyncMock, dict]:
        """Build pool returning current then prior month spend via fetchrow."""
        pool = AsyncMock()
        responses = [
            _make_row({"total": current_spend}),
            _make_row({"total": prior_spend}),
        ]
        pool.fetchrow = AsyncMock(side_effect=responses)
        pool.fetch = AsyncMock(return_value=[])
        return pool, {}

    async def test_yoy_returns_correct_shape(self):
        """YoY response includes all required top-level fields."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("400"), Decimal("380"))
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["comparison"] == "yoy"
        assert result["current_period"] == "2026-03"
        assert result["prior_period"] == "2025-03"
        assert "current_spend" in result
        assert "prior_spend" in result
        assert "change_amount" in result
        assert "change_pct" in result
        assert "direction" in result

    async def test_yoy_direction_up(self):
        """Current spend higher than prior year produces direction='up'."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("500"), Decimal("400"))  # +25%
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["direction"] == "up"
        assert Decimal(result["change_pct"]) == Decimal("25.00")

    async def test_yoy_direction_down(self):
        """Current spend lower than prior year produces direction='down'."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("300"), Decimal("400"))  # -25%
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["direction"] == "down"

    async def test_yoy_insufficient_data_both_zero(self):
        """If both current and prior spend are zero, returns insufficient_data."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("0"), Decimal("0"))
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["status"] == "insufficient_data"

    async def test_yoy_zero_prior_spend_change_pct_none(self):
        """When prior year spend is zero, change_pct is None."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("200"), Decimal("0"))
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["change_pct"] is None

    async def test_yoy_category_filter(self):
        """Category filter is included in the response and SQL params."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("100"), Decimal("90"))
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy", category="groceries")

        assert result["category"] == "groceries"
        for call in pool.fetchrow.call_args_list:
            args = call[0]
            assert "groceries" in args

    async def test_yoy_period_labels_correct(self):
        """Prior period is exactly 12 months before current period."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("200"), Decimal("180"))
        with patch("butlers.tools.finance.budgets._today_utc", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["current_period"] == "2026-03"
        assert result["prior_period"] == "2025-03"


# ---------------------------------------------------------------------------
# spending_trends — invalid comparison
# ---------------------------------------------------------------------------


class TestSpendingTrendsValidation:
    async def test_invalid_comparison_raises_value_error(self):
        """Invalid comparison mode raises ValueError."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _make_pool()
        with pytest.raises(ValueError, match="comparison"):
            await spending_trends(pool, comparison="invalid")


# ---------------------------------------------------------------------------
# spending_forecast — helpers
# ---------------------------------------------------------------------------


def _forecast_pool(
    *,
    current_spend: Decimal,
    cat_rows: list[dict],
    hist_rows: list[dict] | None = None,
    budget_rows: list[dict] | None = None,
    prior_spend: Decimal | None = None,
    budgets_table_exists: bool = True,
) -> AsyncMock:
    """Build a pool mock for spending_forecast tests.

    fetchrow: sequence — [current_spend, (prior_spend if first-of-month)]
    fetch: sequence — [cat_rows, hist_rows, (budget_rows)]
    """
    import asyncpg

    pool = AsyncMock()

    # fetchrow sequence: total current month, maybe total prior month
    fetchrow_responses = [_make_row({"total": current_spend})]
    if prior_spend is not None:
        fetchrow_responses.append(_make_row({"total": prior_spend}))
    pool.fetchrow = AsyncMock(side_effect=fetchrow_responses)

    # fetch sequence: current cat rows, then hist rows
    _hist_rows = hist_rows or []
    _cat_rows = [_make_row(r) for r in cat_rows]
    _hist_rows_mocked = [_make_row(r) for r in _hist_rows]

    fetch_responses: list = [_cat_rows, _hist_rows_mocked]

    if not budgets_table_exists:
        # Raise UndefinedTableError to simulate missing budgets table
        async def _fetch_side_effect(sql, *args):
            if "budgets" in sql.lower():
                raise asyncpg.UndefinedTableError("relation does not exist")
            # Return cat rows first, hist rows second
            if not hasattr(_fetch_side_effect, "_count"):
                _fetch_side_effect._count = 0
            _fetch_side_effect._count += 1
            if _fetch_side_effect._count == 1:
                return _cat_rows
            return _hist_rows_mocked

        pool.fetch = AsyncMock(side_effect=_fetch_side_effect)
    else:
        _budget_rows = [_make_row(r) for r in (budget_rows or [])]
        fetch_responses.append(_budget_rows)
        pool.fetch = AsyncMock(side_effect=fetch_responses)

    return pool


# ---------------------------------------------------------------------------
# spending_forecast — linear projection
# ---------------------------------------------------------------------------


class TestSpendingForecastLinear:
    async def test_linear_projection_mid_month(self):
        """Mid-month forecast uses linear projection."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)  # day 15, 31-day month
        pool = _forecast_pool(
            current_spend=Decimal("300"),
            cat_rows=[{"category": "dining", "total": Decimal("150")}],
            hist_rows=[{"category": "dining", "avg_total": Decimal("280")}],
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["basis"] == "linear_projection"
        assert result["days_elapsed"] == 15
        assert result["days_remaining"] == 16
        assert result["days_in_month"] == 31
        assert result["current_spend"] == "300"
        # projected = (300 / 15) * 31 = 20 * 31 = 620
        assert Decimal(result["projected_total"]) == Decimal("620.00")
        assert Decimal(result["daily_average"]) == Decimal("20.00")

    async def test_as_of_date_is_today(self):
        """as_of_date matches the injected today date."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(current_spend=Decimal("100"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["as_of_date"] == "2026-03-15"

    async def test_categories_in_response(self):
        """Each category with current spend appears in the categories list."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(
            current_spend=Decimal("200"),
            cat_rows=[
                {"category": "groceries", "total": Decimal("100")},
                {"category": "dining", "total": Decimal("50")},
            ],
            hist_rows=[
                {"category": "groceries", "avg_total": Decimal("200")},
                {"category": "dining", "avg_total": Decimal("120")},
            ],
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        cats = {c["category"] for c in result["categories"]}
        assert "groceries" in cats
        assert "dining" in cats

    async def test_category_includes_historical_average(self):
        """historical_average is populated from the 6-month avg query."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(
            current_spend=Decimal("150"),
            cat_rows=[{"category": "groceries", "total": Decimal("100")}],
            hist_rows=[{"category": "groceries", "avg_total": Decimal("220")}],
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        groceries = next(c for c in result["categories"] if c["category"] == "groceries")
        assert groceries["historical_average"] == "220.00"

    async def test_category_with_budget(self):
        """When a budget exists for a category, budget fields appear in category entry."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)  # day 15, 31-day month, daily=100/15
        pool = _forecast_pool(
            current_spend=Decimal("150"),
            cat_rows=[{"category": "dining", "total": Decimal("150")}],
            hist_rows=[{"category": "dining", "avg_total": Decimal("280")}],
            budget_rows=[{"category": "dining", "amount": Decimal("300")}],
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        dining = next(c for c in result["categories"] if c["category"] == "dining")
        assert "budget_amount" in dining
        assert dining["budget_amount"] == "300"
        assert "projected_utilization_pct" in dining
        assert "on_track" in dining
        # projected = (150/15)*31 = 310; 310 > 300, so on_track = False
        assert dining["on_track"] is False

    async def test_category_on_track_when_under_budget(self):
        """on_track=True when projected_total <= budget_amount."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        # projected = (60/15)*31 = 124; budget = 200 → on_track
        pool = _forecast_pool(
            current_spend=Decimal("60"),
            cat_rows=[{"category": "dining", "total": Decimal("60")}],
            hist_rows=[],
            budget_rows=[{"category": "dining", "amount": Decimal("200")}],
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        dining = next(c for c in result["categories"] if c["category"] == "dining")
        assert dining["on_track"] is True

    async def test_category_without_budget_no_budget_fields(self):
        """Categories without a budget do not include budget_amount or on_track."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(
            current_spend=Decimal("100"),
            cat_rows=[{"category": "groceries", "total": Decimal("80")}],
            hist_rows=[],
            budget_rows=[],  # No budget for groceries
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        groceries = next(c for c in result["categories"] if c["category"] == "groceries")
        assert "budget_amount" not in groceries
        assert "on_track" not in groceries

    async def test_missing_budgets_table_graceful(self):
        """If finance.budgets table doesn't exist, forecast still succeeds without budget fields."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(
            current_spend=Decimal("120"),
            cat_rows=[{"category": "dining", "total": Decimal("60")}],
            hist_rows=[],
            budgets_table_exists=False,
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert "categories" in result
        assert result["basis"] == "linear_projection"
        # No budget fields since table was missing
        for cat in result["categories"]:
            assert "budget_amount" not in cat


# ---------------------------------------------------------------------------
# spending_forecast — first-of-month edge case
# ---------------------------------------------------------------------------


class TestSpendingForecastFirstOfMonth:
    async def test_first_of_month_uses_prior_month(self):
        """On the 1st of the month with no current spending, use prior month total."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 1)  # First of March
        # fetchrow: [current=0, prior=500]
        # fetch: [cat_rows=empty, hist_rows=empty, budget_rows=empty]
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _make_row({"total": Decimal("0")}),  # current month
                _make_row({"total": Decimal("500")}),  # prior month
            ]
        )
        pool.fetch = AsyncMock(side_effect=[[], [], []])

        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["basis"] == "prior_month"
        assert result["projected_total"] == "500.00"

    async def test_first_of_month_with_existing_spend_uses_linear(self):
        """On the 1st, if there is already current month spend, use linear projection."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 1)
        pool = _forecast_pool(
            current_spend=Decimal("50"),  # Some spend already on day 1
            cat_rows=[{"category": "dining", "total": Decimal("50")}],
            hist_rows=[],
        )
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        # Has current spend so uses linear (50 / 1) * 31 = 1550
        assert result["basis"] == "linear_projection"
        assert Decimal(result["projected_total"]) == Decimal("1550.00")

    async def test_first_of_month_basis_field_set(self):
        """basis='prior_month' is set in the response when using prior month fallback."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 1)
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(
            side_effect=[
                _make_row({"total": Decimal("0")}),
                _make_row({"total": Decimal("400")}),
            ]
        )
        pool.fetch = AsyncMock(side_effect=[[], [], []])

        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["basis"] == "prior_month"


# ---------------------------------------------------------------------------
# spending_forecast — response fields completeness
# ---------------------------------------------------------------------------


class TestSpendingForecastResponseShape:
    async def test_all_top_level_fields_present(self):
        """All required top-level fields are present in the response."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(current_spend=Decimal("300"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        required = {
            "as_of_date",
            "days_elapsed",
            "days_remaining",
            "days_in_month",
            "current_spend",
            "projected_total",
            "daily_average",
            "basis",
            "categories",
        }
        assert required <= set(result.keys())

    async def test_days_sum_equals_days_in_month(self):
        """days_elapsed + days_remaining == days_in_month."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(current_spend=Decimal("100"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["days_elapsed"] + result["days_remaining"] == result["days_in_month"]

    async def test_days_in_month_february_28(self):
        """February (non-leap year) has 28 days."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2025, 2, 14)
        pool = _forecast_pool(current_spend=Decimal("100"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["days_in_month"] == 28

    async def test_days_in_month_february_leap_year(self):
        """February (leap year) has 29 days."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2024, 2, 14)
        pool = _forecast_pool(current_spend=Decimal("100"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["days_in_month"] == 29

    async def test_empty_categories_when_no_spend(self):
        """When no transactions exist, categories list is empty."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(current_spend=Decimal("0"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today_utc", return_value=today):
            result = await spending_forecast(pool)

        assert result["categories"] == []
