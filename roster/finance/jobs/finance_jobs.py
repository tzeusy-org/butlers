"""Scheduled job handlers for the Finance butler.

Each job handler:
- Takes db_pool: asyncpg.Pool as first parameter
- Returns a dict with a summary of work done
- Uses async with db_pool.acquire() as conn for queries
- Uses the finance schema prefix (finance.bills, finance.subscriptions, finance.transactions)
- Is a no-op (returns early with zeros) when no matching data exists
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.switchboard.insight.broker import propose_insight_candidate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Insight scan constants
# ---------------------------------------------------------------------------

_INSIGHT_BUTLER = "finance"

# Spending anomaly thresholds (percentage above 3-month rolling average)
_ANOMALY_THRESHOLD_LOW = Decimal("0.30")  # >30%  — generate insight
_ANOMALY_THRESHOLD_MID = Decimal("0.50")  # >50%  — medium priority
_ANOMALY_THRESHOLD_HIGH = Decimal("1.00")  # >100% — high priority

# Priority assignments per spec
_SPENDING_ANOMALY_PRIORITY_HIGH = 80  # >100% above average
_SPENDING_ANOMALY_PRIORITY_MID = 65  # 50–100% above average
_SPENDING_ANOMALY_PRIORITY_LOW = 50  # 30–50% above average

_BILL_PRIORITY_CRITICAL = 92  # due within 1 day
_BILL_PRIORITY_SOON = 75  # due within 3 days

_BUDGET_PRIORITY_EXCEEDED = 70  # ≥90% utilisation
_BUDGET_PRIORITY_WARNING = 50  # 80–90% utilisation

_SUBSCRIPTION_PRIORITY_CRITICAL = 75  # renewal within 3 days
_SUBSCRIPTION_PRIORITY_SOON = 55  # renewal within 14 days


async def run_upcoming_bills_check(db_pool: asyncpg.Pool) -> dict:
    """Check for bills due within 14 days and overdue bills.

    Queries bills due within 14 days plus any overdue (past-due pending) bills.
    Classifies each bill into urgency buckets: overdue, due_today, due_soon.

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: bills_found, overdue, due_today, due_soon,
        total_amount_due.
    """
    logger.info("Running upcoming bills check job")

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, payee, amount, currency, due_date, status
            FROM finance.bills
            WHERE
                (due_date <= CURRENT_DATE + INTERVAL '14 days' AND status = 'pending')
                OR status = 'overdue'
            ORDER BY
                CASE
                    WHEN status = 'overdue' THEN 0
                    WHEN due_date = CURRENT_DATE THEN 1
                    ELSE 2
                END,
                due_date ASC
            """
        )

    if not rows:
        logger.info("Upcoming bills check: no bills found")
        return {
            "bills_found": 0,
            "overdue": 0,
            "due_today": 0,
            "due_soon": 0,
            "total_amount_due": "0.00",
        }

    today = date.today()
    overdue_count = 0
    due_today_count = 0
    due_soon_count = 0
    total_amount = Decimal("0.00")

    for row in rows:
        due = row["due_date"]
        status = row["status"]
        amount = Decimal(str(row["amount"]))
        total_amount += amount

        if status == "overdue" or due < today:
            overdue_count += 1
        elif due == today:
            due_today_count += 1
        else:
            due_soon_count += 1

    bills_found = len(rows)
    logger.info(
        "Upcoming bills check complete: %d bills found "
        "(overdue=%d, due_today=%d, due_soon=%d, total=$%s)",
        bills_found,
        overdue_count,
        due_today_count,
        due_soon_count,
        total_amount,
    )

    return {
        "bills_found": bills_found,
        "overdue": overdue_count,
        "due_today": due_today_count,
        "due_soon": due_soon_count,
        "total_amount_due": str(total_amount),
    }


async def run_subscription_renewal_alerts(db_pool: asyncpg.Pool) -> dict:
    """Find active subscriptions renewing within the next 7 days.

    Queries active subscriptions whose next_renewal date falls within 7 days
    from today (inclusive of today through 7 days ahead).

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: renewals_found, total_renewal_amount.
    """
    logger.info("Running subscription renewal alerts job")

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, service, amount, currency, next_renewal, frequency
            FROM finance.subscriptions
            WHERE
                status = 'active'
                AND next_renewal <= CURRENT_DATE + INTERVAL '7 days'
                AND next_renewal >= CURRENT_DATE
            ORDER BY next_renewal ASC
            """
        )

    if not rows:
        logger.info("Subscription renewal alerts: no upcoming renewals found")
        return {
            "renewals_found": 0,
            "total_renewal_amount": "0.00",
        }

    total_amount = Decimal("0.00")
    for row in rows:
        total_amount += Decimal(str(row["amount"]))

    renewals_found = len(rows)
    logger.info(
        "Subscription renewal alerts complete: %d renewals found, total=$%s",
        renewals_found,
        total_amount,
    )

    return {
        "renewals_found": renewals_found,
        "total_renewal_amount": str(total_amount),
    }


async def run_monthly_spending_summary(db_pool: asyncpg.Pool) -> dict:
    """Calculate spending summary for the prior calendar month.

    Queries debit transactions in the prior calendar month, grouped by category
    and by merchant (top 10). Also computes month-over-month changes for
    categories with a >20% swing compared to two months ago.

    Args:
        db_pool: Database connection pool.

    Returns:
        Dictionary with keys: period, total_spend, categories, merchants,
        notable_changes.
    """
    today = date.today()

    # Prior calendar month range
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month
    last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)

    # Two months ago range (for MoM comparison)
    two_months_end = last_month_start
    two_months_start = (last_month_start - timedelta(days=1)).replace(day=1)

    period_label = last_month_start.strftime("%Y-%m")
    logger.info("Running monthly spending summary for period: %s", period_label)

    async with db_pool.acquire() as conn:
        # Spending by category for prior month
        category_rows = await conn.fetch(
            """
            SELECT
                category,
                SUM(ABS(amount)) AS total,
                COUNT(*) AS count
            FROM finance.transactions
            WHERE
                direction = 'debit'
                AND posted_at >= $1
                AND posted_at < $2
            GROUP BY category
            ORDER BY total DESC
            """,
            last_month_start,
            last_month_end,
        )

        # Top 10 merchants for prior month
        merchant_rows = await conn.fetch(
            """
            SELECT
                merchant,
                SUM(ABS(amount)) AS total,
                COUNT(*) AS count
            FROM finance.transactions
            WHERE
                direction = 'debit'
                AND posted_at >= $1
                AND posted_at < $2
            GROUP BY merchant
            ORDER BY total DESC
            LIMIT 10
            """,
            last_month_start,
            last_month_end,
        )

        # Spending by category for two months ago (MoM comparison)
        prev_category_rows = await conn.fetch(
            """
            SELECT
                category,
                SUM(ABS(amount)) AS total,
                COUNT(*) AS count
            FROM finance.transactions
            WHERE
                direction = 'debit'
                AND posted_at >= $1
                AND posted_at < $2
            GROUP BY category
            ORDER BY total DESC
            """,
            two_months_start,
            two_months_end,
        )

    total_spend = Decimal("0.00")
    for row in category_rows:
        total_spend += Decimal(str(row["total"]))

    # Build prev-month category totals for MoM delta
    prev_totals: dict[str, Decimal] = {}
    for row in prev_category_rows:
        prev_totals[row["category"]] = Decimal(str(row["total"]))

    if not category_rows and not merchant_rows and not prev_totals:
        logger.info("Monthly spending summary: no transactions found for %s", period_label)
        return {
            "period": period_label,
            "total_spend": "0.00",
            "categories": 0,
            "merchants": 0,
            "notable_changes": 0,
        }

    # Find notable changes (>20% swing)
    notable_changes = 0
    for row in category_rows:
        cat = row["category"]
        current = Decimal(str(row["total"]))
        prev = prev_totals.get(cat, Decimal("0.00"))

        if prev > 0:
            delta_pct = abs(current - prev) / prev
            if delta_pct > Decimal("0.20"):
                notable_changes += 1
        elif current > 0:
            # New category this month — counts as notable
            notable_changes += 1

    # Categories that disappeared (were in prev but not in current)
    current_cats = {row["category"] for row in category_rows}
    for cat, prev_total in prev_totals.items():
        if cat not in current_cats and prev_total > 0:
            notable_changes += 1

    categories_count = len(category_rows)
    merchants_count = len(merchant_rows)

    logger.info(
        "Monthly spending summary complete: period=%s, total=$%s, "
        "categories=%d, merchants=%d, notable_changes=%d",
        period_label,
        total_spend,
        categories_count,
        merchants_count,
        notable_changes,
    )

    return {
        "period": period_label,
        "total_spend": str(total_spend),
        "categories": categories_count,
        "merchants": merchants_count,
        "notable_changes": notable_changes,
    }


# ---------------------------------------------------------------------------
# Insight scan helpers
# ---------------------------------------------------------------------------


def _end_of_month(ref: date) -> datetime:
    """Return midnight UTC at the end of the calendar month containing *ref*."""
    if ref.month == 12:
        next_month_start = date(ref.year + 1, 1, 1)
    else:
        next_month_start = date(ref.year, ref.month + 1, 1)
    # End-of-month = start of next month, normalised to midnight UTC
    return datetime(next_month_start.year, next_month_start.month, next_month_start.day, tzinfo=UTC)


async def _propose(
    pool: asyncpg.Pool,
    *,
    priority: int,
    category: str,
    dedup_key: str,
    message: str,
    expires_at: datetime,
    cooldown_days: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Propose one insight candidate; return the status string."""
    return (
        await propose_insight_candidate(
            pool,
            origin_butler=_INSIGHT_BUTLER,
            priority=priority,
            category=category,
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
            cooldown_days=cooldown_days,
            metadata=metadata,
        )
    )["status"]


# ---------------------------------------------------------------------------
# run_finance_insight_scan
# ---------------------------------------------------------------------------


async def run_finance_insight_scan(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Evaluate financial domain data and submit proactive insight candidates.

    Scans four categories in order:
    1. Spending anomalies — categories >30% above 3-month rolling average
    2. Upcoming bills — due within 3 days, not paid
    3. Budget thresholds — monthly spending at 80%+ of a budget target
    4. Subscription renewals — annual subscriptions renewing within 14 days

    Each candidate is submitted via ``propose_insight_candidate()``.
    If any submission returns ``{"status": "filtered"}``, verbosity is off and
    all remaining candidates are skipped (early exit).

    Args:
        db_pool: Database connection pool (used for both finance and insight tables).

    Returns:
        Dictionary with keys:
        - submitted:     total candidates submitted (accepted + error)
        - accepted:      candidates queued for delivery
        - filtered:      1 if verbosity=off triggered early exit, else 0
        - errors:        candidates that returned status=error
        - early_exit:    True if verbosity-off early exit triggered
    """
    logger.info("Running finance insight scan job")

    today = datetime.now(UTC).date()
    year_month = today.strftime("%Y-%m")

    counts: dict[str, int] = {
        "submitted": 0,
        "accepted": 0,
        "filtered": 0,
        "errors": 0,
    }

    async def _submit(**kwargs: Any) -> bool:
        """Submit one candidate. Returns False if early-exit should trigger."""
        counts["submitted"] += 1
        status = await _propose(db_pool, **kwargs)
        if status == "filtered":
            counts["filtered"] += 1
            return False  # signal early exit
        elif status == "error":
            counts["errors"] += 1
        else:
            counts["accepted"] += 1
        return True  # continue

    # ------------------------------------------------------------------
    # 1. Spending anomalies
    # ------------------------------------------------------------------
    month_start = date(today.year, today.month, 1)
    # 3-month rolling window start (go back 3 full calendar months)
    if today.month > 3:
        three_months_ago = date(today.year, today.month - 3, 1)
    else:
        three_months_ago = date(today.year - 1, today.month + 9, 1)

    async with db_pool.acquire() as conn:
        # Current month spending per category
        current_rows = await conn.fetch(
            """
            SELECT category, SUM(ABS(amount)) AS total
            FROM finance.transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              AND posted_at < $2
            GROUP BY category
            """,
            datetime(month_start.year, month_start.month, month_start.day, tzinfo=UTC),
            datetime(
                today.year,
                today.month,
                today.day,
                23,
                59,
                59,
                tzinfo=UTC,
            ),
        )

        # 3-month rolling average per category (only categories with data in all 3 months)
        rolling_rows = await conn.fetch(
            """
            SELECT
                category,
                COUNT(DISTINCT DATE_TRUNC('month', posted_at)) AS month_count,
                SUM(ABS(amount)) / COUNT(DISTINCT DATE_TRUNC('month', posted_at)) AS avg_monthly
            FROM finance.transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              AND posted_at < $2
            GROUP BY category
            HAVING COUNT(DISTINCT DATE_TRUNC('month', posted_at)) >= 3
            """,
            datetime(
                three_months_ago.year, three_months_ago.month, three_months_ago.day, tzinfo=UTC
            ),
            datetime(month_start.year, month_start.month, month_start.day, tzinfo=UTC),
        )

    rolling_avg: dict[str, Decimal] = {
        row["category"]: Decimal(str(row["avg_monthly"])) for row in rolling_rows
    }

    month_end_dt = _end_of_month(today)

    for row in current_rows:
        category = row["category"]
        if category not in rolling_avg:
            continue  # fewer than 3 months of history — exclude
        current_total = Decimal(str(row["total"]))
        avg_total = rolling_avg[category]
        if avg_total <= 0:
            continue
        pct_above = (current_total - avg_total) / avg_total
        if pct_above <= _ANOMALY_THRESHOLD_LOW:
            continue

        if pct_above > _ANOMALY_THRESHOLD_HIGH:
            priority = _SPENDING_ANOMALY_PRIORITY_HIGH
        elif pct_above > _ANOMALY_THRESHOLD_MID:
            priority = _SPENDING_ANOMALY_PRIORITY_MID
        else:
            priority = _SPENDING_ANOMALY_PRIORITY_LOW

        pct_label = f"{pct_above * 100:.0f}%"
        message = (
            f"Spending in '{category}' is {pct_label} above the 3-month average "
            f"(current: ${current_total:.2f}, average: ${avg_total:.2f})"
        )
        dedup_key = f"finance:spending-anomaly:{category}:{year_month}"
        keep_going = await _submit(
            priority=priority,
            category="spending-anomaly",
            dedup_key=dedup_key,
            message=message,
            expires_at=month_end_dt,
            metadata={
                "category": category,
                "current": str(current_total),
                "average": str(avg_total),
            },
        )
        if not keep_going:
            logger.info("Finance insight scan: verbosity=off early exit (spending anomalies)")
            return {**counts, "early_exit": True}

    # ------------------------------------------------------------------
    # 2. Upcoming bills (3-day window, not paid)
    # ------------------------------------------------------------------
    bill_window_end = today + timedelta(days=3)

    async with db_pool.acquire() as conn:
        bill_rows = await conn.fetch(
            """
            SELECT id, payee, amount, currency, due_date
            FROM finance.bills
            WHERE status = 'pending'
              AND due_date >= $1
              AND due_date <= $2
            ORDER BY due_date ASC
            """,
            today,
            bill_window_end,
        )

    for row in bill_rows:
        due = row["due_date"]
        days_until = (due - today).days
        bill_id = str(row["id"])
        payee = row["payee"]
        amount = Decimal(str(row["amount"]))
        currency = row["currency"]

        priority = _BILL_PRIORITY_CRITICAL if days_until <= 1 else _BILL_PRIORITY_SOON
        urgency_label = (
            "tomorrow"
            if days_until == 1
            else ("today" if days_until == 0 else f"in {days_until} days")
        )
        message = (
            f"Bill due {urgency_label}: {payee} — {currency} {amount:.2f} due on {due.isoformat()}"
        )
        dedup_key = f"finance:bill-due:{bill_id}:{due.isoformat()}"
        expires_at = datetime(due.year, due.month, due.day, 23, 59, 59, tzinfo=UTC)

        keep_going = await _submit(
            priority=priority,
            category="bill-due",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
            cooldown_days=1,
            metadata={
                "bill_id": bill_id,
                "payee": payee,
                "amount": str(amount),
                "currency": currency,
            },
        )
        if not keep_going:
            logger.info("Finance insight scan: verbosity=off early exit (upcoming bills)")
            return {**counts, "early_exit": True}

    # ------------------------------------------------------------------
    # 3. Budget thresholds (80%/90% utilisation)
    # ------------------------------------------------------------------
    async with db_pool.acquire() as conn:
        budget_rows = await conn.fetch(
            """
            SELECT b.id, b.category, b.amount AS budget_amount,
                   COALESCE(SUM(ABS(t.amount)), 0) AS spent
            FROM finance.budgets b
            LEFT JOIN finance.transactions t
                ON t.category = b.category
               AND t.direction = 'debit'
               AND t.posted_at >= $1
               AND t.posted_at < $2
            WHERE b.is_active = true
              AND b.period = 'monthly'
            GROUP BY b.id, b.category, b.amount
            """,
            datetime(month_start.year, month_start.month, month_start.day, tzinfo=UTC),
            datetime(
                today.year,
                today.month,
                today.day,
                23,
                59,
                59,
                tzinfo=UTC,
            ),
        )

    for row in budget_rows:
        budget_amount = Decimal(str(row["budget_amount"]))
        if budget_amount <= 0:
            continue
        spent = Decimal(str(row["spent"]))
        utilisation = spent / budget_amount

        # Spec thresholds: 80% → warning (P50), 90%+ → exceeded (P70)
        if utilisation < Decimal("0.80"):
            continue

        if utilisation >= Decimal("0.90"):
            priority = _BUDGET_PRIORITY_EXCEEDED
        else:
            priority = _BUDGET_PRIORITY_WARNING

        pct_label = f"{utilisation * 100:.0f}%"
        category = row["category"]
        message = (
            f"Budget alert: '{category}' spending is at {pct_label} of the monthly budget "
            f"(${spent:.2f} of ${budget_amount:.2f})"
        )
        dedup_key = f"finance:budget-threshold:{category}:{year_month}"

        keep_going = await _submit(
            priority=priority,
            category="budget-threshold",
            dedup_key=dedup_key,
            message=message,
            expires_at=month_end_dt,
            metadata={
                "category": category,
                "spent": str(spent),
                "budget": str(budget_amount),
                "utilisation_pct": str(utilisation),
            },
        )
        if not keep_going:
            logger.info("Finance insight scan: verbosity=off early exit (budget thresholds)")
            return {**counts, "early_exit": True}

    # ------------------------------------------------------------------
    # 4. Subscription renewals (annual only, 14-day window)
    # ------------------------------------------------------------------
    renewal_window_end = today + timedelta(days=14)

    async with db_pool.acquire() as conn:
        sub_rows = await conn.fetch(
            """
            SELECT id, service, amount, currency, next_renewal
            FROM finance.subscriptions
            WHERE status = 'active'
              AND frequency = 'yearly'
              AND next_renewal >= $1
              AND next_renewal <= $2
            ORDER BY next_renewal ASC
            """,
            today,
            renewal_window_end,
        )

    for row in sub_rows:
        renewal_date = row["next_renewal"]
        days_until = (renewal_date - today).days
        sub_id = str(row["id"])
        service = row["service"]
        amount = Decimal(str(row["amount"]))
        currency = row["currency"]

        priority = (
            _SUBSCRIPTION_PRIORITY_CRITICAL if days_until <= 3 else _SUBSCRIPTION_PRIORITY_SOON
        )
        urgency_label = (
            "today"
            if days_until == 0
            else ("tomorrow" if days_until == 1 else f"in {days_until} days")
        )
        message = (
            f"Annual subscription renewing {urgency_label}: {service} — "
            f"{currency} {amount:.2f} on {renewal_date.isoformat()}"
        )
        dedup_key = f"finance:subscription-renewal:{sub_id}:{renewal_date.isoformat()}"
        expires_at = datetime(
            renewal_date.year, renewal_date.month, renewal_date.day, 23, 59, 59, tzinfo=UTC
        )

        keep_going = await _submit(
            priority=priority,
            category="subscription-renewal",
            dedup_key=dedup_key,
            message=message,
            expires_at=expires_at,
            metadata={
                "subscription_id": sub_id,
                "service": service,
                "amount": str(amount),
                "currency": currency,
            },
        )
        if not keep_going:
            logger.info("Finance insight scan: verbosity=off early exit (subscription renewals)")
            return {**counts, "early_exit": True}

    logger.info(
        "Finance insight scan complete: submitted=%d accepted=%d filtered=%d errors=%d",
        counts["submitted"],
        counts["accepted"],
        counts["filtered"],
        counts["errors"],
    )
    return {**counts, "early_exit": False}
