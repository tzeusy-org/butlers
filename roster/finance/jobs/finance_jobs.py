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
import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance.alerts import detect_price_changes
from butlers.tools.finance.anomaly_detection import anomaly_scan
from butlers.tools.finance.budgets import budget_status
from butlers.tools.finance.overview import subscription_audit
from butlers.tools.finance.pattern_recognition import predict_bills
from butlers.tools.finance.reconciliation import reconcile_bills
from butlers.tools.switchboard.insight.broker import propose_insight_candidate

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _finance_scoped_connection(db_pool: asyncpg.Pool):
    """Acquire a connection with ``search_path`` forced to ``finance, public``.

    The finance tool-layer functions this module calls (``detect_price_changes``,
    ``anomaly_scan``, ``budget_status``, ``subscription_audit``, ``reconcile_bills``,
    ``predict_bills``) use bare, unqualified table names — they assume an ambient
    ``finance`` schema, which the daemon's per-butler pool already sets in
    production. Generic test pools do not set this, so this helper makes the
    calls schema-safe in both contexts without touching the shared tool code.
    """
    async with db_pool.acquire() as conn:
        await conn.execute("SET search_path TO finance, public")
        yield conn


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

# Subscription price-change thresholds (bu-rvz2o: absorbs subscription-renewal-alerts'
# detect_price_changes() call). detect_price_changes() only returns changes > 5%.
_PRICE_CHANGE_PRIORITY_HIGH = 75  # >=20% change
_PRICE_CHANGE_PRIORITY_MID = 60  # 10-20% change
_PRICE_CHANGE_PRIORITY_LOW = 45  # 5-10% change (detect_price_changes' own floor)
_PRICE_CHANGE_THRESHOLD_HIGH = Decimal("20")
_PRICE_CHANGE_THRESHOLD_MID = Decimal("10")

# bu-rvz2o: absorbs the daily anomaly-digest direct-notify task. anomaly_scan()
# severities are "high"/"medium"/"low" — map onto the insight priority scale.
_ANOMALY_SEVERITY_PRIORITY: dict[str, int] = {"high": 75, "medium": 55, "low": 35}
_MAX_ANOMALY_CANDIDATES_PER_RUN = 10

# bu-rvz2o: absorbs upcoming-bills-check's reconciliation sweep.
_BILL_RECONCILED_PRIORITY = 35  # informational — already happened
_BILL_RECONCILE_CANDIDATE_PRIORITY = 55  # actionable — owner confirmation needed
_BILL_PREDICTED_PRIORITY = 30  # advisory — untracked recurring pattern

# bu-rvz2o: absorbs monthly-spending-summary + subscription-audit-monthly.
_MONTHLY_DIGEST_PRIORITY = 55

# bu-7hogl: restore the month-over-month "notable changes" trend content the old
# monthly-spending-summary task produced (via spending_trends(comparison=
# "month_over_month")). A category is "notable" when its spend swings by more than
# this percentage vs. the month before the digest's covered month, or when it
# newly appears / disappears. Capped for message legibility; overflow is disclosed.
_MONTHLY_TREND_SWING_PCT = Decimal("20")
_MONTHLY_TREND_MAX_NOTABLE = 5


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
        today = date.today()
        horizon = today + timedelta(days=7)
        rows = await conn.fetch(
            """
            SELECT id, service, amount, currency, next_renewal, frequency
            FROM finance.subscriptions
            WHERE
                status = 'active'
                AND next_renewal <= $2
                AND next_renewal >= $1
            ORDER BY next_renewal ASC
            """,
            today,
            horizon,
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
# run_insight_scan
# ---------------------------------------------------------------------------


async def run_insight_scan(db_pool: asyncpg.Pool) -> dict[str, Any]:
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

    today = date.today()
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
                   b.warn_threshold, b.alert_threshold,
                   COALESCE(SUM(ABS(t.amount)), 0) AS spent
            FROM finance.budgets b
            LEFT JOIN finance.transactions t
                ON t.category = b.category
               AND t.direction = 'debit'
               AND t.posted_at >= $1
               AND t.posted_at < $2
            WHERE b.is_active = true
              AND b.period = 'monthly'
            GROUP BY b.id, b.category, b.amount, b.warn_threshold, b.alert_threshold
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

        # bu-rvz2o: use the budget's OWN configured warn/alert thresholds
        # (finance.budgets.warn_threshold/alert_threshold) instead of hardcoded
        # 80%/90% — this is what budget_status() (the tool the old direct-notify
        # budget-status-check task called) already respects, and what the owner
        # actually configured per category. Defaults are 0.80/1.00 respectively.
        warn_threshold = Decimal(str(row["warn_threshold"]))
        alert_threshold = Decimal(str(row["alert_threshold"]))

        if utilisation < warn_threshold:
            continue

        if utilisation >= alert_threshold:
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

    # ------------------------------------------------------------------
    # 5. Subscription price changes (bu-rvz2o: absorbs subscription-renewal-alerts'
    #    detect_price_changes() call — the one piece of that weekly digest not
    #    already covered by the renewal check above).
    # ------------------------------------------------------------------
    async with _finance_scoped_connection(db_pool) as conn:
        price_change_result = await detect_price_changes(conn, days_back=60)

    for change in price_change_result.get("changes", []):
        service = change["service"]
        change_pct = change.get("change_pct")
        direction = change.get("direction", "increase")
        currency = change.get("currency", "USD")
        tracked_amount = change.get("tracked_amount")
        recent_charge = change.get("recent_charge")

        if change_pct is None:
            priority = _PRICE_CHANGE_PRIORITY_LOW
            pct_label = "a new charge amount"
        else:
            abs_pct = Decimal(str(abs(change_pct)))
            if abs_pct >= _PRICE_CHANGE_THRESHOLD_HIGH:
                priority = _PRICE_CHANGE_PRIORITY_HIGH
            elif abs_pct >= _PRICE_CHANGE_THRESHOLD_MID:
                priority = _PRICE_CHANGE_PRIORITY_MID
            else:
                priority = _PRICE_CHANGE_PRIORITY_LOW
            pct_label = f"{abs_pct:.0f}% {direction}"

        message = (
            f"Subscription price change detected: {service} — {pct_label} "
            f"(was {currency} {tracked_amount}, now {currency} {recent_charge})"
        )
        service_slug = re.sub(r"[^a-z0-9]+", "-", service.lower()).strip("-") or "unknown"
        dedup_key = f"finance:subscription-price-change:{service_slug}:{year_month}"

        keep_going = await _submit(
            priority=priority,
            category="subscription-price-change",
            dedup_key=dedup_key,
            message=message,
            expires_at=month_end_dt,
            cooldown_days=30,
            metadata={
                "service": service,
                "tracked_amount": tracked_amount,
                "recent_charge": recent_charge,
                "change_pct": change_pct,
                "currency": currency,
            },
        )
        if not keep_going:
            logger.info("Finance insight scan: verbosity=off early exit (price changes)")
            return {**counts, "early_exit": True}

    logger.info(
        "Finance insight scan complete: submitted=%d accepted=%d filtered=%d errors=%d",
        counts["submitted"],
        counts["accepted"],
        counts["filtered"],
        counts["errors"],
    )
    return {**counts, "early_exit": False}


# ---------------------------------------------------------------------------
# run_bill_reconciliation_sweep (bu-rvz2o: absorbs upcoming-bills-check)
# ---------------------------------------------------------------------------


async def run_bill_reconciliation_sweep(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Run the weekly bill-reconciliation sweep and surface results as insights.

    Replaces the old ``upcoming-bills-check`` prompt-mode cron task. The
    reconciliation itself (``reconcile_bills``) is a deterministic, mutating
    action — it stays a first-class job step, not gated by insight verbosity.
    Its *results* (auto-settled bills, ambiguous matches needing confirmation,
    and untracked recurring patterns from ``predict_bills``) are surfaced as
    insight candidates instead of an LLM-composed digest, so they flow through
    the same budget/dedup/quiet-hours machinery as everything else.

    The routine "bill due soon" digest that used to live in this same prompt
    is intentionally NOT reproduced here — ``run_insight_scan`` already emits
    a ``bill-due`` candidate per overdue/upcoming bill on its own (now-daily)
    cadence, so repeating it here would just double-notify.

    Returns
    -------
    dict
        ``{auto_settled_count, confirm_candidates_count, predicted_count,
        submitted, accepted, filtered, errors}``
    """
    logger.info("Running finance bill reconciliation sweep job")

    today = date.today()
    counts: dict[str, int] = {"submitted": 0, "accepted": 0, "filtered": 0, "errors": 0}

    async def _submit(**kwargs: Any) -> bool:
        counts["submitted"] += 1
        status = await _propose(db_pool, **kwargs)
        if status == "filtered":
            counts["filtered"] += 1
            return False
        elif status == "error":
            counts["errors"] += 1
        else:
            counts["accepted"] += 1
        return True

    async with _finance_scoped_connection(db_pool) as conn:
        reconcile_result = await reconcile_bills(conn, lookback_days=90)
    auto_settled = reconcile_result.get("auto_settled", [])
    confirm_candidates = reconcile_result.get("candidates", [])

    async with _finance_scoped_connection(db_pool) as conn:
        predict_result = await predict_bills(conn, days_ahead=30)
    untracked_predictions = [
        p for p in predict_result.get("predictions", []) if not p.get("is_tracked", False)
    ]

    if auto_settled:
        payees = ", ".join(sorted({item["payee"] for item in auto_settled}))
        message = f"Auto-settled {len(auto_settled)} bill(s) from matched transactions: {payees}"
        keep_going = await _submit(
            priority=_BILL_RECONCILED_PRIORITY,
            category="bill-reconciled",
            dedup_key=f"finance:bill-reconciled:{today.isoformat()}",
            message=message,
            expires_at=datetime(today.year, today.month, today.day, tzinfo=UTC) + timedelta(days=3),
            cooldown_days=1,
            metadata={"count": len(auto_settled), "bill_ids": [i["bill_id"] for i in auto_settled]},
        )
        if not keep_going:
            return {
                "auto_settled_count": len(auto_settled),
                "confirm_candidates_count": len(confirm_candidates),
                "predicted_count": len(untracked_predictions),
                **counts,
            }

    if confirm_candidates:
        payees = ", ".join(sorted({item["payee"] for item in confirm_candidates}))
        message = (
            f"{len(confirm_candidates)} bill(s) have ambiguous transaction matches "
            f"needing confirmation: {payees}"
        )
        keep_going = await _submit(
            priority=_BILL_RECONCILE_CANDIDATE_PRIORITY,
            category="bill-reconcile-candidate",
            dedup_key=f"finance:bill-reconcile-candidate:{today.isoformat()}",
            message=message,
            expires_at=datetime(today.year, today.month, today.day, tzinfo=UTC) + timedelta(days=7),
            cooldown_days=1,
            metadata={
                "count": len(confirm_candidates),
                "bill_ids": [i["bill_id"] for i in confirm_candidates],
            },
        )
        if not keep_going:
            return {
                "auto_settled_count": len(auto_settled),
                "confirm_candidates_count": len(confirm_candidates),
                "predicted_count": len(untracked_predictions),
                **counts,
            }

    if untracked_predictions:
        payees = ", ".join(sorted({item["payee"] for item in untracked_predictions}))
        message = (
            f"{len(untracked_predictions)} untracked recurring payment pattern(s) detected: "
            f"{payees}"
        )
        await _submit(
            priority=_BILL_PREDICTED_PRIORITY,
            category="bill-predicted",
            dedup_key=f"finance:bill-predicted:{today.isoformat()}",
            message=message,
            expires_at=datetime(today.year, today.month, today.day, tzinfo=UTC)
            + timedelta(days=30),
            cooldown_days=7,
            metadata={"count": len(untracked_predictions)},
        )

    logger.info(
        "Finance bill reconciliation sweep complete: auto_settled=%d candidates=%d "
        "predicted=%d submitted=%d accepted=%d",
        len(auto_settled),
        len(confirm_candidates),
        len(untracked_predictions),
        counts["submitted"],
        counts["accepted"],
    )
    return {
        "auto_settled_count": len(auto_settled),
        "confirm_candidates_count": len(confirm_candidates),
        "predicted_count": len(untracked_predictions),
        **counts,
    }


# ---------------------------------------------------------------------------
# run_anomaly_insight_scan (bu-rvz2o: absorbs anomaly-digest)
# ---------------------------------------------------------------------------


async def run_anomaly_insight_scan(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Run the daily per-transaction anomaly scan and propose insight candidates.

    Replaces the old ``anomaly-digest`` prompt-mode cron task. This is a
    genuinely different signal than ``run_insight_scan``'s category-level
    ``spending-anomaly`` (a monthly-average comparison): ``anomaly_scan()``
    flags individual transactions (amount outliers, first-time merchants,
    category velocity spikes) and must keep its daily cadence to stay useful.

    Each anomaly becomes its own dedupeable, priority-scored insight candidate
    (severity high/medium/low -> priority 75/55/35) instead of an always-fire
    LLM-composed digest. A run is capped at
    ``_MAX_ANOMALY_CANDIDATES_PER_RUN`` candidates (most severe first) so a
    pathological day cannot flood the owner or the insight budget; anything
    beyond the cap is reported in ``truncated`` rather than silently dropped.

    Returns
    -------
    dict
        ``{anomalies_found, submitted, accepted, filtered, errors, truncated,
        status}``
    """
    logger.info("Running finance anomaly insight scan job")

    async with _finance_scoped_connection(db_pool) as conn:
        result = await anomaly_scan(conn, days_back=1, sensitivity="medium")
    status = result.get("status", "ok")

    if status == "insufficient_data":
        logger.info("Finance anomaly insight scan: insufficient baseline data, skipping")
        return {
            "anomalies_found": 0,
            "submitted": 0,
            "accepted": 0,
            "filtered": 0,
            "errors": 0,
            "truncated": 0,
            "status": status,
        }

    today = date.today()
    anomalies = result.get("anomalies", [])

    severity_order = {"high": 0, "medium": 1, "low": 2}
    sorted_anomalies = sorted(
        anomalies, key=lambda a: severity_order.get(a.get("severity", "low"), 3)
    )
    truncated = max(0, len(sorted_anomalies) - _MAX_ANOMALY_CANDIDATES_PER_RUN)
    selected = sorted_anomalies[:_MAX_ANOMALY_CANDIDATES_PER_RUN]

    counts: dict[str, int] = {"submitted": 0, "accepted": 0, "filtered": 0, "errors": 0}

    for anomaly in selected:
        severity = anomaly.get("severity", "low")
        priority = _ANOMALY_SEVERITY_PRIORITY.get(severity, 35)
        txn_id = anomaly.get("transaction_id")
        category = anomaly.get("category")
        identity = txn_id or (f"category-{category}" if category else anomaly.get("type", "n-a"))
        dedup_key = f"finance:anomaly:{identity}:{today.isoformat()}"

        merchant = anomaly.get("merchant")
        explanation = anomaly.get("explanation", "")
        subject = merchant or category or anomaly.get("type", "transaction")
        message = f"Spending anomaly ({severity}): {subject} — {explanation}"

        counts["submitted"] += 1
        status_result = await _propose(
            db_pool,
            priority=priority,
            category="spending-anomaly-transaction",
            dedup_key=dedup_key,
            message=message,
            expires_at=datetime(today.year, today.month, today.day, tzinfo=UTC) + timedelta(days=2),
            cooldown_days=1,
            metadata={"anomaly_type": anomaly.get("type"), "severity": severity},
        )
        if status_result == "filtered":
            counts["filtered"] += 1
            logger.info("Finance anomaly insight scan: verbosity=off early exit")
            break
        elif status_result == "error":
            counts["errors"] += 1
        else:
            counts["accepted"] += 1

    logger.info(
        "Finance anomaly insight scan complete: found=%d submitted=%d accepted=%d truncated=%d",
        len(anomalies),
        counts["submitted"],
        counts["accepted"],
        truncated,
    )
    return {
        "anomalies_found": len(anomalies),
        "truncated": truncated,
        "status": status,
        **counts,
    }


# ---------------------------------------------------------------------------
# run_monthly_finance_digest (bu-rvz2o: absorbs monthly-spending-summary +
# subscription-audit-monthly — their "subscription audit" bullets were
# literally duplicated across both prompts, so they are merged into one
# deterministic monthly candidate rather than two competing LLM prompts.)
# ---------------------------------------------------------------------------


async def _month_over_month_trend(
    db_pool: asyncpg.Pool,
    *,
    last_month_start: date,
    last_month_end: date,
) -> dict[str, Any] | None:
    """Compute the month-over-month "notable changes" trend for the digest.

    Compares the digest's covered month (``[last_month_start, last_month_end)``)
    against the calendar month immediately before it, per category. This is the
    deterministic equivalent of the old ``monthly-spending-summary`` task's
    ``spending_trends(comparison="month_over_month", months=2)`` call plus its
    per-category delta pass (bu-7hogl).

    Returns
    -------
    dict | None
        ``{prior_period, direction, total_change_pct, notable, notable_total}``
        where ``notable`` is a capped list of human-readable category-swing
        strings, or ``None`` when there is insufficient prior-month data to
        compute a meaningful comparison (the digest then simply omits the bullet
        rather than blocking).
    """
    prior_month_end = last_month_start
    prior_month_start = (last_month_start - timedelta(days=1)).replace(day=1)

    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                category,
                COALESCE(
                    SUM(ABS(amount)) FILTER (WHERE posted_at >= $1 AND posted_at < $2),
                    0
                ) AS prior_total,
                COALESCE(
                    SUM(ABS(amount)) FILTER (WHERE posted_at >= $2 AND posted_at < $3),
                    0
                ) AS last_total
            FROM finance.transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              AND posted_at < $3
            GROUP BY category
            """,
            prior_month_start,
            prior_month_end,
            last_month_end,
        )

    prior_grand = Decimal("0.00")
    last_grand = Decimal("0.00")
    # (sort_key, label) so we can surface the biggest swings first.
    scored: list[tuple[Decimal, str]] = []
    for row in rows:
        category = row["category"] or "uncategorized"
        prior_total = Decimal(str(row["prior_total"]))
        last_total = Decimal(str(row["last_total"]))
        prior_grand += prior_total
        last_grand += last_total

        if prior_total > 0 and last_total > 0:
            change_pct = (last_total - prior_total) / prior_total * 100
            if abs(change_pct) > _MONTHLY_TREND_SWING_PCT:
                sign = "+" if change_pct >= 0 else ""
                scored.append(
                    (abs(last_total - prior_total), f"{category} {sign}{change_pct:.0f}%")
                )
        elif prior_total == 0 and last_total > 0:
            scored.append((last_total, f"{category} (new)"))
        elif prior_total > 0 and last_total == 0:
            scored.append((prior_total, f"{category} (no spend)"))

    # Insufficient prior-month data -> no meaningful month-over-month comparison.
    if prior_grand <= 0:
        return None

    total_change_pct = (last_grand - prior_grand) / prior_grand * 100
    if total_change_pct > 0:
        direction = "up"
    elif total_change_pct < 0:
        direction = "down"
    else:
        direction = "flat"

    scored.sort(key=lambda item: item[0], reverse=True)
    notable = [label for _, label in scored[:_MONTHLY_TREND_MAX_NOTABLE]]

    return {
        "prior_period": prior_month_start.strftime("%Y-%m"),
        "direction": direction,
        "total_change_pct": total_change_pct,
        "notable": notable,
        "notable_total": len(scored),
    }


async def run_monthly_finance_digest(db_pool: asyncpg.Pool) -> dict[str, Any]:
    """Compose and propose one consolidated monthly finance digest insight.

    Combines the prior calendar month's spending summary (total spend, top 3
    categories) with the current budget status and subscription audit — the
    two pieces of content that ``monthly-spending-summary`` and
    ``subscription-audit-monthly`` both independently generated every month.

    This is proposed as a single, medium-priority, month-scoped insight
    candidate rather than delivered unconditionally: per repo doctrine,
    insights flow through candidates -> broker -> delivery under the owner's
    own verbosity preference, even for periodic "always fire" reports.

    Returns
    -------
    dict
        ``{status, period}`` — the ``propose_insight_candidate`` result status
        and the ``YYYY-MM`` period label this digest covers.
    """
    logger.info("Running finance monthly digest job")

    today = date.today()
    first_of_this_month = today.replace(day=1)
    last_month_end = first_of_this_month
    last_month_start = (first_of_this_month - timedelta(days=1)).replace(day=1)
    period_label = last_month_start.strftime("%Y-%m")

    async with db_pool.acquire() as conn:
        category_rows = await conn.fetch(
            """
            SELECT category, SUM(ABS(amount)) AS total
            FROM finance.transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              AND posted_at < $2
            GROUP BY category
            ORDER BY total DESC
            LIMIT 3
            """,
            last_month_start,
            last_month_end,
        )

    total_spend = Decimal("0.00")
    async with db_pool.acquire() as conn:
        total_row = await conn.fetchrow(
            """
            SELECT COALESCE(SUM(ABS(amount)), 0) AS total
            FROM finance.transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              AND posted_at < $2
            """,
            last_month_start,
            last_month_end,
        )
    if total_row:
        total_spend = Decimal(str(total_row["total"]))

    top_categories = ", ".join(
        f"{row['category']} (${Decimal(str(row['total'])):.2f})" for row in category_rows
    )

    async with _finance_scoped_connection(db_pool) as conn:
        budget_result = await budget_status(conn)
    flagged = [item for item in budget_result.get("items", []) if item.get("status") != "on_track"]
    if flagged:
        budget_summary = "; ".join(
            f"{item['category']} {item['status']} ({item['utilization_pct']:.0f}%)"
            for item in flagged
        )
    else:
        budget_summary = "all categories on track"

    async with _finance_scoped_connection(db_pool) as conn:
        audit_result = await subscription_audit(conn)
    active_count = sum(
        1 for e in audit_result.get("entries", []) if e.get("status") == "tracked_active"
    )
    untracked_count = sum(
        1 for e in audit_result.get("entries", []) if e.get("status") == "detected_untracked"
    )
    total_annual_cost = audit_result.get("total_annual_cost", "0")

    # bu-7hogl: restore the month-over-month "notable changes" trend content.
    # Never let a trend computation failure block the digest — degrade to omitting
    # the bullet (the digest is more valuable delivered without it than not at all).
    trend: dict[str, Any] | None = None
    try:
        trend = await _month_over_month_trend(
            db_pool,
            last_month_start=last_month_start,
            last_month_end=last_month_end,
        )
    except Exception:  # noqa: BLE001 — graceful degradation, never block the digest
        logger.warning(
            "Finance monthly digest: month-over-month trend computation failed; "
            "sending digest without the trend bullet",
            exc_info=True,
        )

    if trend is not None:
        trend_segment = (
            f" Month-over-month: total spend {trend['direction']} "
            f"{abs(trend['total_change_pct']):.0f}% vs {trend['prior_period']}"
        )
        if trend["notable"]:
            notable_str = ", ".join(trend["notable"])
            overflow = trend["notable_total"] - len(trend["notable"])
            if overflow > 0:
                notable_str += f" (+{overflow} more)"
            trend_segment += f"; notable changes: {notable_str}"
        trend_segment += "."
    else:
        trend_segment = ""

    message = (
        f"Monthly finance digest for {period_label}: "
        f"total spend ${total_spend:.2f}"
        + (f", top categories: {top_categories}" if top_categories else "")
        + f". Budget status: {budget_summary}. "
        f"Subscriptions: {active_count} active (${total_annual_cost}/yr projected)"
        + (f", {untracked_count} untracked pattern(s) detected" if untracked_count else "")
        + "."
        + trend_segment
    )

    result = await propose_insight_candidate(
        db_pool,
        origin_butler=_INSIGHT_BUTLER,
        priority=_MONTHLY_DIGEST_PRIORITY,
        category="monthly-finance-digest",
        dedup_key=f"finance:monthly-digest:{period_label}",
        message=message,
        expires_at=_end_of_month(today),
        cooldown_days=25,
        metadata={
            "period": period_label,
            "total_spend": str(total_spend),
            "budget_flagged_count": len(flagged),
            "subscription_active_count": active_count,
            "subscription_untracked_count": untracked_count,
            "trend_available": trend is not None,
            "trend_direction": trend["direction"] if trend else None,
            "trend_notable_count": trend["notable_total"] if trend else 0,
        },
    )

    logger.info(
        "Finance monthly digest complete: period=%s status=%s", period_label, result["status"]
    )
    return {"status": result["status"], "period": period_label}
