"""Finance transactions — record and query transaction ledger entries."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid as _uuid_mod
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _log_activity, _row_to_dict

logger = logging.getLogger(__name__)

# Maximum rows accepted by bulk_record_transactions in a single call.
_MAX_BULK_TRANSACTIONS = 500


def _is_uuid(value: str) -> bool:
    """Return True if *value* is a valid UUID string."""
    try:
        _uuid_mod.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


async def _resolve_account_id(pool: asyncpg.Pool, raw: str | None) -> str | None:
    """Resolve a human-readable account identifier to its UUID.

    Accepts a UUID string (returned as-is) or a fuzzy identifier that is
    matched against ``accounts.name``, ``accounts.institution``, or
    ``accounts.last_four``.  Returns ``None`` when *raw* is ``None`` or
    when no matching account is found.

    Raises :class:`ValueError` with an actionable hint when a non-UUID
    identifier matches zero or multiple accounts.
    """
    if raw is None:
        return None
    if _is_uuid(raw):
        return raw

    # Try exact match on name, then institution, then last_four.
    row = await pool.fetchrow(
        """
        SELECT id FROM accounts
        WHERE name = $1 OR institution = $1 OR last_four = $1
        LIMIT 2
        """,
        raw,
    )
    if row is None:
        # Try case-insensitive ILIKE on name and institution.
        rows = await pool.fetch(
            """
            SELECT id FROM accounts
            WHERE name ILIKE $1 OR institution ILIKE $1
            LIMIT 2
            """,
            f"%{raw}%",
        )
        if len(rows) == 1:
            return str(rows[0]["id"])
        if len(rows) > 1:
            raise ValueError(
                f"account_id '{raw}' is ambiguous — matched multiple accounts. "
                "Pass the account UUID instead. List accounts with list_accounts()."
            )
        raise ValueError(
            f"account_id '{raw}' is not a valid UUID and no matching account was "
            "found by name, institution, or last_four. "
            "List accounts with list_accounts() and pass the UUID."
        )
    return str(row["id"])


# Module-level cache for _has_column results.
# Keyed by id(pool) so different pools (e.g. test fixtures with different schemas)
# don't pollute each other.  Plain dict because asyncpg.Pool does not support
# weak references.
_column_existence_cache: dict[int, dict[tuple[str, str], bool]] = {}

# Module-level cache for _has_table results, mirroring _column_existence_cache.
# Avoids an information_schema round-trip per insert (hot on bulk imports).
_table_existence_cache: dict[int, dict[str, bool]] = {}


async def _mirror_to_spo(
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
) -> None:
    """Fire-and-forget SPO mirror write to shared.facts after a primary insert.

    Writes a bitemporal fact with predicate='transaction_{direction}' to
    public.facts via record_transaction_fact.  Errors are swallowed so that
    a mirror failure never rolls back the primary finance.transactions insert.

    This function is scheduled via asyncio.ensure_future and must never raise.
    """
    try:
        from butlers.tools.finance.facts import record_transaction_fact

        await record_transaction_fact(
            pool=pool,
            posted_at=posted_at,
            merchant=merchant,
            amount=amount,
            currency=currency,
            category=category,
            description=description,
            payment_method=payment_method,
            account_id=account_id,
            receipt_url=receipt_url,
            external_ref=external_ref,
            source_message_id=source_message_id,
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "_mirror_to_spo: SPO mirror write failed for merchant=%r posted_at=%r; "
            "primary insert is unaffected",
            merchant,
            posted_at,
            exc_info=True,
        )


def _infer_direction(amount: Decimal | float | int) -> str:
    """Infer transaction direction from amount sign.

    Negative amounts are debits (money out); positive amounts are credits
    (money in / refunds).
    """
    return "credit" if Decimal(str(amount)) >= 0 else "debit"


def _normalize_direction(direction: str | None) -> str | None:
    """Normalize an explicit direction value and reject invalid inputs."""
    if direction is None:
        return None
    normalized = direction.strip().lower()
    if normalized not in {"debit", "credit"}:
        raise ValueError("direction must be 'debit' or 'credit'")
    return normalized


def _coerce_signed_amount(
    amount: Decimal | float | int,
    direction: str | None = None,
) -> tuple[Decimal, str]:
    """Return a signed Decimal amount plus its effective debit/credit direction."""
    decimal_amount = Decimal(str(amount))
    normalized_direction = _normalize_direction(direction)
    if normalized_direction == "debit":
        return (-abs(decimal_amount), normalized_direction)
    if normalized_direction == "credit":
        return (abs(decimal_amount), normalized_direction)
    return (decimal_amount, _infer_direction(decimal_amount))


def _normalize_amount(amount: Decimal | float | int) -> Decimal:
    """Return absolute value of amount as Decimal(14,2)."""
    return abs(Decimal(str(amount)))


async def _deduplicate(pool: asyncpg.Pool, txn: dict[str, Any]) -> str | None:
    """Check whether *txn* already exists in ``finance.transactions``.

    Applies a three-tier deduplication strategy in strict priority order:

    **Priority 1 — external_id + account_id** (most reliable)
        Used when a bank API supplies a stable, bank-assigned transaction ID.
        Requires both ``external_id`` and ``account_id`` to be non-None in *txn*,
        and the ``external_id`` column must exist in the schema (added in
        ``finance_002``).

    **Priority 2 — source_message_id** (email / notification provenance)
        Used when a transaction was extracted from a single source message
        (e.g. an email receipt).  A match on ``source_message_id`` alone is
        sufficient to identify a duplicate because the same message should never
        produce two distinct transactions in the ledger.
        Requires ``source_message_id`` to be non-None in *txn*.

    **Priority 3 — composite fallback** (cross-source / account-scoped entry)
        Always attempted as a last resort when Priorities 1–2 found no match.
        Matches on transaction day, ``amount``, and ``merchant``; when
        ``account_id`` is available it is included to keep the match scoped to
        one account. This catches cross-source duplicates where the same
        real-world transaction arrives from different channels with different
        ``source_message_id`` values without collapsing repeated manual entries
        that have no provenance key or account linkage.

    Parameters
    ----------
    pool:
        asyncpg connection pool (must be connected to the schema that contains
        ``finance.transactions``).
    txn:
        A dict-like object whose keys map to transaction fields.  Expected keys
        (all optional; missing or ``None`` values are treated as absent):

        - ``external_id`` (*str*) — bank-assigned stable ID
        - ``account_id`` (*str | UUID*) — FK to ``finance.accounts``
        - ``source_message_id`` (*str*) — originating email/message ID
        - ``posted_at`` (*datetime*) — transaction post timestamp
        - ``amount`` (*Decimal | float | int*) — raw amount (sign ignored;
          stored absolute value is compared)
        - ``merchant`` (*str*) — merchant / payee name

    Returns
    -------
    str | None
        The ``id`` of the existing transaction as a string if a duplicate is
        found, or ``None`` if the transaction appears to be new.
    """
    external_id: str | None = txn.get("external_id")
    account_id: str | None = txn.get("account_id")
    source_message_id: str | None = txn.get("source_message_id")
    posted_at: datetime | None = txn.get("posted_at")
    raw_amount = txn.get("amount")
    merchant: str | None = txn.get("merchant")

    # Normalise amount only when provided — guards against None / missing keys.
    stored_amount: Decimal | None = None
    if raw_amount is not None:
        try:
            stored_amount = _normalize_amount(raw_amount)
        except (InvalidOperation, TypeError):
            stored_amount = None

    # ------------------------------------------------------------------
    # Priority 1: (account_id, external_id)
    # ------------------------------------------------------------------
    if external_id is not None and account_id is not None:
        has_ext_id = await _has_column(pool, "transactions", "external_id")
        if has_ext_id:
            row = await pool.fetchrow(
                """
                SELECT id FROM transactions
                WHERE account_id = $1::uuid
                  AND external_id = $2
                """,
                str(account_id),
                external_id,
            )
            if row is not None:
                return str(row["id"])

    # ------------------------------------------------------------------
    # Priority 2: source_message_id
    # ------------------------------------------------------------------
    if source_message_id is not None:
        row = await pool.fetchrow(
            """
            SELECT id FROM transactions
            WHERE source_message_id = $1
            """,
            source_message_id,
        )
        if row is not None:
            return str(row["id"])

    # ------------------------------------------------------------------
    # Priority 3: composite fallback (same-day posted_at + amount + merchant)
    # Runs when P1/P2 did not find a match and we still have enough provenance
    # to avoid collapsing user-entered duplicates. That means either:
    # - account_id is present (CSV/bank-import style matching), or
    # - source_message_id is present (cross-source duplicates with different
    #   message ids but the same real-world transaction).
    # ------------------------------------------------------------------
    if (
        posted_at is not None
        and stored_amount is not None
        and merchant is not None
        and (account_id is not None or source_message_id is not None)
    ):
        row = None
        if account_id is not None:
            row = await pool.fetchrow(
                """
                SELECT id FROM transactions
                WHERE account_id = $1::uuid
                  AND posted_at >= date_trunc('day', $2::timestamptz)
                  AND posted_at < date_trunc('day', $2::timestamptz) + INTERVAL '1 day'
                  AND amount = $3
                  AND merchant = $4
                """,
                str(account_id),
                posted_at,
                stored_amount,
                merchant,
            )
        elif source_message_id is not None:
            row = await pool.fetchrow(
                """
                SELECT id FROM transactions
                WHERE account_id IS NULL
                  AND posted_at >= date_trunc('day', $1::timestamptz)
                  AND posted_at < date_trunc('day', $1::timestamptz) + INTERVAL '1 day'
                  AND amount = $2
                  AND merchant = $3
                """,
                posted_at,
                stored_amount,
                merchant,
            )
        if row is not None:
            return str(row["id"])

    return None


async def _has_column(pool: asyncpg.Pool, table: str, column: str) -> bool:
    """Return True if the given table has the named column in the current schema.

    Results are cached for the lifetime of the process to avoid repeated
    ``information_schema`` queries on every deduplication call.
    """
    per_pool = _column_existence_cache.setdefault(id(pool), {})
    cache_key = (table, column)
    if cache_key in per_pool:
        return per_pool[cache_key]
    count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = $1 AND column_name = $2
        """,
        table,
        column,
    )
    result = bool(count)
    per_pool[cache_key] = result
    return result


async def _has_table(pool: asyncpg.Pool, table: str) -> bool:
    """Return True if the given table exists in the current schema.

    Results are cached per pool for the lifetime of the process to avoid
    repeated ``information_schema`` queries on every insert / dedup call
    (hot during bulk imports).
    """
    per_pool = _table_existence_cache.setdefault(id(pool), {})
    if table in per_pool:
        return per_pool[table]
    count = await pool.fetchval(
        """
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = current_schema() AND table_name = $1
        """,
        table,
    )
    result = bool(count)
    per_pool[table] = result
    return result


async def _lookup_merchant_category(pool: asyncpg.Pool, merchant: str) -> str | None:
    """Look up a merchant in finance.merchant_mappings via ILIKE pattern matching.

    Returns the mapped category string or None if no mapping is found.
    Only used when the merchant_mappings table exists.
    """
    has_mm = await _has_table(pool, "merchant_mappings")
    if not has_mm:
        return None
    row = await pool.fetchrow(
        """
        SELECT category FROM merchant_mappings
        WHERE is_active = true
          AND lower($1) LIKE lower(raw_pattern)
        ORDER BY confidence DESC, updated_at DESC
        LIMIT 1
        """,
        merchant,
    )
    if row is None:
        return None
    return row["category"]


async def _resolve_category_for_insert(
    pool: asyncpg.Pool,
    category: str | None,
    metadata: dict[str, Any],
) -> tuple[str, bool]:
    """Return a category value that is valid for the current schema.

    Older test and development schemas allow free-form ``transactions.category``
    text. Migrated schemas enforce ``transactions.category -> categories.name``;
    in those schemas an LLM-supplied free-form category should not leak a raw FK
    violation from the tool layer. Unknown categories are preserved in metadata
    and stored under the canonical ``uncategorized`` bucket.
    """
    candidate = str(category or "").strip() or "uncategorized"
    if not await _has_table(pool, "categories"):
        return candidate, False

    row = await pool.fetchrow(
        """
        SELECT name FROM categories
        WHERE lower(name) = lower($1)
        LIMIT 1
        """,
        candidate,
    )
    if row is not None:
        return row["name"], False

    fallback = await pool.fetchrow(
        """
        SELECT name FROM categories
        WHERE name = 'uncategorized'
        LIMIT 1
        """
    )
    if fallback is None:
        return candidate, False

    metadata.setdefault("original_category", candidate)
    warning = {
        "code": "unknown_category",
        "field": "category",
        "stored_as": fallback["name"],
    }
    existing_warnings = metadata.get("warnings")
    if isinstance(existing_warnings, list):
        # Assign a new list rather than appending in place: ``metadata`` is only
        # a shallow copy of the caller's dict, so its list values may be shared.
        metadata["warnings"] = [*existing_warnings, warning]
    elif existing_warnings is None:
        metadata["warnings"] = [warning]
    else:
        metadata["warnings"] = [existing_warnings, warning]

    return fallback["name"], True


async def _record_correction(
    pool_or_conn: Any,
    transaction_id: str,
    field_name: str,
    old_value: Any,
    new_value: Any,
    reason: str | None = None,
    source: str = "manual",
) -> None:
    """Insert a row into transaction_corrections if the table exists.

    Silently skips when the corrections table is absent (pre-migration schema).
    """
    try:
        has_corrections = await _has_table(pool_or_conn, "transaction_corrections")
        if not has_corrections:
            return
        await pool_or_conn.execute(
            """
            INSERT INTO transaction_corrections (
                transaction_id, field_name, old_value, new_value, reason, source
            ) VALUES ($1::uuid, $2, $3, $4, $5, $6)
            """,
            transaction_id,
            field_name,
            str(old_value) if old_value is not None else None,
            str(new_value) if new_value is not None else None,
            reason,
            source,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "_record_correction: failed to log correction for txn %s field %s",
            transaction_id,
            field_name,
            exc_info=True,
        )


async def record_transaction(
    pool: asyncpg.Pool,
    posted_at: datetime,
    merchant: str,
    amount: Decimal | float | int,
    currency: str,
    category: str,
    direction: str | None = None,
    description: str | None = None,
    payment_method: str | None = None,
    account_id: str | None = None,
    receipt_url: str | None = None,
    external_ref: str | None = None,
    source_message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    """Record a transaction in the finance.transactions ledger.

    Direction is inferred from the amount sign when not provided:
    - Negative amount  -> debit  (money out)
    - Positive amount  -> credit (money in / refund)

    When ``direction`` is provided, it overrides the amount sign and coerces the
    transaction into the requested debit/credit semantics.

    Deduplication is checked via a tiered key hierarchy in priority order:
    1. (account_id, external_id) — for bank APIs with stable IDs
    2. (source_message_id, merchant, amount, posted_at) — for email-extracted
    3. (account_id, posted_at, amount, merchant) — composite fallback for CSV imports

    When a duplicate is found, the existing record is returned without inserting.

    When category is 'uncategorized' (or not provided), the merchant is looked
    up in finance.merchant_mappings for auto-categorization.

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
    direction:
        Optional explicit direction override: ``"debit"`` or ``"credit"``.
        When provided, it takes precedence over the amount sign.
    currency:
        ISO-4217 currency code (e.g. ``"USD"``).
    category:
        Transaction category (e.g. ``"groceries"``, ``"subscriptions"``).
        Pass ``"uncategorized"`` to trigger auto-categorization via merchant mappings.
    description:
        Optional free-text description.
    payment_method:
        Optional payment method (e.g. ``"Amex"``, ``"PayPal"``).
    account_id:
        Optional UUID string of the linked finance.accounts row.
    receipt_url:
        Optional URL to receipt or invoice.
    external_ref:
        Optional external provider transaction ID (legacy field).
    source_message_id:
        Source email or message ID, used for deduplication.
    metadata:
        Optional free-form JSONB metadata dict.
    external_id:
        Stable external transaction ID from bank APIs (used for Priority-1 dedup).

    Returns
    -------
    dict
        Full TransactionRecord dict. When an enabled ``large_transaction`` alert
        is configured (via ``alert_configure``) and the recorded amount exceeds
        its threshold, the dict additionally carries a ``large_transaction_alert``
        key: ``{threshold, amount, merchant, exceeds_by}``.
    """
    # Resolve human-readable account identifiers to UUID.
    account_id = await _resolve_account_id(pool, account_id)

    signed_amount, effective_direction = _coerce_signed_amount(amount, direction)
    stored_amount = _normalize_amount(signed_amount)

    # --- Tiered deduplication via _deduplicate() ---
    txn_dict: dict[str, Any] = {
        "external_id": external_id,
        "account_id": account_id,
        "source_message_id": source_message_id,
        "posted_at": posted_at,
        "amount": signed_amount,
        "merchant": merchant,
    }
    existing_id = await _deduplicate(pool, txn_dict)
    if existing_id is not None:
        existing = await pool.fetchrow(
            "SELECT * FROM transactions WHERE id = $1::uuid",
            existing_id,
        )
        if existing is not None:
            return _row_to_dict(existing)

    has_external_id = await _has_column(pool, "transactions", "external_id")

    # --- Auto-categorization via merchant mappings ---
    effective_category = category
    category_source = "manual"
    if category in ("uncategorized", "") or category is None:
        mapped_category = await _lookup_merchant_category(pool, merchant)
        if mapped_category is not None:
            effective_category = mapped_category
            category_source = "auto"
        else:
            effective_category = "uncategorized"
            category_source = "manual"

    meta_dict = dict(metadata or {})
    effective_category, used_category_fallback = await _resolve_category_for_insert(
        pool,
        effective_category,
        meta_dict,
    )
    if used_category_fallback:
        category_source = "manual"

    # Check for optional new columns from finance_002 migration.
    has_category_source = await _has_column(pool, "transactions", "category_source")

    # Build the INSERT with explicit casts to avoid IndeterminateDatatypeError.
    # We always include the 13 base columns; optional columns are appended.
    extra_cols: list[str] = []
    extra_vals: list[str] = []
    extra_params: list[Any] = []
    param_idx = 14  # base params occupy $1-$13

    if has_external_id and external_id is not None:
        extra_cols.append("external_id")
        extra_vals.append(f"${param_idx}::text")
        extra_params.append(external_id)
        param_idx += 1

    if has_category_source:
        extra_cols.append("category_source")
        extra_vals.append(f"${param_idx}::text")
        extra_params.append(category_source)
        param_idx += 1

    cols_clause = ", ".join(extra_cols)
    vals_clause = ", ".join(extra_vals)
    extra_cols_sql = f", {cols_clause}" if extra_cols else ""
    extra_vals_sql = f", {vals_clause}" if extra_vals else ""

    is_fresh_insert = True
    try:
        row = await pool.fetchrow(
            f"""
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
                {extra_cols_sql}
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9,
                $10::uuid, $11, $12, $13
                {extra_vals_sql}
            )
            RETURNING *
            """,
            source_message_id,
            posted_at,
            merchant,
            description,
            stored_amount,
            currency.upper(),
            effective_direction,
            effective_category,
            payment_method,
            account_id,
            receipt_url,
            external_ref,
            meta_dict,
            *extra_params,
        )
    except asyncpg.UniqueViolationError:
        # Race condition: another insert beat us to it; return the existing row.
        is_fresh_insert = False
        row = None
        if source_message_id is not None:
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
        if row is None and has_external_id and external_id is not None and account_id is not None:
            row = await pool.fetchrow(
                """
                SELECT * FROM transactions
                WHERE account_id = $1::uuid AND external_id = $2
                """,
                account_id,
                external_id,
            )
        if row is None and account_id is not None and source_message_id is None:
            source_filter = "AND source_message_id IS NULL"
            if has_external_id:
                source_filter += " AND external_id IS NULL"
            row = await pool.fetchrow(
                f"""
                SELECT * FROM transactions
                WHERE account_id = $1::uuid
                  AND posted_at = $2
                  AND amount = $3
                  AND merchant = $4
                  {source_filter}
                """,
                account_id,
                posted_at,
                stored_amount,
                merchant,
            )
        if row is None:
            raise

    await _log_activity(
        pool,
        "transaction_recorded",
        (
            f"Recorded {effective_direction} transaction: "
            f"{merchant} {stored_amount} {currency.upper()}"
        ),
        entity_type="transaction",
        entity_id=str(row["id"]),
    )

    # Fire-and-forget SPO mirror write to public.facts.  Scheduled as a
    # background task so failures never roll back the primary insert.
    asyncio.create_task(
        _mirror_to_spo(
            pool=pool,
            posted_at=posted_at,
            merchant=merchant,
            amount=signed_amount,
            currency=currency,
            category=effective_category,
            description=description,
            payment_method=payment_method,
            account_id=account_id,
            receipt_url=receipt_url,
            external_ref=external_ref,
            source_message_id=source_message_id,
            metadata=meta_dict,
        )
    )

    result = _row_to_dict(row)

    # --- Bill reconciliation hook (Track C / bu-y6gpw) ---
    # Synchronous and in-process for fresh debit inserts only.  The try/except
    # makes this best-effort: any reconciliation failure is logged but never
    # propagates to the caller — the primary insert is always returned intact.
    if is_fresh_insert and effective_direction == "debit":
        try:
            from butlers.tools.finance.reconciliation import (  # noqa: PLC0415
                _settle_bill,
                match_transaction_to_bills,
            )

            # Ensure posted_at is timezone-aware (defensive: callers should pass
            # TZ-aware datetimes, but guard against naive inputs so that
            # astimezone(UTC) inside match_transaction_to_bills never silently
            # shifts the date by assuming the system local timezone).
            _posted_at_tz = (
                posted_at if posted_at.tzinfo is not None else posted_at.replace(tzinfo=UTC)
            )

            _txn_for_match: dict[str, Any] = {
                "id": str(row["id"]),
                "direction": effective_direction,
                "merchant": merchant,
                "currency": currency.upper(),
                "amount": stored_amount,  # keep Decimal; reconciliation converts internally
                "posted_at": _posted_at_tz,
                "metadata": meta_dict,
            }
            _match = await match_transaction_to_bills(pool, _txn_for_match)
            _tier = _match.get("tier", "none")

            if _tier == "auto_settle":
                _bill = _match["bill"]
                _settled = await _settle_bill(
                    pool,
                    _bill["id"],
                    {
                        "id": str(row["id"]),
                        "amount": stored_amount,  # keep Decimal; _settle_bill converts internally
                        "posted_at": _posted_at_tz,
                        "payment_method": payment_method,
                    },
                )
                if _settled:
                    result["bill_reconciliation"] = {
                        "auto_settled": {
                            "bill_id": str(_bill["id"]),
                            "payee": _bill["payee"],
                            "amount": float(stored_amount),
                            "paid_at": _posted_at_tz.isoformat(),
                            "txn_id": str(row["id"]),
                        }
                    }
            elif _tier == "confirm":
                _candidates = _match.get("candidates", [])
                if _candidates:
                    result["bill_reconciliation"] = {
                        "candidates": [
                            {
                                "bill_id": str(_c["id"]),
                                "payee": _c["payee"],
                                "due_date": (
                                    _c["due_date"].isoformat()
                                    if hasattr(_c["due_date"], "isoformat")
                                    else str(_c["due_date"])
                                ),
                                "amount": float(_c["amount"]),
                            }
                            for _c in _candidates
                        ]
                    }
        except Exception:  # noqa: BLE001
            logger.warning(
                "record_transaction: bill reconciliation hook failed for txn %s merchant=%r; "
                "primary insert is unaffected",
                row["id"],
                merchant,
                exc_info=True,
            )

    # --- Large transaction alert flag (finance-alerts spec) ---
    # If an enabled `large_transaction` alert is configured and the recorded
    # amount exceeds its threshold, surface a `large_transaction_alert` flag in
    # the response. Best-effort: the threshold lookup queries the facts table,
    # which is not present in every caller's schema, so any failure is swallowed
    # and never affects the primary insert.
    try:
        from butlers.tools.finance.alerts import (  # noqa: PLC0415
            evaluate_large_transaction_alert,
            get_large_transaction_alert_config,
        )

        _alert_config = await get_large_transaction_alert_config(pool)
        _alert = evaluate_large_transaction_alert(stored_amount, merchant, _alert_config)
        if _alert is not None:
            result["large_transaction_alert"] = _alert
    except Exception:  # noqa: BLE001
        logger.debug(
            "record_transaction: large_transaction alert evaluation failed for txn %s "
            "merchant=%r; primary insert is unaffected",
            row["id"],
            merchant,
            exc_info=True,
        )

    return result


async def list_transactions(
    pool: asyncpg.Pool,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    category: str | None = None,
    merchant: str | None = None,
    account_id: str | None = None,
    min_amount: Decimal | float | int | None = None,
    max_amount: Decimal | float | int | None = None,
    direction: str | None = None,
    tags: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a paginated, filtered list of transactions.

    All filter parameters are optional and combined with AND. Results are
    sorted by posted_at DESC. Soft-deleted transactions (deleted_at IS NOT NULL)
    are always excluded.

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
    direction:
        Filter by direction: 'debit' or 'credit'. None returns both.
    tags:
        Filter by tags array containment (requires tags column from finance_002).
        Transactions that contain ALL provided tags are returned.
    limit:
        Page size (default 50, max 500).
    offset:
        Number of rows to skip (default 0).

    Returns
    -------
    dict
        TransactionListResponse with keys: items, total, limit, offset.
    """
    # Resolve human-readable account identifiers to UUID.
    account_id = await _resolve_account_id(pool, account_id)

    limit = min(max(1, limit), 500)
    offset = max(0, offset)

    if direction is not None and direction not in ("debit", "credit"):
        raise ValueError("direction must be 'debit' or 'credit'")

    # Check whether new columns exist (added in finance_002).
    has_deleted_at = await _has_column(pool, "transactions", "deleted_at")
    has_tags = await _has_column(pool, "transactions", "tags")

    conditions: list[str] = []
    # Always exclude soft-deleted rows when the column is present.
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

    if direction is not None:
        conditions.append(f"direction = ${idx}")
        params.append(direction)
        idx += 1

    if tags is not None and len(tags) > 0 and has_tags:
        # Array containment: transaction must have ALL provided tags
        conditions.append(f"tags @> ${idx}::text[]")
        params.append(tags)
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
    expected_version: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Update mutable fields on an existing transaction.

    Only provided (non-None) fields are updated; omitted fields retain their
    current values.

    When ``category`` is changed, ``category_source`` is set to ``'manual'``
    and ``is_category_locked`` is set to ``true`` (when those columns exist from
    finance_002 migration). This prevents future automatic re-categorization
    from overwriting manual corrections.

    When ``expected_version`` is provided and the ``version`` column exists,
    optimistic locking is enforced: the update fails with a conflict error if
    the row's current version does not match.

    Field changes are recorded in ``transaction_corrections`` when that table
    exists (requires finance_002 migration).

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
        Dict replacing the existing metadata JSONB field.
    expected_version:
        When provided and the version column exists, the UPDATE will only
        succeed if the current row version matches. Returns a conflict error
        if there is a mismatch (optimistic locking).
    reason:
        Optional human-readable reason for the update, recorded in corrections.

    Returns
    -------
    dict
        Updated TransactionRecord dict, or an error dict on failure.
    """
    # Check which optional columns exist (from finance_002 migration).
    has_version = await _has_column(pool, "transactions", "version")
    has_category_source = await _has_column(pool, "transactions", "category_source")
    has_is_category_locked = await _has_column(pool, "transactions", "is_category_locked")
    has_corrections = await _has_table(pool, "transaction_corrections")

    # Fetch current row for comparison (needed for correction logging and version check).
    current = await pool.fetchrow(
        "SELECT * FROM transactions WHERE id = $1::uuid",
        transaction_id,
    )
    if current is None:
        return {"error": "transaction_not_found", "transaction_id": transaction_id}

    # Optimistic locking check.
    if has_version and expected_version is not None:
        current_version = current["version"]
        if current_version != expected_version:
            return {
                "error": "version_conflict",
                "transaction_id": transaction_id,
                "expected_version": expected_version,
                "current_version": current_version,
            }

    # Build SET clause dynamically.
    sets: list[str] = ["updated_at = now()"]
    params: list[Any] = []
    idx = 1

    # Track which fields are being changed for correction logging.
    changed_fields: dict[str, tuple[Any, Any]] = {}  # field -> (old_val, new_val)

    if category is not None and str(current.get("category", "")) != category:
        sets.append(f"category = ${idx}")
        params.append(category)
        changed_fields["category"] = (current.get("category"), category)
        idx += 1
        # Set category_source = 'manual' and lock the category.
        if has_category_source:
            sets.append("category_source = 'manual'")
        if has_is_category_locked:
            sets.append("is_category_locked = true")

    if merchant is not None and str(current.get("merchant", "")) != merchant:
        sets.append(f"merchant = ${idx}")
        params.append(merchant)
        changed_fields["merchant"] = (current.get("merchant"), merchant)
        idx += 1

    if description is not None and current.get("description") != description:
        sets.append(f"description = ${idx}")
        params.append(description)
        changed_fields["description"] = (current.get("description"), description)
        idx += 1

    if metadata is not None:
        sets.append(f"metadata = ${idx}")
        params.append(metadata)
        changed_fields["metadata"] = (None, metadata)  # Don't log full metadata diffs
        idx += 1

    # Increment version when version column exists.
    if has_version:
        sets.append("version = version + 1")

    if len(sets) == 1:
        # Nothing to update beyond the timestamp; just return current row.
        return _row_to_dict(current)

    # Build WHERE clause with optional version lock.
    if has_version and expected_version is not None:
        where_extra = f" AND version = ${idx}"
        params.append(expected_version)
        idx += 1
    else:
        where_extra = ""

    params.append(transaction_id)
    set_clause = ", ".join(sets)
    row = await pool.fetchrow(
        f"UPDATE transactions SET {set_clause} WHERE id = ${idx}::uuid{where_extra} RETURNING *",
        *params,
    )
    if row is None:
        # Could happen if version changed between our fetch and update (race condition).
        if has_version and expected_version is not None:
            return {
                "error": "version_conflict",
                "transaction_id": transaction_id,
                "expected_version": expected_version,
            }
        return {"error": "transaction_not_found", "transaction_id": transaction_id}

    # Record corrections for each changed field.
    if has_corrections:
        for field_name, (old_val, new_val) in changed_fields.items():
            if field_name != "metadata":  # Skip metadata diffs to keep corrections clean
                await _record_correction(
                    pool,
                    transaction_id,
                    field_name,
                    old_val,
                    new_val,
                    reason=reason,
                    source="manual",
                )

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

    The ``version`` column is incremented on soft-delete when it exists
    (from finance_002 migration).

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
    has_deleted_at = await _has_column(pool, "transactions", "deleted_at")
    if not has_deleted_at:
        return {"error": "soft_delete_not_supported", "transaction_id": transaction_id}

    has_version = await _has_column(pool, "transactions", "version")
    version_clause = ", version = version + 1" if has_version else ""

    row = await pool.fetchrow(
        f"""
        UPDATE transactions
        SET deleted_at = COALESCE(deleted_at, now()),
            updated_at = now()
            {version_clause}
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
    duplicate_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Merge duplicate transactions, keeping one canonical record and soft-deleting the rest.

    For each duplicate:
    - Sets ``is_duplicate = true`` and ``duplicate_of = keep_id`` (when those columns exist)
    - Soft-deletes the duplicate (``deleted_at = now()``)
    - Records a correction entry for the audit trail

    The ``metadata`` of all discarded records is deep-merged into the kept record
    before deletion. The kept record's ``updated_at`` is refreshed.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    keep_id:
        UUID string of the transaction to keep (canonical record).
    duplicate_ids:
        List of UUID strings of transactions to mark as duplicates and soft-delete.

    Returns
    -------
    dict
        Updated TransactionRecord dict for the kept transaction, or
        ``{"error": ..., "keep_id": ..., "duplicate_ids": ...}`` on failure.
    """
    # Resolve the list of IDs to discard.
    if duplicate_ids is not None:
        ids_to_discard = list(duplicate_ids)
    else:
        return {
            "error": "must provide duplicate_ids",
            "keep_id": keep_id,
            "duplicate_ids": [],
        }

    if not ids_to_discard:
        return {
            "error": "duplicate_ids must not be empty",
            "keep_id": keep_id,
            "duplicate_ids": [],
        }

    if keep_id in ids_to_discard:
        return {
            "error": "keep_id must not appear in duplicate_ids",
            "keep_id": keep_id,
            "duplicate_ids": ids_to_discard,
        }

    has_deleted_at = await _has_column(pool, "transactions", "deleted_at")
    if not has_deleted_at:
        return {
            "error": "soft_delete_not_supported",
            "keep_id": keep_id,
            "duplicate_ids": ids_to_discard,
        }

    has_is_duplicate = await _has_column(pool, "transactions", "is_duplicate")
    has_duplicate_of = await _has_column(pool, "transactions", "duplicate_of")
    has_corrections = await _has_table(pool, "transaction_corrections")

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
                    "duplicate_ids": ids_to_discard,
                }

            # Fetch all discard rows and validate.
            discard_rows: list[asyncpg.Record] = []
            for did in ids_to_discard:
                row = await conn.fetchrow(
                    "SELECT * FROM transactions WHERE id = $1::uuid AND deleted_at IS NULL",
                    did,
                )
                if row is None:
                    return {
                        "error": "discard_transaction_not_found",
                        "keep_id": keep_id,
                        "duplicate_ids": ids_to_discard,
                        "missing_id": did,
                    }
                discard_rows.append(row)

            # Deep-merge metadata: keep's values win on conflict.
            def _parse_row_meta(val: Any) -> dict[str, Any]:
                if val is None:
                    return {}
                if isinstance(val, str):
                    return json.loads(val)
                return dict(val)

            merged_meta = _parse_row_meta(keep_row["metadata"])
            for discard_row in discard_rows:
                discard_meta = _parse_row_meta(discard_row["metadata"])
                # keep's values win: merge discard first, then overlay keep
                merged_meta = {**discard_meta, **merged_meta}

            updated_row = await conn.fetchrow(
                """
                UPDATE transactions
                SET metadata = $1,
                    updated_at = now()
                WHERE id = $2::uuid
                RETURNING *
                """,
                merged_meta,
                keep_id,
            )

            # Soft-delete each duplicate with is_duplicate / duplicate_of flags.
            for did in ids_to_discard:
                if has_is_duplicate and has_duplicate_of:
                    await conn.execute(
                        """
                        UPDATE transactions
                        SET deleted_at = COALESCE(deleted_at, now()),
                            updated_at = now(),
                            is_duplicate = true,
                            duplicate_of = $2::uuid
                        WHERE id = $1::uuid
                        """,
                        did,
                        keep_id,
                    )
                elif has_is_duplicate:
                    await conn.execute(
                        """
                        UPDATE transactions
                        SET deleted_at = COALESCE(deleted_at, now()),
                            updated_at = now(),
                            is_duplicate = true
                        WHERE id = $1::uuid
                        """,
                        did,
                    )
                elif has_duplicate_of:
                    await conn.execute(
                        """
                        UPDATE transactions
                        SET deleted_at = COALESCE(deleted_at, now()),
                            updated_at = now(),
                            duplicate_of = $2::uuid
                        WHERE id = $1::uuid
                        """,
                        did,
                        keep_id,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE transactions
                        SET deleted_at = COALESCE(deleted_at, now()),
                            updated_at = now()
                        WHERE id = $1::uuid
                        """,
                        did,
                    )

                # Record correction for audit trail.
                if has_corrections:
                    await _record_correction(
                        conn,
                        keep_id,
                        "merge",
                        None,
                        did,
                        reason=f"merged duplicate {did} into {keep_id}",
                        source="manual",
                    )

    await _log_activity(
        pool,
        "transaction_merged",
        f"Merged {len(ids_to_discard)} duplicate(s) into {keep_id}",
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

    Each child transaction has ``metadata.split_from`` set to the original
    transaction's ID. Corrections are recorded in ``transaction_corrections``
    when that table exists (requires finance_002 migration).

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
    has_deleted_at = await _has_column(pool, "transactions", "deleted_at")
    has_corrections = await _has_table(pool, "transaction_corrections")
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

            # Parse original metadata (defensive: may be str or dict from asyncpg).
            orig_meta: dict[str, Any]
            raw_meta = original["metadata"]
            if isinstance(raw_meta, str):
                orig_meta = json.loads(raw_meta) if raw_meta else {}
            else:
                orig_meta = dict(raw_meta or {})

            # Insert split records with metadata.split_from set.
            inserted: list[dict[str, Any]] = []
            for s in parsed_splits:
                child_meta = dict(orig_meta)
                child_meta["split_from"] = str(original["id"])

                row = await conn.fetchrow(
                    """
                    INSERT INTO transactions (
                        account_id, source_message_id, posted_at, merchant,
                        description, amount, currency, direction, category,
                        payment_method, receipt_url, external_ref, metadata
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
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
                    child_meta,
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

            # Record correction for audit trail.
            if has_corrections:
                child_ids = [s["id"] for s in inserted]
                await _record_correction(
                    conn,
                    transaction_id,
                    "split",
                    None,
                    json.dumps(child_ids),
                    reason=f"split into {len(inserted)} records",
                    source="manual",
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
    create_rule: bool = False,
) -> dict[str, Any]:
    """Reassign category for all transactions matching a merchant pattern (ILIKE).

    Excludes soft-deleted transactions and category-locked transactions
    (``is_category_locked = true`` when that column exists from finance_002).
    When ``dry_run=True``, returns a preview of affected transactions without
    modifying them.

    When ``create_rule=True``, upserts a ``finance.merchant_mappings`` row
    mapping the pattern to ``new_category`` after the update.

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
    create_rule:
        When ``True``, upserts a merchant mapping rule for future auto-categorization.

    Returns
    -------
    dict
        ``{matched, updated, dry_run, create_rule, sample_transactions}``
        where ``updated`` is 0 when ``dry_run=True``.
    """
    has_deleted_at = await _has_column(pool, "transactions", "deleted_at")
    has_is_category_locked = await _has_column(pool, "transactions", "is_category_locked")
    has_corrections = await _has_table(pool, "transaction_corrections")

    deleted_cond = "AND deleted_at IS NULL" if has_deleted_at else ""
    locked_cond = (
        "AND (is_category_locked IS NULL OR is_category_locked = false)"
        if has_is_category_locked
        else ""
    )

    sample_rows = await pool.fetch(
        f"""
        SELECT * FROM transactions
        WHERE lower(merchant) LIKE lower($1)
          {deleted_cond}
          {locked_cond}
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
          {locked_cond}
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
              {locked_cond}
            """,
            new_category,
            merchant_pattern,
        )
        # asyncpg execute() returns "UPDATE N" string
        try:
            updated = int(result.split()[-1])
        except (IndexError, ValueError):
            updated = matched

        # Record corrections for bulk recategorize (log one entry representing the batch).
        if updated > 0 and has_corrections:
            await _record_correction(
                pool,
                "00000000-0000-0000-0000-000000000000",  # sentinel for bulk ops
                "bulk_recategorize",
                merchant_pattern,
                new_category,
                reason=f"bulk_recategorize: {updated} transactions updated",
                source="bulk",
            )

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

        # Upsert merchant mapping rule when create_rule=True and at least one transaction matched.
        if create_rule and updated > 0:
            try:
                has_mm = await _has_table(pool, "merchant_mappings")
                if has_mm:
                    normalized_merchant = merchant_pattern.replace("%", "").replace("_", "").strip()
                    existing = await pool.fetchrow(
                        """
                        SELECT id, learned_from_count
                        FROM merchant_mappings
                        WHERE is_active = true
                          AND lower(raw_pattern) = lower($1)
                        LIMIT 1
                        """,
                        merchant_pattern,
                    )
                    if existing is None:
                        await pool.execute(
                            """
                            INSERT INTO merchant_mappings (
                                raw_pattern,
                                normalized_merchant,
                                category,
                                confidence,
                                learned_from_count,
                                source
                            )
                            VALUES ($1, $2, $3, 1.0, $4, 'manual')
                            """,
                            merchant_pattern,
                            normalized_merchant or merchant_pattern,
                            new_category,
                            int(updated),
                        )
                    else:
                        await pool.execute(
                            """
                            UPDATE merchant_mappings
                            SET normalized_merchant = $2,
                                category = $3,
                                confidence = 1.0,
                                learned_from_count = COALESCE(learned_from_count, 0) + $4,
                                source = 'manual',
                                updated_at = now()
                            WHERE id = $1::uuid
                            """,
                            str(existing["id"]),
                            normalized_merchant or merchant_pattern,
                            new_category,
                            int(updated),
                        )
            except Exception:
                logger.warning(
                    "bulk_recategorize: merchant mapping rule upsert failed for pattern %r",
                    merchant_pattern,
                    exc_info=True,
                )

    return {
        "matched": matched,
        "updated": updated,
        "dry_run": dry_run,
        "create_rule": create_rule,
        "sample_transactions": [_row_to_dict(r) for r in sample_rows],
    }


async def bulk_record_transactions(
    pool: asyncpg.Pool,
    transactions: list[dict[str, Any]],
    account_id: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Bulk-ingest normalized transaction objects.

    Routes each row through ``record_transaction()`` to obtain per-row
    deduplication (source_message_id uniqueness) and the SPO mirror write
    to public.facts.

    Args:
        pool: Database connection pool.
        transactions: List of normalized transaction dicts.  Each must have:
            posted_at (ISO 8601 str or datetime), merchant (str),
            amount (str decimal or numeric).
            Optional: currency, category, description, payment_method,
            account_id (per-row override), receipt_url, external_ref,
            source_message_id, metadata.
        account_id: Top-level account_id inherited by all rows unless a
            per-row account_id is set.
        source: Stored as import_source in each row's metadata.

    Returns:
        {total, imported, skipped, errors, error_details, large_transaction_alerts}
        error_details items have: {index, reason}
        reason is "duplicate" for dedup skips, "invalid_date" for
        unparseable dates, "invalid_amount" for non-numeric amounts.
        large_transaction_alerts lists rows whose amount exceeded the configured
        `large_transaction` alert threshold, each as
        {index, threshold, amount, merchant, exceeds_by}.
    """
    if len(transactions) > _MAX_BULK_TRANSACTIONS:
        raise ValueError(
            f"Batch too large: {len(transactions)} exceeds maximum of {_MAX_BULK_TRANSACTIONS}"
        )

    from datetime import UTC

    imported = 0
    skipped = 0
    errors = 0
    error_details: list[dict[str, Any]] = []
    large_transaction_alerts: list[dict[str, Any]] = []

    for idx, txn in enumerate(transactions):
        # ------------------------------------------------------------------
        # 1. Parse and validate required fields
        # ------------------------------------------------------------------
        try:
            raw_posted_at = txn.get("posted_at")
            if not raw_posted_at:
                raise ValueError("missing posted_at")
            if isinstance(raw_posted_at, datetime):
                posted_at = raw_posted_at
            else:
                posted_at = datetime.fromisoformat(str(raw_posted_at))
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            errors += 1
            error_details.append({"index": idx, "reason": "invalid_date"})
            continue

        try:
            raw_amount = txn.get("amount")
            if raw_amount is None:
                raise ValueError("missing amount")
            amount_decimal = Decimal(str(raw_amount))
        except (ValueError, TypeError, InvalidOperation):
            errors += 1
            error_details.append({"index": idx, "reason": "invalid_amount"})
            continue

        merchant = txn.get("merchant")
        if not merchant:
            errors += 1
            error_details.append({"index": idx, "reason": "missing_merchant"})
            continue

        # ------------------------------------------------------------------
        # 2. Resolve per-row fields
        # ------------------------------------------------------------------
        effective_account_id: str | None = txn.get("account_id") or account_id
        currency = (txn.get("currency") or "USD").upper()
        category = txn.get("category") or "uncategorized"
        description = txn.get("description")
        payment_method = txn.get("payment_method")
        direction = txn.get("direction")
        source_message_id = txn.get("source_message_id")
        receipt_url = txn.get("receipt_url")
        external_ref = txn.get("external_ref")
        external_id = txn.get("external_id")
        extra_metadata: dict[str, Any] = dict(txn.get("metadata") or {})
        if source is not None:
            extra_metadata.setdefault("import_source", source)

        # ------------------------------------------------------------------
        # 3. Route through record_transaction for dedup + SPO mirror
        # ------------------------------------------------------------------
        try:
            _recorded = await record_transaction(
                pool=pool,
                posted_at=posted_at,
                merchant=merchant,
                amount=amount_decimal,
                currency=currency,
                category=category,
                direction=direction,
                description=description,
                payment_method=payment_method,
                account_id=effective_account_id,
                receipt_url=receipt_url,
                external_ref=external_ref,
                source_message_id=source_message_id,
                external_id=external_id,
                metadata=extra_metadata if extra_metadata else None,
            )
            imported += 1
            _lta = _recorded.get("large_transaction_alert")
            if _lta is not None:
                large_transaction_alerts.append({"index": idx, **_lta})
        except asyncpg.UniqueViolationError:
            skipped += 1
            error_details.append({"index": idx, "reason": "duplicate"})
        except asyncpg.PostgresError as exc:
            logger.warning("bulk_record_transactions: row %d failed: %s", idx, exc)
            errors += 1
            error_details.append({"index": idx, "reason": f"db_error: {exc}"})

    return {
        "total": len(transactions),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "error_details": error_details,
        "large_transaction_alerts": large_transaction_alerts,
    }
