"""Finance transactions — record and query transaction ledger entries."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
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

    # Check whether the transactions table has a deleted_at column; guard
    # against schemas that predate migration finance_004.
    has_deleted_at = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'transactions' AND column_name = 'deleted_at'
        """
    )

    conditions: list[str] = []
    if has_deleted_at:
        conditions.append("deleted_at IS NULL")
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


async def delete_transaction(
    pool: asyncpg.Pool,
    transaction_id: str,
) -> dict[str, Any]:
    """Soft-delete a transaction by setting ``deleted_at`` to now().

    Deleted transactions are excluded from all queries and analytics.
    Deletion is idempotent: calling again on an already-deleted transaction
    returns the existing record unchanged.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    transaction_id:
        UUID string of the transaction to soft-delete.

    Returns
    -------
    dict
        Updated TransactionRecord dict with ``deleted_at`` set, or
        ``{"error": "transaction_not_found", "transaction_id": ...}``
        when the transaction does not exist.
    """
    has_deleted_at = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'transactions' AND column_name = 'deleted_at'
        """
    )
    if not has_deleted_at:
        return {"error": "soft_delete_not_supported", "transaction_id": transaction_id}

    row = await pool.fetchrow(
        """
        UPDATE transactions
        SET deleted_at = COALESCE(deleted_at, now()),
            updated_at = now()
        WHERE id = $1::uuid
        RETURNING *
        """,
        transaction_id,
    )
    if row is None:
        return {"error": "transaction_not_found", "transaction_id": transaction_id}

    await _log_activity(
        pool,
        "transaction_deleted",
        f"Soft-deleted transaction {transaction_id}",
        entity_type="transaction",
        entity_id=transaction_id,
    )
    return _row_to_dict(row)


async def merge_duplicates(
    pool: asyncpg.Pool,
    keep_id: str,
    discard_id: str,
) -> dict[str, Any]:
    """Merge two duplicate transactions, keeping one and soft-deleting the other.

    The ``metadata`` of the discarded record is deep-merged into the kept
    record before the discard record is soft-deleted.  The kept record's
    ``updated_at`` is refreshed.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    keep_id:
        UUID string of the transaction to keep.
    discard_id:
        UUID string of the transaction to soft-delete.

    Returns
    -------
    dict
        Updated TransactionRecord dict for the kept transaction, or
        ``{"error": ..., "keep_id": ..., "discard_id": ...}`` on failure.
    """
    if keep_id == discard_id:
        return {
            "error": "keep_id and discard_id must be different",
            "keep_id": keep_id,
            "discard_id": discard_id,
        }

    has_deleted_at = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'transactions' AND column_name = 'deleted_at'
        """
    )
    if not has_deleted_at:
        return {
            "error": "soft_delete_not_supported",
            "keep_id": keep_id,
            "discard_id": discard_id,
        }

    async with pool.acquire() as conn:
        async with conn.transaction():
            keep_row = await conn.fetchrow(
                "SELECT * FROM transactions WHERE id = $1::uuid AND deleted_at IS NULL",
                keep_id,
            )
            if keep_row is None:
                return {
                    "error": "keep_transaction_not_found",
                    "keep_id": keep_id,
                    "discard_id": discard_id,
                }

            discard_row = await conn.fetchrow(
                "SELECT * FROM transactions WHERE id = $1::uuid AND deleted_at IS NULL",
                discard_id,
            )
            if discard_row is None:
                return {
                    "error": "discard_transaction_not_found",
                    "keep_id": keep_id,
                    "discard_id": discard_id,
                }

            # Deep-merge metadata: keep's values win on conflict.
            # asyncpg may return JSONB as a string or a dict depending on codec registration.
            def _parse_row_meta(val: Any) -> dict[str, Any]:
                if val is None:
                    return {}
                if isinstance(val, str):
                    return json.loads(val)
                return dict(val)

            keep_meta = _parse_row_meta(keep_row["metadata"])
            discard_meta = _parse_row_meta(discard_row["metadata"])
            merged_meta = {**discard_meta, **keep_meta}

            updated_row = await conn.fetchrow(
                """
                UPDATE transactions
                SET metadata = $1::jsonb,
                    updated_at = now()
                WHERE id = $2::uuid
                RETURNING *
                """,
                json.dumps(merged_meta),
                keep_id,
            )

            await conn.execute(
                """
                UPDATE transactions
                SET deleted_at = COALESCE(deleted_at, now()),
                    updated_at = now()
                WHERE id = $1::uuid
                """,
                discard_id,
            )

    await _log_activity(
        pool,
        "transaction_merged",
        f"Merged transaction {discard_id} into {keep_id}",
        entity_type="transaction",
        entity_id=keep_id,
    )
    return _row_to_dict(updated_row)


async def split_transaction(
    pool: asyncpg.Pool,
    transaction_id: str,
    splits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Split a single transaction into multiple records with different amounts/categories.

    All split amounts must sum to the original transaction's stored amount
    (absolute value, NUMERIC precision).  The original transaction is
    soft-deleted after the split records are inserted.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    transaction_id:
        UUID string of the transaction to split.
    splits:
        List of split objects.  Each must contain:

        - ``amount`` (str or numeric): Split amount as a decimal string.
          Must be positive (absolute value); direction is inherited from
          the original transaction.
        - ``category`` (str): Category for this split.
        - ``description`` (str, optional): Description override for this split.

    Returns
    -------
    dict
        ``{"original_id": ..., "splits": [<TransactionRecord>, ...]}``
        or ``{"error": ..., "transaction_id": ...}`` on failure.
    """
    # Check whether the transactions table has a deleted_at column.
    has_deleted_at = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'transactions' AND column_name = 'deleted_at'
        """
    )
    not_deleted_cond = "AND deleted_at IS NULL" if has_deleted_at else ""

    async with pool.acquire() as conn:
        async with conn.transaction():
            original = await conn.fetchrow(
                f"SELECT * FROM transactions WHERE id = $1::uuid {not_deleted_cond}",
                transaction_id,
            )
            if original is None:
                return {
                    "error": "transaction_not_found",
                    "transaction_id": transaction_id,
                }

            if not splits:
                return {
                    "error": "splits must not be empty",
                    "transaction_id": transaction_id,
                }

            # Validate and parse split amounts.
            parsed_splits: list[dict[str, Any]] = []
            total_split = Decimal("0")
            for i, s in enumerate(splits):
                raw_amount = s.get("amount")
                if raw_amount is None:
                    return {
                        "error": f"split[{i}] missing required field 'amount'",
                        "transaction_id": transaction_id,
                    }
                category = s.get("category")
                if not category:
                    return {
                        "error": f"split[{i}] missing required field 'category'",
                        "transaction_id": transaction_id,
                    }
                try:
                    amt = abs(Decimal(str(raw_amount)))
                except InvalidOperation:
                    return {
                        "error": f"split[{i}] invalid amount: {raw_amount!r}",
                        "transaction_id": transaction_id,
                    }
                total_split += amt
                parsed_splits.append(
                    {
                        "amount": amt,
                        "category": category,
                        "description": s.get("description"),
                    }
                )

            original_amount = Decimal(str(original["amount"]))
            if total_split != original_amount:
                return {
                    "error": (
                        f"split amounts sum to {total_split} "
                        f"but original amount is {original_amount}"
                    ),
                    "transaction_id": transaction_id,
                }

            # Insert split records.
            inserted: list[dict[str, Any]] = []
            for s in parsed_splits:
                row = await conn.fetchrow(
                    """
                    INSERT INTO transactions (
                        account_id, source_message_id, posted_at, merchant,
                        description, amount, currency, direction, category,
                        payment_method, receipt_url, external_ref, metadata
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb
                    )
                    RETURNING *
                    """,
                    original["account_id"],
                    original["source_message_id"],
                    original["posted_at"],
                    original["merchant"],
                    s["description"] or original["description"],
                    s["amount"],
                    original["currency"],
                    original["direction"],
                    s["category"],
                    original["payment_method"],
                    original["receipt_url"],
                    original["external_ref"],
                    json.dumps(
                        json.loads(original["metadata"])
                        if isinstance(original["metadata"], str)
                        else dict(original["metadata"] or {})
                    ),
                )
                inserted.append(_row_to_dict(row))

            # Soft-delete the original (only when column exists).
            if has_deleted_at:
                await conn.execute(
                    """
                    UPDATE transactions
                    SET deleted_at = now(), updated_at = now()
                    WHERE id = $1::uuid
                    """,
                    transaction_id,
                )

    await _log_activity(
        pool,
        "transaction_split",
        f"Split transaction {transaction_id} into {len(inserted)} records",
        entity_type="transaction",
        entity_id=transaction_id,
    )
    return {"original_id": transaction_id, "splits": inserted}


async def bulk_recategorize(
    pool: asyncpg.Pool,
    merchant_pattern: str,
    new_category: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Reassign category for all transactions matching a merchant pattern (ILIKE).

    Excludes soft-deleted transactions.  When ``dry_run=True``, returns a
    preview of affected transactions without modifying them.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    merchant_pattern:
        ILIKE pattern (e.g. ``"%Netflix%"``, ``"Starbucks"``).
    new_category:
        Target category to assign.
    dry_run:
        When ``True``, returns matching transactions without updating them.

    Returns
    -------
    dict
        ``{matched, updated, dry_run, sample_transactions}``
        where ``updated`` is 0 when ``dry_run=True``.
    """
    # Guard: check if deleted_at column exists.
    has_deleted_at = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'transactions' AND column_name = 'deleted_at'
        """
    )
    deleted_cond = "AND deleted_at IS NULL" if has_deleted_at else ""

    sample_rows = await pool.fetch(
        f"""
        SELECT * FROM transactions
        WHERE lower(merchant) LIKE lower($1)
          {deleted_cond}
        ORDER BY posted_at DESC
        LIMIT 10
        """,
        merchant_pattern,
    )

    matched = await pool.fetchval(
        f"""
        SELECT COUNT(*) FROM transactions
        WHERE lower(merchant) LIKE lower($1)
          {deleted_cond}
        """,
        merchant_pattern,
    )

    updated = 0
    if not dry_run:
        result = await pool.execute(
            f"""
            UPDATE transactions
            SET category = $1, updated_at = now()
            WHERE lower(merchant) LIKE lower($2)
              {deleted_cond}
            """,
            new_category,
            merchant_pattern,
        )
        # asyncpg execute() returns "UPDATE N" string
        try:
            updated = int(result.split()[-1])
        except (IndexError, ValueError):
            updated = matched

        # Refresh merchant mappings after bulk category change.
        if updated > 0:
            try:
                from butlers.tools.finance.pattern_recognition import learn_merchant_categories

                await learn_merchant_categories(pool)
            except Exception:
                logger.warning(
                    "bulk_recategorize: merchant mapping refresh failed",
                    exc_info=True,
                )

    return {
        "matched": matched,
        "updated": updated,
        "dry_run": dry_run,
        "sample_transactions": [_row_to_dict(r) for r in sample_rows],
    }
