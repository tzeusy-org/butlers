"""Finance butler bill tools — track payable obligations and surface upcoming dues."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _deserialize_row

_VALID_STATUSES = ("pending", "paid", "overdue")
_VALID_FREQUENCIES = ("one_time", "weekly", "monthly", "quarterly", "yearly", "custom")

_DEFAULT_DAYS_AHEAD = 14


def _normalize_date(value: str | date) -> date:
    """Normalize a date value to a date object."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _urgency(due: date, today: date, is_overdue: bool) -> str:
    """Compute urgency classification for a bill."""
    if is_overdue:
        return "overdue"
    days = (due - today).days
    if days == 0:
        return "due_today"
    return "due_soon"


async def track_bill(
    pool: asyncpg.Pool,
    payee: str,
    amount: float,
    currency: str,
    due_date: str | date,
    frequency: str = "one_time",
    status: str = "pending",
    payment_method: str | None = None,
    account_id: str | uuid.UUID | None = None,
    statement_period_start: str | date | None = None,
    statement_period_end: str | date | None = None,
    paid_at: datetime | str | None = None,
    source_message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a bill obligation in finance.bills.

    Upsert logic: match on (payee, due_date). If a record is found, update
    all provided fields and refresh updated_at. If no record exists, insert.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    payee:
        Name of the payee (e.g. "PG&E", "Comcast", "Chase Credit Card").
    amount:
        Amount owed.
    currency:
        ISO-4217 uppercase currency code (e.g. "USD", "EUR").
    due_date:
        Date the bill is due. Accepts ISO date strings or date objects.
    frequency:
        Recurrence frequency. One of: one_time, weekly, monthly, quarterly,
        yearly, custom. Default: one_time.
    status:
        Bill status. One of: pending, paid, overdue. Default: pending.
    payment_method:
        Payment method description.
    account_id:
        UUID of linked financial account.
    statement_period_start:
        Start of the billing statement period.
    statement_period_end:
        End of the billing statement period.
    paid_at:
        Timestamp when the bill was paid (for status=paid transitions).
    source_message_id:
        Source email or provider message ID for provenance.
    metadata:
        Arbitrary JSON metadata for extended attributes.

    Returns
    -------
    dict
        BillRecord with all persisted fields.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of {_VALID_STATUSES}")
    if frequency not in _VALID_FREQUENCIES:
        raise ValueError(f"Invalid frequency {frequency!r}. Must be one of {_VALID_FREQUENCIES}")

    due = _normalize_date(due_date)
    period_start = _normalize_date(statement_period_start) if statement_period_start else None
    period_end = _normalize_date(statement_period_end) if statement_period_end else None
    metadata_json = json.dumps(metadata) if metadata is not None else "{}"
    account_uuid = uuid.UUID(str(account_id)) if account_id is not None else None

    # Normalize paid_at to datetime if provided as string
    paid_at_dt: datetime | None = None
    if paid_at is not None:
        if isinstance(paid_at, str):
            paid_at_dt = datetime.fromisoformat(paid_at)
        else:
            paid_at_dt = paid_at

    # Upsert: match on (payee, due_date)
    existing = await pool.fetchrow(
        "SELECT id FROM bills WHERE payee = $1 AND due_date = $2 LIMIT 1",
        payee,
        due,
    )

    if existing is not None:
        row = await pool.fetchrow(
            """
            UPDATE bills
            SET
                amount                = $1,
                currency              = $2,
                frequency             = $3,
                status                = $4,
                payment_method        = COALESCE($5, payment_method),
                account_id            = COALESCE($6, account_id),
                statement_period_start = COALESCE($7, statement_period_start),
                statement_period_end   = COALESCE($8, statement_period_end),
                paid_at               = COALESCE($9, paid_at),
                source_message_id     = COALESCE($10, source_message_id),
                metadata              = metadata || $11::jsonb,
                updated_at            = now()
            WHERE id = $12
            RETURNING *
            """,
            amount,
            currency,
            frequency,
            status,
            payment_method,
            account_uuid,
            period_start,
            period_end,
            paid_at_dt,
            source_message_id,
            metadata_json,
            existing["id"],
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO bills (
                payee, amount, currency, due_date, frequency, status,
                payment_method, account_id, statement_period_start,
                statement_period_end, paid_at, source_message_id, metadata
            )
            VALUES (
                $1, $2, $3, $4, $5, $6,
                $7, $8, $9,
                $10, $11, $12, $13::jsonb
            )
            RETURNING *
            """,
            payee,
            amount,
            currency,
            due,
            frequency,
            status,
            payment_method,
            account_uuid,
            period_start,
            period_end,
            paid_at_dt,
            source_message_id,
            metadata_json,
        )

    return _deserialize_row(row)


async def upcoming_bills(
    pool: asyncpg.Pool,
    days_ahead: int = _DEFAULT_DAYS_AHEAD,
    include_overdue: bool = False,
) -> dict[str, Any]:
    """Query bills due within the requested horizon with urgency classification.

    Bills that are due within ``days_ahead`` days from today are included.
    Optionally includes already-overdue obligations (status='overdue' or
    due_date < today and status='pending').

    Urgency classification:
    - ``due_today``: due_date == today
    - ``due_soon``: due_date is within the horizon but not today
    - ``overdue``: status is 'overdue' OR due_date < today and status='pending'

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    days_ahead:
        Number of days from today to include. Default: 14.
    include_overdue:
        Whether to include already-overdue bills. Default: False.

    Returns
    -------
    dict
        UpcomingBillsResponse with ``as_of``, ``window_days``, ``items``,
        and ``totals``.
    """
    now = datetime.now(UTC)
    today = now.date()
    horizon = today + timedelta(days=days_ahead)

    if include_overdue:
        rows = await pool.fetch(
            """
            SELECT * FROM bills
            WHERE
                (due_date >= $1 AND due_date <= $2 AND status IN ('pending', 'overdue'))
                OR status = 'overdue'
                OR (due_date < $1 AND status = 'pending')
            ORDER BY due_date ASC, payee ASC
            """,
            today,
            horizon,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT * FROM bills
            WHERE due_date >= $1 AND due_date <= $2 AND status IN ('pending', 'overdue')
            ORDER BY due_date ASC, payee ASC
            """,
            today,
            horizon,
        )

    items: list[dict[str, Any]] = []
    total_due_soon = 0
    total_overdue = 0
    amount_due = Decimal("0.00")

    for row in rows:
        bill = _deserialize_row(row)
        due = row["due_date"]
        bill_status = row["status"]

        is_overdue = bill_status == "overdue" or (due < today and bill_status == "pending")
        urgency = _urgency(due, today, is_overdue)
        days_until_due = (due - today).days

        items.append(
            {
                "bill": bill,
                "urgency": urgency,
                "days_until_due": days_until_due,
            }
        )

        if urgency == "overdue":
            total_overdue += 1
        else:
            total_due_soon += 1

        try:
            amount_due += Decimal(str(row["amount"]))
        except Exception:
            pass

    return {
        "as_of": now.isoformat(),
        "window_days": days_ahead,
        "items": items,
        "totals": {
            "due_soon": total_due_soon,
            "overdue": total_overdue,
            "amount_due": str(amount_due),
        },
    }
