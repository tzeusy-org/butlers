"""Finance butler spending summary — aggregate outflow spend over a date range."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

VALID_GROUP_BY_MODES = {"category", "merchant", "week", "month"}


def _current_month_bounds() -> tuple[date, date]:
    """Return (start_date, end_date) for the current calendar month."""
    today = datetime.now(UTC).date()
    start = today.replace(day=1)
    # End of month: first day of next month minus one day
    if today.month == 12:
        end = date(today.year + 1, 1, 1)
    else:
        end = date(today.year, today.month + 1, 1)
    # end_date is inclusive — use last day of current month
    end = end - timedelta(days=1)
    return start, end


def _iso(d: date) -> str:
    return d.isoformat()


async def spending_summary(
    pool: asyncpg.Pool,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    group_by: str | None = None,
    category_filter: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate outflow (debit-direction) spending across a date range.

    Parameters
    ----------
    pool:
        Database connection pool (schema must be set to ``finance``).
    start_date:
        Inclusive start date (ISO-8601 or ``date`` object).
        Defaults to the first day of the current calendar month.
    end_date:
        Inclusive end date (ISO-8601 or ``date`` object).
        Defaults to the last day of the current calendar month.
    group_by:
        How to bucket the results.  Supported values: ``category``,
        ``merchant``, ``week``, ``month``.  When *None* the results are not
        bucketed (single overall group).
    category_filter:
        If supplied, only transactions with this exact category are included.
    account_id:
        If supplied, only transactions linked to this account UUID are included.

    Returns
    -------
    dict
        A ``SpendingSummaryResponse``-shaped dict::

            {
                "start_date": "2026-02-01",
                "end_date": "2026-02-23",
                "currency": "USD",
                "total_spend": "427.50",
                "groups": [
                    {"key": "groceries", "amount": "210.00", "count": 5},
                    ...
                ],
            }

        Amounts are returned as strings so callers can preserve
        ``NUMERIC(14,2)`` precision without floating-point loss.

    Raises
    ------
    ValueError
        If ``group_by`` is not one of the supported grouping modes.
    """
    if group_by is not None and group_by not in VALID_GROUP_BY_MODES:
        raise ValueError(
            f"Unsupported group_by value: {group_by!r}. "
            f"Must be one of: {', '.join(sorted(VALID_GROUP_BY_MODES))}"
        )

    # Resolve date range — default to current calendar month
    if start_date is None or end_date is None:
        default_start, default_end = _current_month_bounds()
        if start_date is None:
            start_date = default_start
        if end_date is None:
            end_date = default_end

    # Normalise to date objects
    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    # Build WHERE clauses
    conditions: list[str] = [
        "direction = 'debit'",
        "posted_at::date >= $1",
        "posted_at::date <= $2",
    ]
    params: list[Any] = [start_date, end_date]
    idx = 3

    if category_filter is not None:
        conditions.append(f"category = ${idx}")
        params.append(category_filter)
        idx += 1

    if account_id is not None:
        conditions.append(f"account_id = ${idx}::uuid")
        params.append(account_id)
        idx += 1

    where_clause = " AND ".join(conditions)

    # --- Total spend (across all matching rows) ---
    total_row = await pool.fetchrow(
        f"SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE {where_clause}",
        *params,
    )
    total_spend: Decimal = total_row["total"]

    # --- Determine representative currency ---
    # Use the currency that appears most frequently among matching rows.
    currency_row = await pool.fetchrow(
        f"""
        SELECT currency, COUNT(*) AS cnt
        FROM transactions
        WHERE {where_clause}
        GROUP BY currency
        ORDER BY cnt DESC
        LIMIT 1
        """,
        *params,
    )
    currency: str = currency_row["currency"] if currency_row else "USD"

    # --- Grouping ---
    groups: list[dict[str, Any]] = []

    if group_by is None:
        # No grouping — single bucket covering the whole range
        count_row = await pool.fetchrow(
            f"SELECT COUNT(*) AS cnt FROM transactions WHERE {where_clause}",
            *params,
        )
        groups.append(
            {
                "key": "total",
                "amount": str(total_spend),
                "count": count_row["cnt"] if count_row else 0,
            }
        )

    elif group_by == "category":
        rows = await pool.fetch(
            f"""
            SELECT category AS key,
                   SUM(amount) AS amount,
                   COUNT(*) AS count
            FROM transactions
            WHERE {where_clause}
            GROUP BY category
            ORDER BY amount DESC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    elif group_by == "merchant":
        rows = await pool.fetch(
            f"""
            SELECT merchant AS key,
                   SUM(amount) AS amount,
                   COUNT(*) AS count
            FROM transactions
            WHERE {where_clause}
            GROUP BY merchant
            ORDER BY amount DESC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    elif group_by == "week":
        # ISO week bucket: "YYYY-Www" e.g. "2026-W08"
        rows = await pool.fetch(
            f"""
            SELECT TO_CHAR(DATE_TRUNC('week', posted_at), 'IYYY-"W"IW') AS key,
                   SUM(amount) AS amount,
                   COUNT(*) AS count
            FROM transactions
            WHERE {where_clause}
            GROUP BY DATE_TRUNC('week', posted_at)
            ORDER BY DATE_TRUNC('week', posted_at) ASC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    elif group_by == "month":
        # Calendar month bucket: "YYYY-MM" e.g. "2026-02"
        rows = await pool.fetch(
            f"""
            SELECT TO_CHAR(DATE_TRUNC('month', posted_at), 'YYYY-MM') AS key,
                   SUM(amount) AS amount,
                   COUNT(*) AS count
            FROM transactions
            WHERE {where_clause}
            GROUP BY DATE_TRUNC('month', posted_at)
            ORDER BY DATE_TRUNC('month', posted_at) ASC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    return {
        "start_date": _iso(start_date),
        "end_date": _iso(end_date),
        "currency": currency,
        "total_spend": str(total_spend),
        "groups": groups,
    }
