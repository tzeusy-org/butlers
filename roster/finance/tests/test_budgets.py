"""Unit tests for butlers.tools.finance.budgets — spending_trends and spending_forecast.

These tests use AsyncMock to simulate the asyncpg pool so they run without
a real database (no Docker required). All tests are marked unit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        assert result["comparison"] == "mom"
        assert result["category"] is None
        assert len(result["periods"]) == 3

    async def test_mom_first_period_has_no_change(self):
        """First period in MoM has no change_amount/change_pct and direction=flat."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("120")])
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        first = result["periods"][0]
        assert first["change_amount"] is None
        assert first["change_pct"] is None
        assert first["direction"] == "flat"

    async def test_mom_direction_up(self):
        """Spending increase > 5% produces direction='up'."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("120")])  # +20%
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        second = result["periods"][1]
        assert second["direction"] == "up"
        assert second["change_amount"] == "20"
        assert Decimal(second["change_pct"]) == Decimal("20.00")

    async def test_mom_direction_down(self):
        """Spending decrease > 5% produces direction='down'."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("200"), Decimal("100")])  # -50%
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        second = result["periods"][1]
        assert second["direction"] == "down"

    async def test_mom_direction_flat_within_5pct(self):
        """Change < 5% (absolute) produces direction='flat'."""
        from butlers.tools.finance.budgets import spending_trends

        # 100 -> 103 = +3%, within flat threshold
        pool = _mom_pool([Decimal("100"), Decimal("103")])
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=2)

        second = result["periods"][1]
        assert second["direction"] == "flat"

    async def test_mom_period_labels(self):
        """Period labels are YYYY-MM strings in ascending chronological order."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("200"), Decimal("300")])
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
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
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
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
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        assert result["status"] == "insufficient_data"
        assert "message" in result

    async def test_mom_insufficient_data_only_one_nonzero(self):
        """If only 1 month has non-zero spend, returns insufficient_data."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("0"), Decimal("0"), Decimal("100")])
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="mom", months=3)

        assert result["status"] == "insufficient_data"

    async def test_mom_min_months_clamped_to_2(self):
        """months=1 is clamped to 2 to allow at least one comparison."""
        from butlers.tools.finance.budgets import spending_trends

        pool = _mom_pool([Decimal("100"), Decimal("120")])
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
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
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
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
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
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
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["direction"] == "up"
        assert Decimal(result["change_pct"]) == Decimal("25.00")

    async def test_yoy_direction_down(self):
        """Current spend lower than prior year produces direction='down'."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("300"), Decimal("400"))  # -25%
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["direction"] == "down"

    async def test_yoy_insufficient_data_both_zero(self):
        """If both current and prior spend are zero, returns insufficient_data."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("0"), Decimal("0"))
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["status"] == "insufficient_data"

    async def test_yoy_zero_prior_spend_change_pct_none(self):
        """When prior year spend is zero, change_pct is None."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("200"), Decimal("0"))
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy")

        assert result["change_pct"] is None

    async def test_yoy_category_filter(self):
        """Category filter is included in the response and SQL params."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("100"), Decimal("90"))
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
            result = await spending_trends(pool, comparison="yoy", category="groceries")

        assert result["category"] == "groceries"
        for call in pool.fetchrow.call_args_list:
            args = call[0]
            assert "groceries" in args

    async def test_yoy_period_labels_correct(self):
        """Prior period is exactly 12 months before current period."""
        from butlers.tools.finance.budgets import spending_trends

        pool, _ = await self._yoy_pool(Decimal("200"), Decimal("180"))
        with patch("butlers.tools.finance.budgets._today", return_value=TODAY_MID_MONTH):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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

        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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

        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
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
        with patch("butlers.tools.finance.budgets._today", return_value=today):
            result = await spending_forecast(pool)

        assert result["days_elapsed"] + result["days_remaining"] == result["days_in_month"]

    async def test_days_in_month_february_28(self):
        """February (non-leap year) has 28 days."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2025, 2, 14)
        pool = _forecast_pool(current_spend=Decimal("100"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today", return_value=today):
            result = await spending_forecast(pool)

        assert result["days_in_month"] == 28

    async def test_days_in_month_february_leap_year(self):
        """February (leap year) has 29 days."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2024, 2, 14)
        pool = _forecast_pool(current_spend=Decimal("100"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today", return_value=today):
            result = await spending_forecast(pool)

        assert result["days_in_month"] == 29

    async def test_empty_categories_when_no_spend(self):
        """When no transactions exist, categories list is empty."""
        from butlers.tools.finance.budgets import spending_forecast

        today = date(2026, 3, 15)
        pool = _forecast_pool(current_spend=Decimal("0"), cat_rows=[])
        with patch("butlers.tools.finance.budgets._today", return_value=today):
            result = await spending_forecast(pool)

        assert result["categories"] == []


# =============================================================================
# Budget CRUD integration tests (require provisioned_postgres_pool fixture)
# These tests run against a real in-process Postgres instance and are NOT marked
# "unit" so they are excluded from the mock-only test run.
# =============================================================================

# ---------------------------------------------------------------------------
# Schema helpers (CRUD tests provision their own tables)
# ---------------------------------------------------------------------------

_CREATE_BUDGETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS budgets (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category         TEXT NOT NULL,
    period           TEXT NOT NULL
                         CHECK (period IN ('weekly', 'monthly', 'quarterly', 'yearly')),
    amount           NUMERIC(14, 2) NOT NULL,
    currency         CHAR(3) NOT NULL DEFAULT 'USD',
    warn_threshold   NUMERIC(5, 4) NOT NULL DEFAULT 0.8000,
    alert_threshold  NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
    is_active        BOOLEAN NOT NULL DEFAULT true,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# Includes deleted_at column per finance-transaction-schema spec contract
_CREATE_TRANSACTIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS transactions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id        UUID,
    source_message_id TEXT,
    posted_at         TIMESTAMPTZ NOT NULL,
    merchant          TEXT NOT NULL,
    description       TEXT,
    amount            NUMERIC(14, 2) NOT NULL,
    currency          CHAR(3) NOT NULL,
    direction         TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
    category          TEXT NOT NULL,
    payment_method    TEXT,
    receipt_url       TEXT,
    external_ref      TEXT,
    deleted_at        TIMESTAMPTZ,
    metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


@pytest.fixture
async def budget_pool(provisioned_postgres_pool):
    """Provision a fresh database with finance budgets and transactions tables."""
    async with provisioned_postgres_pool() as p:
        await p.execute(_CREATE_BUDGETS_TABLE_SQL)
        await p.execute(_CREATE_TRANSACTIONS_TABLE_SQL)
        yield p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _insert_tx(
    pool,
    *,
    merchant: str = "Test Merchant",
    amount: str = "50.00",
    currency: str = "USD",
    direction: str = "debit",
    category: str = "groceries",
    posted_at: datetime | None = None,
    deleted_at: datetime | None = None,
) -> None:
    """Insert a transaction row directly for test setup."""
    if posted_at is None:
        posted_at = datetime.now(UTC)
    await pool.execute(
        """
        INSERT INTO transactions
            (merchant, amount, currency, direction, category, posted_at, deleted_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        merchant,
        Decimal(amount),
        currency,
        direction,
        category,
        posted_at,
        deleted_at,
    )


def _now_mid_month() -> datetime:
    """Return a datetime firmly within the current calendar month."""
    today = datetime.now(UTC)
    return today.replace(day=max(1, today.day - 1), hour=12, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# budget_set tests
# ---------------------------------------------------------------------------


class TestBudgetSet:
    """Tests for budget_set (create/upsert)."""

    async def test_creates_new_budget(self, budget_pool):
        """Creating a new budget returns a full budget row."""
        from butlers.tools.finance.budgets import budget_set

        result = await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")

        assert result["category"] == "groceries"
        assert Decimal(result["amount"]) == Decimal("500.00")
        assert result["period"] == "monthly"
        assert result["currency"] == "USD"
        assert result["is_active"] is True
        assert result["id"] is not None
        assert "created_at" in result

    async def test_creates_with_explicit_currency(self, budget_pool):
        """Budget currency is stored as uppercase ISO-4217."""
        from butlers.tools.finance.budgets import budget_set

        result = await budget_set(
            budget_pool, category="travel", amount=1000.0, period="monthly", currency="eur"
        )
        assert result["currency"] == "EUR"

    async def test_creates_with_custom_thresholds(self, budget_pool):
        """Custom warn/alert thresholds are persisted correctly."""
        from butlers.tools.finance.budgets import budget_set

        result = await budget_set(
            budget_pool,
            category="dining",
            amount=200.0,
            period="monthly",
            warn_threshold=0.75,
            alert_threshold=0.95,
        )
        assert Decimal(result["warn_threshold"]) == Decimal("0.7500")
        assert Decimal(result["alert_threshold"]) == Decimal("0.9500")

    async def test_default_thresholds(self, budget_pool):
        """Default warn=0.80 and alert=1.00 are applied when not specified."""
        from butlers.tools.finance.budgets import budget_set

        result = await budget_set(budget_pool, category="utilities", amount=150.0, period="monthly")
        assert Decimal(result["warn_threshold"]) == Decimal("0.8000")
        assert Decimal(result["alert_threshold"]) == Decimal("1.0000")

    async def test_upsert_deactivates_existing(self, budget_pool):
        """Setting a budget for the same category+period deactivates the previous one."""
        from butlers.tools.finance.budgets import budget_set

        first = await budget_set(budget_pool, category="groceries", amount=400.0, period="monthly")
        first_id = first["id"]

        second = await budget_set(budget_pool, category="groceries", amount=600.0, period="monthly")

        # The new budget is active
        assert second["is_active"] is True
        assert Decimal(second["amount"]) == Decimal("600.00")

        # The old budget should be deactivated
        old_row = await budget_pool.fetchrow(
            "SELECT is_active FROM budgets WHERE id = $1::uuid", first_id
        )
        assert old_row["is_active"] is False

    async def test_upsert_preserves_different_period(self, budget_pool):
        """Setting monthly budget does not affect the yearly budget for the same category."""
        from butlers.tools.finance.budgets import budget_set

        monthly = await budget_set(
            budget_pool, category="groceries", amount=400.0, period="monthly"
        )
        yearly = await budget_set(budget_pool, category="groceries", amount=4800.0, period="yearly")

        # Monthly budget is still active
        monthly_row = await budget_pool.fetchrow(
            "SELECT is_active FROM budgets WHERE id = $1::uuid", monthly["id"]
        )
        assert monthly_row["is_active"] is True

        # Yearly budget is active too
        assert yearly["is_active"] is True

    async def test_invalid_period_raises(self, budget_pool):
        """An unsupported period raises ValueError."""
        from butlers.tools.finance.budgets import budget_set

        with pytest.raises(ValueError, match="Unsupported period"):
            await budget_set(budget_pool, category="food", amount=100.0, period="bi-weekly")

    async def test_invalid_amount_raises(self, budget_pool):
        """A non-positive amount raises ValueError."""
        from butlers.tools.finance.budgets import budget_set

        with pytest.raises(ValueError, match="must be positive"):
            await budget_set(budget_pool, category="food", amount=0.0, period="monthly")

        with pytest.raises(ValueError, match="must be positive"):
            await budget_set(budget_pool, category="food", amount=-50.0, period="monthly")

    async def test_all_valid_periods(self, budget_pool):
        """All four valid period values are accepted."""
        from butlers.tools.finance.budgets import budget_set

        for period in ("weekly", "monthly", "quarterly", "yearly"):
            result = await budget_set(
                budget_pool, category=f"cat_{period}", amount=100.0, period=period
            )
            assert result["period"] == period


# ---------------------------------------------------------------------------
# budget_list tests
# ---------------------------------------------------------------------------


class TestBudgetList:
    """Tests for budget_list (list active budgets)."""

    async def test_empty_when_no_budgets(self, budget_pool):
        """Returns empty list and count=0 when no budgets exist."""
        from butlers.tools.finance.budgets import budget_list

        result = await budget_list(budget_pool)
        assert result["budgets"] == []
        assert result["count"] == 0

    async def test_returns_active_budgets(self, budget_pool):
        """Returns all active budgets."""
        from butlers.tools.finance.budgets import budget_list, budget_set

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        await budget_set(budget_pool, category="dining", amount=200.0, period="monthly")

        result = await budget_list(budget_pool)
        assert result["count"] == 2
        categories = [b["category"] for b in result["budgets"]]
        assert "groceries" in categories
        assert "dining" in categories

    async def test_excludes_inactive_budgets(self, budget_pool):
        """Deactivated budgets are not returned."""
        from butlers.tools.finance.budgets import budget_list, budget_set

        await budget_set(budget_pool, category="groceries", amount=400.0, period="monthly")
        # Upsert deactivates the old one and creates a new one
        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")

        result = await budget_list(budget_pool)
        # Only one active budget should appear
        assert result["count"] == 1
        assert Decimal(result["budgets"][0]["amount"]) == Decimal("500.00")

    async def test_returns_full_row_structure(self, budget_pool):
        """Each budget entry has the expected fields."""
        from butlers.tools.finance.budgets import budget_list, budget_set

        await budget_set(budget_pool, category="utilities", amount=150.0, period="monthly")
        result = await budget_list(budget_pool)

        assert result["count"] == 1
        budget = result["budgets"][0]
        expected_keys = {
            "id",
            "category",
            "period",
            "amount",
            "currency",
            "warn_threshold",
            "alert_threshold",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert expected_keys.issubset(set(budget.keys()))

    async def test_ordered_by_category_period(self, budget_pool):
        """Results are ordered by category ASC, period ASC."""
        from butlers.tools.finance.budgets import budget_list, budget_set

        await budget_set(budget_pool, category="transport", amount=100.0, period="monthly")
        await budget_set(budget_pool, category="dining", amount=200.0, period="monthly")
        await budget_set(budget_pool, category="dining", amount=300.0, period="yearly")

        result = await budget_list(budget_pool)
        categories = [b["category"] for b in result["budgets"]]
        # dining (monthly, yearly) comes before transport
        assert categories[0] == "dining"
        assert categories[-1] == "transport"


# ---------------------------------------------------------------------------
# budget_remove tests
# ---------------------------------------------------------------------------


class TestBudgetRemove:
    """Tests for budget_remove (soft-delete)."""

    async def test_removes_active_budget(self, budget_pool):
        """Removing an existing active budget returns removed=True."""
        from butlers.tools.finance.budgets import budget_remove, budget_set

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        result = await budget_remove(budget_pool, category="groceries", period="monthly")

        assert result["removed"] is True
        assert result["category"] == "groceries"
        assert result["period"] == "monthly"

    async def test_removed_budget_no_longer_active(self, budget_pool):
        """After removal, the budget does not appear in budget_list."""
        from butlers.tools.finance.budgets import budget_list, budget_remove, budget_set

        await budget_set(budget_pool, category="dining", amount=200.0, period="monthly")
        await budget_remove(budget_pool, category="dining", period="monthly")

        result = await budget_list(budget_pool)
        assert result["count"] == 0

    async def test_remove_nonexistent_returns_false(self, budget_pool):
        """Removing a budget that doesn't exist returns removed=False."""
        from butlers.tools.finance.budgets import budget_remove

        result = await budget_remove(budget_pool, category="nonexistent", period="monthly")
        assert result["removed"] is False

    async def test_remove_already_inactive_returns_false(self, budget_pool):
        """Removing an already-deactivated budget returns removed=False."""
        from butlers.tools.finance.budgets import budget_remove, budget_set

        await budget_set(budget_pool, category="travel", amount=800.0, period="monthly")
        # First removal succeeds
        first = await budget_remove(budget_pool, category="travel", period="monthly")
        assert first["removed"] is True
        # Second removal on already-inactive budget
        second = await budget_remove(budget_pool, category="travel", period="monthly")
        assert second["removed"] is False

    async def test_remove_only_matching_period(self, budget_pool):
        """Removing monthly budget does not affect the yearly budget."""
        from butlers.tools.finance.budgets import budget_list, budget_remove, budget_set

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        await budget_set(budget_pool, category="groceries", amount=6000.0, period="yearly")

        await budget_remove(budget_pool, category="groceries", period="monthly")

        result = await budget_list(budget_pool)
        assert result["count"] == 1
        assert result["budgets"][0]["period"] == "yearly"

    async def test_invalid_period_raises(self, budget_pool):
        """An unsupported period raises ValueError."""
        from butlers.tools.finance.budgets import budget_remove

        with pytest.raises(ValueError, match="Unsupported period"):
            await budget_remove(budget_pool, category="food", period="bi-weekly")


# ---------------------------------------------------------------------------
# budget_status tests
# ---------------------------------------------------------------------------


class TestBudgetStatus:
    """Tests for budget_status (per-category utilization)."""

    async def test_empty_when_no_budgets(self, budget_pool):
        """Returns empty items and count=0 when no budgets are active."""
        from butlers.tools.finance.budgets import budget_status

        result = await budget_status(budget_pool)
        assert result["items"] == []
        assert result["count"] == 0

    async def test_on_track_with_no_spending(self, budget_pool):
        """A budget with no spending is on_track with 0% utilization."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")

        result = await budget_status(budget_pool)
        assert result["count"] == 1
        item = result["items"][0]
        assert item["category"] == "groceries"
        assert item["status"] == "on_track"
        assert Decimal(item["spent"]) == Decimal("0")
        assert item["utilization_pct"] == 0.0

    async def test_on_track_below_warn_threshold(self, budget_pool):
        """Spending below warn_threshold returns on_track status."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(
            budget_pool, category="dining", amount=200.0, period="monthly", warn_threshold=0.8
        )
        await _insert_tx(budget_pool, category="dining", amount="100.00")  # 50% utilization

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert item["status"] == "on_track"
        assert item["utilization_pct"] == pytest.approx(50.0, rel=1e-3)

    async def test_warning_at_warn_threshold(self, budget_pool):
        """Spending at exactly warn_threshold returns warning status."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(
            budget_pool,
            category="dining",
            amount=200.0,
            period="monthly",
            warn_threshold=0.8,
            alert_threshold=1.0,
        )
        await _insert_tx(budget_pool, category="dining", amount="160.00")  # 80%

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert item["status"] == "warning"
        assert item["utilization_pct"] == pytest.approx(80.0, rel=1e-3)

    async def test_warning_between_thresholds(self, budget_pool):
        """Spending between warn and alert thresholds returns warning."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(
            budget_pool,
            category="dining",
            amount=200.0,
            period="monthly",
            warn_threshold=0.8,
            alert_threshold=1.0,
        )
        await _insert_tx(budget_pool, category="dining", amount="180.00")  # 90%

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert item["status"] == "warning"

    async def test_exceeded_at_alert_threshold(self, budget_pool):
        """Spending at exactly alert_threshold (default 100%) returns exceeded."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="dining", amount=200.0, period="monthly")
        await _insert_tx(budget_pool, category="dining", amount="200.00")  # 100%

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert item["status"] == "exceeded"
        assert item["utilization_pct"] == pytest.approx(100.0, rel=1e-3)

    async def test_exceeded_over_budget(self, budget_pool):
        """Spending over budget returns exceeded and negative remaining."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="dining", amount=200.0, period="monthly")
        await _insert_tx(budget_pool, category="dining", amount="250.00")  # 125%

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert item["status"] == "exceeded"
        assert Decimal(item["remaining"]) == Decimal("-50.00")
        assert item["utilization_pct"] == pytest.approx(125.0, rel=1e-3)

    async def test_excludes_credit_transactions(self, budget_pool):
        """Credit-direction transactions are not counted toward spending."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        await _insert_tx(budget_pool, category="groceries", amount="100.00", direction="debit")
        await _insert_tx(budget_pool, category="groceries", amount="50.00", direction="credit")

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert Decimal(item["spent"]) == Decimal("100.00")

    async def test_excludes_deleted_transactions(self, budget_pool):
        """Soft-deleted transactions (deleted_at IS NOT NULL) are excluded."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        await _insert_tx(budget_pool, category="groceries", amount="100.00")
        # Insert a soft-deleted transaction
        await _insert_tx(
            budget_pool, category="groceries", amount="999.00", deleted_at=datetime.now(UTC)
        )

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert Decimal(item["spent"]) == Decimal("100.00")

    async def test_period_alignment_excludes_prior_period(self, budget_pool):
        """Transactions from a prior period are excluded from the current period total."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")

        # Transaction in the current month
        this_month = _now_mid_month()
        await _insert_tx(budget_pool, category="groceries", amount="100.00", posted_at=this_month)

        # Transaction in the prior month
        prior_month = (this_month.replace(day=1) - timedelta(days=1)).replace(
            day=1, hour=12, minute=0, second=0, microsecond=0
        )
        await _insert_tx(budget_pool, category="groceries", amount="999.00", posted_at=prior_month)

        result = await budget_status(budget_pool)
        item = result["items"][0]
        # Only the current-month transaction should be counted
        assert Decimal(item["spent"]) == Decimal("100.00")

    async def test_multiple_categories_independent(self, budget_pool):
        """Spending for one category does not affect another category's status."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        await budget_set(budget_pool, category="dining", amount=200.0, period="monthly")

        await _insert_tx(budget_pool, category="groceries", amount="400.00")
        await _insert_tx(budget_pool, category="dining", amount="50.00")

        result = await budget_status(budget_pool)
        assert result["count"] == 2

        by_category = {item["category"]: item for item in result["items"]}
        assert by_category["groceries"]["status"] == "warning"  # 80%
        assert by_category["dining"]["status"] == "on_track"  # 25%

    async def test_response_shape(self, budget_pool):
        """budget_status returns the expected keys on each item."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        result = await budget_status(budget_pool)

        item = result["items"][0]
        expected_keys = {
            "category",
            "period",
            "budget_amount",
            "currency",
            "spent",
            "remaining",
            "utilization_pct",
            "status",
            "period_start",
            "period_end",
            "warn_threshold",
            "alert_threshold",
        }
        assert expected_keys == set(item.keys())

    async def test_monthly_period_bounds(self, budget_pool):
        """A monthly budget reports the first and last day of the current month."""
        import calendar as _calendar

        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="groceries", amount=500.0, period="monthly")
        result = await budget_status(budget_pool)
        item = result["items"][0]

        today = datetime.now(UTC).date()
        expected_start = today.replace(day=1)
        expected_end = today.replace(day=_calendar.monthrange(today.year, today.month)[1])
        assert item["period_start"] == expected_start.isoformat()
        assert item["period_end"] == expected_end.isoformat()

    async def test_weekly_period_bounds(self, budget_pool):
        """A weekly budget reports Monday..Sunday of the current week."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="dining", amount=200.0, period="weekly")
        result = await budget_status(budget_pool)
        item = result["items"][0]

        today = datetime.now(UTC).date()
        expected_start = today - timedelta(days=today.isoweekday() - 1)
        expected_end = expected_start + timedelta(days=6)
        assert item["period_start"] == expected_start.isoformat()
        assert item["period_end"] == expected_end.isoformat()
        # Spec invariant: start is a Monday, end is a Sunday.
        assert date.fromisoformat(item["period_start"]).isoweekday() == 1
        assert date.fromisoformat(item["period_end"]).isoweekday() == 7

    async def test_quarterly_and_yearly_period_bounds(self, budget_pool):
        """Quarterly bounds align to the quarter; yearly bounds span Jan 1..Dec 31."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(budget_pool, category="travel", amount=1000.0, period="quarterly")
        await budget_set(budget_pool, category="gifts", amount=600.0, period="yearly")
        result = await budget_status(budget_pool)
        by_category = {item["category"]: item for item in result["items"]}

        today = datetime.now(UTC).date()

        q_start_month = ((today.month - 1) // 3) * 3 + 1
        expected_q_start = date(today.year, q_start_month, 1)
        assert by_category["travel"]["period_start"] == expected_q_start.isoformat()
        # Quarter start month is one of Jan/Apr/Jul/Oct.
        assert date.fromisoformat(by_category["travel"]["period_start"]).month in {1, 4, 7, 10}

        assert by_category["gifts"]["period_start"] == date(today.year, 1, 1).isoformat()
        assert by_category["gifts"]["period_end"] == date(today.year, 12, 31).isoformat()

    async def test_excludes_inactive_budgets_from_status(self, budget_pool):
        """Deactivated budgets are not included in budget_status."""
        from butlers.tools.finance.budgets import budget_remove, budget_set, budget_status

        await budget_set(budget_pool, category="travel", amount=1000.0, period="monthly")
        await budget_remove(budget_pool, category="travel", period="monthly")

        result = await budget_status(budget_pool)
        assert result["count"] == 0

    async def test_custom_thresholds_affect_status(self, budget_pool):
        """Custom warn/alert thresholds determine the status boundaries correctly."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(
            budget_pool,
            category="entertainment",
            amount=100.0,
            period="monthly",
            warn_threshold=0.5,
            alert_threshold=0.9,
        )
        await _insert_tx(budget_pool, category="entertainment", amount="60.00")  # 60%

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert item["status"] == "warning"

    async def test_excludes_different_currency_transactions(self, budget_pool):
        """Transactions in a different currency are not counted against a USD budget."""
        from butlers.tools.finance.budgets import budget_set, budget_status

        await budget_set(
            budget_pool, category="groceries", amount=500.0, period="monthly", currency="USD"
        )
        await _insert_tx(budget_pool, category="groceries", amount="100.00", currency="USD")
        # EUR transaction should NOT be counted toward the USD budget
        await _insert_tx(budget_pool, category="groceries", amount="200.00", currency="EUR")

        result = await budget_status(budget_pool)
        item = result["items"][0]
        assert Decimal(item["spent"]) == Decimal("100.00")


# ---------------------------------------------------------------------------
# budget_set threshold validation tests
# ---------------------------------------------------------------------------


class TestBudgetSetThresholdValidation:
    """Tests for warn_threshold / alert_threshold validation in budget_set."""

    async def test_warn_threshold_above_one_raises(self, budget_pool):
        """warn_threshold > 1.0 raises ValueError."""
        from butlers.tools.finance.budgets import budget_set

        with pytest.raises(ValueError, match="warn_threshold must be between"):
            await budget_set(
                budget_pool,
                category="food",
                amount=100.0,
                period="monthly",
                warn_threshold=1.5,
            )

    async def test_warn_threshold_negative_raises(self, budget_pool):
        """warn_threshold < 0.0 raises ValueError."""
        from butlers.tools.finance.budgets import budget_set

        with pytest.raises(ValueError, match="warn_threshold must be between"):
            await budget_set(
                budget_pool,
                category="food",
                amount=100.0,
                period="monthly",
                warn_threshold=-0.1,
            )

    async def test_alert_threshold_above_two_raises(self, budget_pool):
        """alert_threshold > 2.0 raises ValueError."""
        from butlers.tools.finance.budgets import budget_set

        with pytest.raises(ValueError, match="alert_threshold must be between"):
            await budget_set(
                budget_pool,
                category="food",
                amount=100.0,
                period="monthly",
                alert_threshold=2.5,
            )

    async def test_alert_threshold_negative_raises(self, budget_pool):
        """alert_threshold < 0.0 raises ValueError."""
        from butlers.tools.finance.budgets import budget_set

        with pytest.raises(ValueError, match="alert_threshold must be between"):
            await budget_set(
                budget_pool,
                category="food",
                amount=100.0,
                period="monthly",
                alert_threshold=-0.1,
            )

    async def test_warn_greater_than_alert_raises(self, budget_pool):
        """warn_threshold > alert_threshold raises ValueError."""
        from butlers.tools.finance.budgets import budget_set

        with pytest.raises(ValueError, match="warn_threshold.*must not exceed.*alert_threshold"):
            await budget_set(
                budget_pool,
                category="food",
                amount=100.0,
                period="monthly",
                warn_threshold=0.9,
                alert_threshold=0.5,
            )

    async def test_valid_boundary_thresholds_accepted(self, budget_pool):
        """warn_threshold=0.0 and alert_threshold=2.0 are accepted as boundary values."""
        from butlers.tools.finance.budgets import budget_set

        result = await budget_set(
            budget_pool,
            category="food",
            amount=100.0,
            period="monthly",
            warn_threshold=0.0,
            alert_threshold=2.0,
        )
        assert Decimal(result["warn_threshold"]) == Decimal("0.0000")
        assert Decimal(result["alert_threshold"]) == Decimal("2.0000")


# ---------------------------------------------------------------------------
# Package import tests
# ---------------------------------------------------------------------------


async def test_budget_crud_functions_importable_from_package():
    """budget_set, budget_list, budget_remove, budget_status are importable from finance."""
    from butlers.tools.finance import (  # noqa: F401
        budget_list,
        budget_remove,
        budget_set,
        budget_status,
    )

    assert callable(budget_set)
    assert callable(budget_list)
    assert callable(budget_remove)
    assert callable(budget_status)
