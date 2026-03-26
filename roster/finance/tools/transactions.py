"""Finance transactions — record and query transaction ledger entries."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _log_activity, _row_to_dict

logger = logging.getLogger(__name__)


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


async def update_transaction(
    pool: asyncpg.Pool,
    transaction_id: str,
    category: str | None = None,
    merchant: str | None = None,
    description: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update mutable fields on an existing transaction.

    Only provided (non-None) fields are updated; omitted fields retain their
    current values. When ``category`` is changed, ``merchant_mappings`` is
    refreshed via ``learn_merchant_categories()`` so future suggestions reflect
    the corrected mapping.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    transaction_id:
        UUID string of the transaction to update.
    category:
        New category to assign. Triggers merchant mapping refresh when provided.
    merchant:
        Updated merchant name.
    description:
        Updated free-text description.
    metadata:
        Dict merged into (or replacing) the existing metadata JSONB field.

    Returns
    -------
    dict
        Updated TransactionRecord dict, or ``{"error": ..., "transaction_id": ...}``
        when the transaction is not found.
    """
    # "updated_at = now()" is included only when at least one real field is being
    # changed; the early-return branch below skips the UPDATE entirely for no-op calls.
    sets: list[str] = ["updated_at = now()"]
    params: list[Any] = []
    idx = 1

    if category is not None:
        sets.append(f"category = ${idx}")
        params.append(category)
        idx += 1

    if merchant is not None:
        sets.append(f"merchant = ${idx}")
        params.append(merchant)
        idx += 1

    if description is not None:
        sets.append(f"description = ${idx}")
        params.append(description)
        idx += 1

    if metadata is not None:
        sets.append(f"metadata = ${idx}::jsonb")
        params.append(json.dumps(metadata))
        idx += 1

    if len(sets) == 1:
        # Nothing to update beyond the timestamp; just fetch and return current row.
        row = await pool.fetchrow(
            "SELECT * FROM transactions WHERE id = $1::uuid",
            transaction_id,
        )
        if row is None:
            return {"error": "transaction_not_found", "transaction_id": transaction_id}
        return _row_to_dict(row)

    params.append(transaction_id)
    set_clause = ", ".join(sets)
    row = await pool.fetchrow(
        f"UPDATE transactions SET {set_clause} WHERE id = ${idx}::uuid RETURNING *",
        *params,
    )
    if row is None:
        return {"error": "transaction_not_found", "transaction_id": transaction_id}

    await _log_activity(
        pool,
        "transaction_updated",
        f"Updated transaction {transaction_id}",
        entity_type="transaction",
        entity_id=transaction_id,
    )

    # Category feedback loop: when category is changed, refresh merchant_mappings
    # so future suggest_categories() calls reflect the corrected assignment.
    if category is not None:
        try:
            from butlers.tools.finance.pattern_recognition import learn_merchant_categories

            await learn_merchant_categories(pool)
        except Exception:
            # Best-effort: do not fail the update if mapping refresh fails.
            logger.warning(
                "update_transaction: merchant mapping refresh failed for transaction %s",
                transaction_id,
                exc_info=True,
            )

    return _row_to_dict(row)
