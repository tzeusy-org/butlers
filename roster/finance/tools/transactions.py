"""Finance transactions — record and query transaction ledger entries."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _log_activity, _row_to_dict


def _infer_direction(amount: Decimal | float | int) -> str:
    """Infer transaction direction from amount sign.

    Negative amounts are debits (money out); positive amounts are credits
    (money in / refunds).
    """
    return "credit" if Decimal(str(amount)) >= 0 else "debit"


def _normalize_amount(amount: Decimal | float | int) -> Decimal:
    """Return absolute value of amount as Decimal(14,2)."""
    return abs(Decimal(str(amount)))


async def record_transaction(
    pool: asyncpg.Pool,
    posted_at: datetime,
    merchant: str,
    amount: Decimal | float | int,
    currency: str,
    category: str,
    description: str | None = None,
    payment_method: str | None = None,
    account_id: str | None = None,
    receipt_url: str | None = None,
    external_ref: str | None = None,
    source_message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a transaction in the finance.transactions ledger.

    Direction is inferred from the amount sign when not provided:
    - Negative amount  -> debit  (money out)
    - Positive amount  -> credit (money in / refund)

    When ``source_message_id`` is provided, dedupe is enforced via the
    unique partial index on (source_message_id, merchant, amount, posted_at).
    Duplicate inserts return the existing record rather than raising.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    posted_at:
        Timestamp when the transaction was posted (timezone-aware preferred).
    merchant:
        Merchant or payee name.
    amount:
        Transaction amount. Negative means debit, positive means credit.
        Stored as absolute value in NUMERIC(14,2).
    currency:
        ISO-4217 currency code (e.g. ``"USD"``).
    category:
        Transaction category (e.g. ``"groceries"``, ``"subscriptions"``).
    description:
        Optional free-text description.
    payment_method:
        Optional payment method (e.g. ``"Amex"``, ``"PayPal"``).
    account_id:
        Optional UUID string of the linked finance.accounts row.
    receipt_url:
        Optional URL to receipt or invoice.
    external_ref:
        Optional external provider transaction ID.
    source_message_id:
        Source email or message ID, used for deduplication.
    metadata:
        Optional free-form JSONB metadata dict.

    Returns
    -------
    dict
        Full TransactionRecord dict.
    """
    direction = _infer_direction(amount)
    stored_amount = _normalize_amount(amount)
    meta_json = json.dumps(metadata or {})

    # Attempt dedupe-safe insert; on conflict return existing row.
    if source_message_id is not None:
        existing = await pool.fetchrow(
            """
            SELECT * FROM transactions
            WHERE source_message_id = $1
              AND merchant = $2
              AND amount = $3
              AND posted_at = $4
            """,
            source_message_id,
            merchant,
            stored_amount,
            posted_at,
        )
        if existing is not None:
            return _row_to_dict(existing)

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO transactions (
                source_message_id,
                posted_at,
                merchant,
                description,
                amount,
                currency,
                direction,
                category,
                payment_method,
                account_id,
                receipt_url,
                external_ref,
                metadata
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10::uuid, $11, $12, $13::jsonb
            )
            RETURNING *
            """,
            source_message_id,
            posted_at,
            merchant,
            description,
            stored_amount,
            currency.upper(),
            direction,
            category,
            payment_method,
            account_id,
            receipt_url,
            external_ref,
            meta_json,
        )
    except asyncpg.UniqueViolationError:
        # Race condition: another insert beat us to it; return the existing row.
        row = await pool.fetchrow(
            """
            SELECT * FROM transactions
            WHERE source_message_id = $1
              AND merchant = $2
              AND amount = $3
              AND posted_at = $4
            """,
            source_message_id,
            merchant,
            stored_amount,
            posted_at,
        )
        if row is None:
            raise

    await _log_activity(
        pool,
        "transaction_recorded",
        f"Recorded {direction} transaction: {merchant} {stored_amount} {currency.upper()}",
        entity_type="transaction",
        entity_id=str(row["id"]),
    )
    return _row_to_dict(row)


async def list_transactions(
    pool: asyncpg.Pool,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    category: str | None = None,
    merchant: str | None = None,
    account_id: str | None = None,
    min_amount: Decimal | float | int | None = None,
    max_amount: Decimal | float | int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a paginated, filtered list of transactions.

    All filter parameters are optional and combined with AND. Results are
    sorted by posted_at DESC.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    start_date:
        Inclusive lower bound on posted_at.
    end_date:
        Inclusive upper bound on posted_at.
    category:
        Exact category match.
    merchant:
        Case-insensitive prefix/substring match on merchant.
    account_id:
        Filter by linked account UUID.
    min_amount:
        Minimum absolute transaction amount (inclusive).
    max_amount:
        Maximum absolute transaction amount (inclusive).
    limit:
        Page size (default 50, max 500).
    offset:
        Number of rows to skip (default 0).

    Returns
    -------
    dict
        TransactionListResponse with keys: items, total, limit, offset.
    """
    limit = min(max(1, limit), 500)
    offset = max(0, offset)

    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if start_date is not None:
        conditions.append(f"posted_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"posted_at <= ${idx}")
        params.append(end_date)
        idx += 1

    if category is not None:
        conditions.append(f"category = ${idx}")
        params.append(category)
        idx += 1

    if merchant is not None:
        conditions.append(f"lower(merchant) LIKE lower(${idx})")
        params.append(f"%{merchant}%")
        idx += 1

    if account_id is not None:
        conditions.append(f"account_id = ${idx}::uuid")
        params.append(account_id)
        idx += 1

    if min_amount is not None:
        conditions.append(f"amount >= ${idx}")
        params.append(Decimal(str(min_amount)))
        idx += 1

    if max_amount is not None:
        conditions.append(f"amount <= ${idx}")
        params.append(Decimal(str(max_amount)))
        idx += 1

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    # Count total matching rows.
    count_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS total FROM transactions {where_clause}",
        *params,
    )
    total = count_row["total"]

    # Fetch page.
    rows = await pool.fetch(
        f"""
        SELECT * FROM transactions
        {where_clause}
        ORDER BY posted_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        limit,
        offset,
    )

    return {
        "items": [_row_to_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }
