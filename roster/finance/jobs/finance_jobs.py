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
from datetime import date, timedelta
from decimal import Decimal

import asyncpg

logger = logging.getLogger(__name__)


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
                OR (due_date < CURRENT_DATE AND status = 'pending')
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

    if not category_rows and not merchant_rows:
        logger.info("Monthly spending summary: no transactions found for %s", period_label)
        return {
            "period": period_label,
            "total_spend": "0.00",
            "categories": 0,
            "merchants": 0,
            "notable_changes": 0,
        }

    total_spend = Decimal("0.00")
    for row in category_rows:
        total_spend += Decimal(str(row["total"]))

    # Build prev-month category totals for MoM delta
    prev_totals: dict[str, Decimal] = {}
    for row in prev_category_rows:
        prev_totals[row["category"]] = Decimal(str(row["total"]))

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

    # New categories that disappeared (were in prev but not current)
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
