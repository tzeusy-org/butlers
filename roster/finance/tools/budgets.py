"""Finance butler budget tools — budget management, spending trends, and forecasting.

Implements:
- budget_set(category, amount, period, ...): Create or replace a budget target.
- budget_list(): List all active budgets.
- budget_remove(category, period): Deactivate a budget.
- budget_status(): Per-category utilization status (on_track/warning/exceeded).
- spending_trends(comparison, months, category): MoM/YoY trend analysis with
  percentage change and direction indicators.
- spending_forecast(): Linear projection for end-of-month spending, per-category
  forecasts including budget comparison and first-of-month edge case handling.
"""

from __future__ import annotations

import calendar
import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from butlers.core.general_settings import resolve_general_timezone

logger = logging.getLogger(__name__)

# Supported budget periods — see ``_period_bounds`` for how each one is aligned.
VALID_PERIODS = {"weekly", "monthly", "quarterly", "yearly"}

# Default thresholds
DEFAULT_WARN_THRESHOLD = Decimal("0.80")
DEFAULT_ALERT_THRESHOLD = Decimal("1.00")

CREATE_BUDGETS_TABLE_SQL = """
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

_FLAT_THRESHOLD_PCT = Decimal("5")  # abs(change_pct) < 5% => direction="flat"


# ---------------------------------------------------------------------------
# Internal helpers — budget management
# ---------------------------------------------------------------------------


async def resolve_budget_zone(pool: asyncpg.Pool) -> ZoneInfo:
    """Return the timezone every budget and spending period is measured in.

    A budget period is a claim about the *owner's* calendar: "this week's coffee
    budget" means the week the owner is living in, and the alert that fires on
    Monday morning has to be about the week that started that morning. Reading
    the boundary off UTC — or off whatever local date the daemon's host happens
    to have — makes that claim false for every owner who is not on UTC, and does
    so invisibly, because on a UTC machine all three frames coincide (bu-4zd9h).

    The zone is the owner's configured general-settings timezone, the same one
    the scheduler already uses to interpret cron fields in local time, so a scan
    fired at the owner's 08:00 measures the owner's period. It falls back to UTC
    when the setting is unreadable or unset, which keeps this a no-op for
    unconfigured deployments and for CI.
    """
    return ZoneInfo(await resolve_general_timezone(pool))


def _period_anchor(zone: ZoneInfo, now: datetime | None = None) -> date:
    """Return the owner-local calendar date whose period is "the current one"."""
    return (now or datetime.now(UTC)).astimezone(zone).date()


def _period_window(
    period_start: date,
    period_end: date,
    zone: ZoneInfo,
) -> tuple[datetime, datetime]:
    """Return the half-open instant range ``[start, end)`` spanning a period.

    Both bounds are owner-local midnights: a period opens when the owner's first
    day begins and closes when the day *after* its last day begins. Half-open is
    what makes the upper edge unambiguous — a transaction posted at exactly the
    boundary instant belongs to the next period and to that period only, so
    consecutive windows tile the timeline without gap or overlap.
    """
    start = datetime.combine(period_start, time.min, tzinfo=zone)
    end = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def _period_bounds(period: str, anchor: date) -> tuple[date, date]:
    """Return the inclusive (start, end) date bounds of the current budget period.

    Weekly periods run Monday..Sunday, monthly from the 1st, quarterly from the
    quarter start, yearly from Jan 1. ``anchor`` is a date in the owner's
    timezone (see :func:`_period_anchor`); the instants that date range covers
    come from :func:`_period_window`.

    Parameters
    ----------
    period:
        Budget period: ``weekly``, ``monthly``, ``quarterly``, or ``yearly``.
    anchor:
        The date whose containing period is computed (typically "today").

    Returns
    -------
    tuple[date, date]
        ``(period_start, period_end)`` — both inclusive.
    """
    if period == "weekly":
        # ISO weekday: Monday == 1 ... Sunday == 7; align week start to Monday.
        from datetime import timedelta

        start = anchor - timedelta(days=anchor.isoweekday() - 1)
        end = start + timedelta(days=6)
        return start, end
    if period == "monthly":
        start = anchor.replace(day=1)
        end = anchor.replace(day=calendar.monthrange(anchor.year, anchor.month)[1])
        return start, end
    if period == "quarterly":
        quarter_index = (anchor.month - 1) // 3  # 0..3
        start_month = quarter_index * 3 + 1
        end_month = start_month + 2
        start = date(anchor.year, start_month, 1)
        end = date(anchor.year, end_month, calendar.monthrange(anchor.year, end_month)[1])
        return start, end
    if period == "yearly":
        return date(anchor.year, 1, 1), date(anchor.year, 12, 31)
    raise ValueError(
        f"Unsupported period: {period!r}. Must be one of: {', '.join(sorted(VALID_PERIODS))}"
    )


def _budget_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert a budgets table row to a serializable dict."""
    import uuid

    d = dict(row)
    for key, val in d.items():
        if isinstance(val, uuid.UUID):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
        elif isinstance(val, Decimal):
            d[key] = str(val)
    return d


# ---------------------------------------------------------------------------
# Internal helpers — spending trends and forecast
# ---------------------------------------------------------------------------


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _days_in_month(d: date) -> int:
    return calendar.monthrange(d.year, d.month)[1]


def _n_months_ago(today: date, n: int) -> date:
    """Return the first day of the month that is exactly n full months before today's month."""
    year = today.year
    month = today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def _period_label(d: date) -> str:
    """Return YYYY-MM label for a date."""
    return d.strftime("%Y-%m")


def _safe_div(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Divide two Decimals; return None if denominator is zero."""
    if denominator == 0:
        return None
    return numerator / denominator


def _direction(change_pct: Decimal | None) -> str:
    """Classify a percentage change as 'up', 'down', or 'flat'."""
    if change_pct is None:
        return "flat"
    if change_pct.copy_abs() < _FLAT_THRESHOLD_PCT:
        return "flat"
    return "up" if change_pct > 0 else "down"


# ---------------------------------------------------------------------------
# Budget management
# ---------------------------------------------------------------------------


async def budget_set(
    pool: asyncpg.Pool,
    category: str,
    amount: Decimal | float | int,
    period: str,
    currency: str = "USD",
    warn_threshold: Decimal | float | None = None,
    alert_threshold: Decimal | float | None = None,
) -> dict[str, Any]:
    """Create or replace a budget for a given category and period.

    Uses deactivation-based versioning: any existing active budget for the same
    (category, period) pair is deactivated first, then a new active row is
    inserted. This preserves history while making the new budget the single active
    one.

    Parameters
    ----------
    pool:
        asyncpg connection pool (schema must be set to ``finance``).
    category:
        Budget category (e.g. ``"groceries"``, ``"dining"``).
    amount:
        Budget amount limit (positive value).
    period:
        Budget period: ``weekly``, ``monthly``, ``quarterly``, or ``yearly``.
    currency:
        ISO-4217 currency code (default ``"USD"``).
    warn_threshold:
        Utilization fraction that triggers a warning (default 0.80 = 80%).
        Must be between 0.0 and 1.0.
    alert_threshold:
        Utilization fraction that triggers an alert (default 1.00 = 100%).
        Must be between 0.0 and 2.0 (allows tracking over-budget amounts).

    Returns
    -------
    dict
        The newly created budget row.

    Raises
    ------
    ValueError
        If ``period`` is not a valid budget period.
    """
    if period not in VALID_PERIODS:
        raise ValueError(
            f"Unsupported period: {period!r}. Must be one of: {', '.join(sorted(VALID_PERIODS))}"
        )

    stored_amount = Decimal(str(amount))
    if stored_amount <= 0:
        raise ValueError(f"Budget amount must be positive, got: {stored_amount}")

    warn = Decimal(str(warn_threshold)) if warn_threshold is not None else DEFAULT_WARN_THRESHOLD
    alert = (
        Decimal(str(alert_threshold)) if alert_threshold is not None else DEFAULT_ALERT_THRESHOLD
    )

    # Validate threshold ranges and ordering.
    if not (Decimal("0") <= warn <= Decimal("1")):
        raise ValueError(f"warn_threshold must be between 0.0 and 1.0 inclusive, got: {warn}")
    if not (Decimal("0") <= alert <= Decimal("2")):
        raise ValueError(f"alert_threshold must be between 0.0 and 2.0 inclusive, got: {alert}")
    if warn > alert:
        raise ValueError(f"warn_threshold ({warn}) must not exceed alert_threshold ({alert})")

    now = datetime.now(UTC)

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Deactivate existing active budget for the same category+period
            await conn.execute(
                """
                UPDATE budgets
                   SET is_active = false,
                       updated_at = $1
                 WHERE category = $2
                   AND period = $3
                   AND is_active = true
                """,
                now,
                category,
                period,
            )

            # Insert the new active row
            row = await conn.fetchrow(
                """
                INSERT INTO budgets (
                    category, period, amount, currency,
                    warn_threshold, alert_threshold,
                    is_active, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, true, $7, $7)
                RETURNING *
                """,
                category,
                period,
                stored_amount,
                currency.upper(),
                warn,
                alert,
                now,
            )

    return _budget_row_to_dict(row)


async def budget_list(pool: asyncpg.Pool) -> dict[str, Any]:
    """Return all active budget rows.

    Parameters
    ----------
    pool:
        asyncpg connection pool (schema must be set to ``finance``).

    Returns
    -------
    dict
        ``{"budgets": [...], "count": N}`` where each budget is a full row dict
        with string-encoded numeric fields.
    """
    rows = await pool.fetch(
        """
        SELECT * FROM budgets
         WHERE is_active = true
         ORDER BY category ASC, period ASC
        """
    )
    return {
        "budgets": [_budget_row_to_dict(r) for r in rows],
        "count": len(rows),
    }


async def budget_remove(
    pool: asyncpg.Pool,
    category: str,
    period: str,
) -> dict[str, Any]:
    """Deactivate (soft-delete) the active budget for a given category and period.

    Parameters
    ----------
    pool:
        asyncpg connection pool (schema must be set to ``finance``).
    category:
        The budget category to remove.
    period:
        The budget period to remove: ``weekly``, ``monthly``, ``quarterly``, or ``yearly``.

    Returns
    -------
    dict
        ``{"removed": True, "category": ..., "period": ...}`` if found and
        deactivated, or ``{"removed": False, ...}`` if no active budget existed.
    """
    if period not in VALID_PERIODS:
        raise ValueError(
            f"Unsupported period: {period!r}. Must be one of: {', '.join(sorted(VALID_PERIODS))}"
        )

    now = datetime.now(UTC)
    result = await pool.execute(
        """
        UPDATE budgets
           SET is_active = false,
               updated_at = $1
         WHERE category = $2
           AND period = $3
           AND is_active = true
        """,
        now,
        category,
        period,
    )
    # asyncpg returns "UPDATE <count>" as the result tag
    updated_count = int(result.split()[-1])
    return {
        "removed": updated_count > 0,
        "category": category,
        "period": period,
    }


async def budget_status(
    pool: asyncpg.Pool,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compute per-category budget status by joining budgets with aggregated spending.

    For each active budget, aggregates debit transactions from the current budget
    period using DATE_TRUNC alignment. Returns status per category:
    - ``on_track`` -- utilization < warn_threshold
    - ``warning``  -- utilization >= warn_threshold and < alert_threshold
    - ``exceeded`` -- utilization >= alert_threshold

    Parameters
    ----------
    pool:
        asyncpg connection pool (schema must be set to ``finance``).
    now:
        Optional reference instant that selects "the current period". Defaults
        to :func:`datetime.now`; tests inject a fixed value so a period
        boundary can be examined from either side without waiting for one.

    Returns
    -------
    dict
        ``{"items": [...], "count": N}`` where each item has:
        - ``category`` -- budget category
        - ``period`` -- budget period
        - ``budget_amount`` -- string-encoded budget limit
        - ``currency`` -- ISO-4217 currency
        - ``spent`` -- string-encoded spending total for the current period
        - ``remaining`` -- string-encoded remaining budget (may be negative if exceeded)
        - ``utilization_pct`` -- float, spending / budget_amount as a percentage (0-100+)
        - ``status`` -- ``on_track``, ``warning``, or ``exceeded``
        - ``period_start`` -- ISO date (inclusive) of the current period's start
        - ``period_end`` -- ISO date (inclusive) of the current period's end
        - ``warn_threshold`` -- configured warning threshold fraction
        - ``alert_threshold`` -- configured alert threshold fraction
    """
    # Fetch all active budgets
    budgets = await pool.fetch(
        "SELECT * FROM budgets WHERE is_active = true ORDER BY category ASC, period ASC"
    )

    if not budgets:
        return {"items": [], "count": 0}

    zone = await resolve_budget_zone(pool)
    anchor = _period_anchor(zone, now)

    # Check whether transactions.deleted_at exists (per finance-transaction-schema spec).
    # Guard the filter dynamically so budget_status works on schemas both with and without it.
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name   = 'transactions'
               AND column_name  = 'deleted_at'
        )
        """
    )
    deleted_filter = "AND deleted_at IS NULL" if has_deleted_at else ""

    items: list[dict[str, Any]] = []

    for budget in budgets:
        category = budget["category"]
        period = budget["period"]
        budget_amount: Decimal = budget["amount"]
        currency: str = budget["currency"]
        warn_threshold: Decimal = budget["warn_threshold"]
        alert_threshold: Decimal = budget["alert_threshold"]

        period_start, period_end = _period_bounds(period, anchor)
        window_start, window_end = _period_window(period_start, period_end, zone)

        # Aggregate debit spending for this category and currency in the current period window.
        # Filter by currency to avoid incorrect cross-currency aggregation.
        #
        # Compared against instants rather than a DATE_TRUNC of the posting
        # timestamp: truncating in a fixed zone answers "which UTC week is this
        # in?", which is not the question a weekly budget asks. The half-open
        # bounds are owner-local midnights, so the aggregate covers exactly the
        # period reported in ``period_start``/``period_end``.
        spending_row = await pool.fetchrow(
            f"""
            SELECT COALESCE(SUM(amount), 0) AS spent
              FROM transactions
             WHERE direction = 'debit'
               {deleted_filter}
               AND category = $1
               AND currency = $2
               AND posted_at >= $3
               AND posted_at <  $4
            """,
            category,
            currency,
            window_start,
            window_end,
        )
        spent: Decimal = spending_row["spent"]

        remaining = budget_amount - spent

        if budget_amount > 0:
            utilization = spent / budget_amount
        else:
            utilization = Decimal("0")

        if utilization >= alert_threshold:
            status = "exceeded"
        elif utilization >= warn_threshold:
            status = "warning"
        else:
            status = "on_track"

        items.append(
            {
                "category": category,
                "period": period,
                "budget_amount": str(budget_amount),
                "currency": currency,
                "spent": str(spent),
                "remaining": str(remaining),
                "utilization_pct": float(utilization * 100),
                "status": status,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "warn_threshold": str(warn_threshold),
                "alert_threshold": str(alert_threshold),
            }
        )

    return {"items": items, "count": len(items)}


# ---------------------------------------------------------------------------
# spending_trends
# ---------------------------------------------------------------------------


async def spending_trends(
    pool: asyncpg.Pool,
    comparison: str = "mom",
    months: int = 6,
    category: str | None = None,
) -> dict[str, Any]:
    """Compare spending across time periods with percentage changes and direction indicators.

    Parameters
    ----------
    pool:
        asyncpg connection pool (schema must be set to ``finance``).
    comparison:
        Comparison mode. One of:
        - ``mom`` (month-over-month): returns per-month totals for the last N months.
        - ``yoy`` (year-over-year): compares current month against same month last year.
    months:
        Number of months of history to return for ``mom`` comparison. Ignored for ``yoy``.
        Minimum 2 (required for at least one MoM delta).
    category:
        If supplied, scopes the analysis to transactions matching this category only.

    Returns
    -------
    dict
        For ``mom``::

            {
                "comparison": "mom",
                "category": null,
                "periods": [
                    {
                        "period": "2025-08",
                        "total_spend": "300.00",
                        "change_amount": null,
                        "change_pct": null,
                        "direction": "flat"
                    },
                    {
                        "period": "2025-09",
                        "total_spend": "350.00",
                        "change_amount": "50.00",
                        "change_pct": "16.67",
                        "direction": "up"
                    },
                    ...
                ]
            }

        For ``yoy``::

            {
                "comparison": "yoy",
                "category": null,
                "current_period": "2026-03",
                "prior_period": "2025-03",
                "current_spend": "400.00",
                "prior_spend": "380.00",
                "change_amount": "20.00",
                "change_pct": "5.26",
                "direction": "up"
            }

        On insufficient data::

            {
                "status": "insufficient_data",
                "message": "..."
            }

    Raises
    ------
    ValueError
        If ``comparison`` is not one of ``mom``, ``yoy``.
    """
    if comparison not in ("mom", "yoy"):
        raise ValueError(f"Invalid comparison {comparison!r}. Must be 'mom' or 'yoy'.")

    zone = await resolve_budget_zone(pool)
    today = _period_anchor(zone)

    if comparison == "mom":
        return await _spending_trends_mom(
            pool, today=today, months=months, category=category, zone=zone
        )
    else:
        return await _spending_trends_yoy(pool, today=today, category=category, zone=zone)


async def _fetch_monthly_spend(
    pool: asyncpg.Pool,
    month_start: date,
    month_end: date,
    category: str | None,
    zone: ZoneInfo,
) -> Decimal:
    """Fetch total debit spend over ``month_start``..``month_end`` inclusive.

    The date bounds are days on the owner's calendar, so they are widened here
    into the half-open instant range those days actually occupy. Casting
    ``posted_at::date`` instead would have truncated in the *database session's*
    timezone — a fourth frame, agreeing with the caller only on a UTC host.
    """
    window_start, window_end = _period_window(month_start, month_end, zone)
    conditions = [
        "direction = 'debit'",
        "posted_at >= $1",
        "posted_at <  $2",
    ]
    params: list[Any] = [window_start, window_end]

    if category is not None:
        conditions.append("category = $3")
        params.append(category)

    where = " AND ".join(conditions)
    row = await pool.fetchrow(
        f"SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE {where}",
        *params,
    )
    if row is None:
        return Decimal("0")
    try:
        return Decimal(str(row["total"]))
    except (InvalidOperation, TypeError):
        return Decimal("0")


def _month_end(month_start: date) -> date:
    days = _days_in_month(month_start)
    return month_start.replace(day=days)


async def _spending_trends_mom(
    pool: asyncpg.Pool,
    *,
    today: date,
    months: int,
    category: str | None,
    zone: ZoneInfo,
) -> dict[str, Any]:
    """Compute month-over-month spending trend."""
    if months < 2:
        months = 2

    # Collect monthly totals: from (months) ago up to and including current month
    month_spends: list[tuple[str, Decimal]] = []
    for i in range(months - 1, -1, -1):
        m_start = _n_months_ago(today, i)
        m_end = _month_end(m_start)
        total = await _fetch_monthly_spend(pool, m_start, m_end, category, zone)
        month_spends.append((_period_label(m_start), total))

    # Check if we have sufficient data: at least 2 months must have spend data.
    # A spend of zero for all months means there are no transactions at all.
    # A single non-zero month means we cannot compute a meaningful prior comparison.
    non_zero = [spend for _, spend in month_spends if spend > 0]
    if len(non_zero) < 2:
        return {
            "status": "insufficient_data",
            "message": (
                "spending_trends requires at least 2 months of transaction data "
                "to compute month-over-month comparisons."
            ),
        }

    periods: list[dict[str, Any]] = []
    for idx, (period, total) in enumerate(month_spends):
        if idx == 0:
            periods.append(
                {
                    "period": period,
                    "total_spend": str(total),
                    "change_amount": None,
                    "change_pct": None,
                    "direction": "flat",
                }
            )
        else:
            prior = month_spends[idx - 1][1]
            change_amount = total - prior
            change_pct = _safe_div(change_amount * 100, prior)
            periods.append(
                {
                    "period": period,
                    "total_spend": str(total),
                    "change_amount": str(change_amount),
                    "change_pct": (
                        str(change_pct.quantize(Decimal("0.01")))
                        if change_pct is not None
                        else None
                    ),
                    "direction": _direction(change_pct),
                }
            )

    return {
        "comparison": "mom",
        "category": category,
        "periods": periods,
    }


async def _spending_trends_yoy(
    pool: asyncpg.Pool,
    *,
    today: date,
    category: str | None,
    zone: ZoneInfo,
) -> dict[str, Any]:
    """Compute year-over-year spending trend for the current month vs same month last year."""
    current_start = _month_start(today)
    current_end = _month_end(current_start)

    prior_start = current_start.replace(year=current_start.year - 1)
    prior_end = _month_end(prior_start)

    current_spend = await _fetch_monthly_spend(pool, current_start, current_end, category, zone)
    prior_spend = await _fetch_monthly_spend(pool, prior_start, prior_end, category, zone)

    # Insufficient data check
    if current_spend == 0 and prior_spend == 0:
        return {
            "status": "insufficient_data",
            "message": (
                "spending_trends requires transaction data in at least one of the "
                "compared periods to compute year-over-year comparisons."
            ),
        }

    change_amount = current_spend - prior_spend
    change_pct = _safe_div(change_amount * 100, prior_spend)

    return {
        "comparison": "yoy",
        "category": category,
        "current_period": _period_label(current_start),
        "prior_period": _period_label(prior_start),
        "current_spend": str(current_spend),
        "prior_spend": str(prior_spend),
        "change_amount": str(change_amount),
        "change_pct": (
            str(change_pct.quantize(Decimal("0.01"))) if change_pct is not None else None
        ),
        "direction": _direction(change_pct),
    }


# ---------------------------------------------------------------------------
# spending_forecast
# ---------------------------------------------------------------------------


async def spending_forecast(
    pool: asyncpg.Pool,
) -> dict[str, Any]:
    """Predict end-of-month spending based on current trajectory and historical patterns.

    Computes a linear projection: (current_month_spend / days_elapsed) * days_in_month.
    Includes per-category breakdowns with historical averages and optional budget comparison.

    First-of-month edge case: when called on the 1st of the month (days_elapsed == 0 or
    no current-month spend), uses the prior month's total as the forecast basis.

    Parameters
    ----------
    pool:
        asyncpg connection pool (schema must be set to ``finance``).

    Returns
    -------
    dict
        Forecast response::

            {
                "as_of_date": "2026-03-15",
                "days_elapsed": 15,
                "days_remaining": 16,
                "days_in_month": 31,
                "current_spend": "450.00",
                "projected_total": "930.00",
                "daily_average": "30.00",
                "basis": "linear_projection",
                "categories": [
                    {
                        "category": "dining",
                        "current_spend": "150.00",
                        "projected_total": "310.00",
                        "historical_average": "280.00",
                        "budget_amount": "300.00",
                        "projected_utilization_pct": "103.33",
                        "on_track": false
                    },
                    ...
                ]
            }
    """
    zone = await resolve_budget_zone(pool)
    today = _period_anchor(zone)
    month_start = _month_start(today)
    days_in_month = _days_in_month(month_start)
    days_elapsed = today.day  # day-of-month (1-indexed)
    days_remaining = days_in_month - days_elapsed

    # --- Fetch current month spend (overall) ---
    current_spend = await _fetch_monthly_spend(pool, month_start, today, None, zone)

    # --- First-of-month edge case ---
    # Also covers: called on day 1 regardless of current spend being zero or not -
    # use prior month as basis when days_elapsed == 1 (i.e. today is the 1st) AND
    # current spend is zero (no data yet this month).
    use_prior_month = today.day == 1 and current_spend == 0

    if use_prior_month:
        prior_start = _n_months_ago(today, 1)
        prior_end = _month_end(prior_start)
        prior_spend = await _fetch_monthly_spend(pool, prior_start, prior_end, None, zone)
        projected_total = prior_spend
        daily_average = _safe_div(prior_spend, Decimal(days_in_month)) or Decimal("0")
        basis = "prior_month"
    else:
        daily_average = _safe_div(current_spend, Decimal(days_elapsed)) or Decimal("0")
        projected_total = daily_average * Decimal(days_in_month)
        basis = "linear_projection"

    # --- Per-category current spend ---
    month_to_date_start, month_to_date_end = _period_window(month_start, today, zone)
    cat_rows = await pool.fetch(
        """
        SELECT category,
               SUM(amount) AS total
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          AND posted_at <  $2
        GROUP BY category
        ORDER BY total DESC
        """,
        month_to_date_start,
        month_to_date_end,
    )

    # --- Historical average (last 6 months, not counting current month) ---
    history_start = _n_months_ago(today, 6)
    history_end = _month_end(_n_months_ago(today, 1))

    history_window_start, history_window_end = _period_window(history_start, history_end, zone)
    # The month a transaction belongs to is the month it fell in for the owner,
    # so shift each timestamp into the owner's zone before truncating rather
    # than letting the database session's timezone decide the grouping.
    hist_rows = await pool.fetch(
        """
        SELECT category,
               AVG(monthly_total) AS avg_total
        FROM (
            SELECT category,
                   TO_CHAR(DATE_TRUNC('month', posted_at AT TIME ZONE $3), 'YYYY-MM')
                       AS month_label,
                   SUM(amount) AS monthly_total
            FROM transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              AND posted_at <  $2
            GROUP BY category, DATE_TRUNC('month', posted_at AT TIME ZONE $3)
        ) monthly_by_cat
        GROUP BY category
        """,
        history_window_start,
        history_window_end,
        str(zone),
    )
    hist_by_cat: dict[str, Decimal] = {}
    for r in hist_rows:
        try:
            hist_by_cat[r["category"]] = Decimal(str(r["avg_total"])).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError):
            hist_by_cat[r["category"]] = Decimal("0")

    # --- Fetch active budgets (if finance.budgets table exists) ---
    budgets_by_cat: dict[str, Decimal] = {}
    try:
        budget_rows = await pool.fetch(
            "SELECT category, amount FROM budgets WHERE is_active = true"
        )
        for br in budget_rows:
            try:
                budgets_by_cat[br["category"]] = Decimal(str(br["amount"]))
            except (InvalidOperation, TypeError):
                pass
    except asyncpg.UndefinedTableError:
        # budgets table may not exist yet (pre-migration); gracefully skip
        pass

    # --- Build category projections ---
    categories: list[dict[str, Any]] = []
    for row in cat_rows:
        cat = row["category"]
        try:
            cat_spend = Decimal(str(row["total"]))
        except (InvalidOperation, TypeError):
            cat_spend = Decimal("0")

        if use_prior_month:
            # For prior_month basis, use historical average as cat projection
            cat_projected = hist_by_cat.get(cat, cat_spend)
        else:
            cat_daily = _safe_div(cat_spend, Decimal(days_elapsed)) or Decimal("0")
            cat_projected = cat_daily * Decimal(days_in_month)

        cat_entry: dict[str, Any] = {
            "category": cat,
            "current_spend": str(cat_spend),
            "projected_total": str(cat_projected.quantize(Decimal("0.01"))),
            "historical_average": str(hist_by_cat.get(cat, Decimal("0"))),
        }

        if cat in budgets_by_cat:
            budget_amt = budgets_by_cat[cat]
            util_pct = _safe_div(cat_projected * 100, budget_amt)
            cat_entry["budget_amount"] = str(budget_amt)
            cat_entry["projected_utilization_pct"] = (
                str(util_pct.quantize(Decimal("0.01"))) if util_pct is not None else None
            )
            cat_entry["on_track"] = cat_projected <= budget_amt

        categories.append(cat_entry)

    return {
        "as_of_date": today.isoformat(),
        "days_elapsed": days_elapsed,
        "days_remaining": days_remaining,
        "days_in_month": days_in_month,
        "current_spend": str(current_spend),
        "projected_total": str(projected_total.quantize(Decimal("0.01"))),
        "daily_average": str(daily_average.quantize(Decimal("0.01"))),
        "basis": basis,
        "categories": categories,
    }
