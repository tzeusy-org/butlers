"""Finance butler fact-layer — SPO-backed CRUD for transactions, accounts,
subscriptions, and bills.

These functions mirror the CRUD interface of the original table-backed tools but
persist data as bitemporal facts in the memory.facts table anchored to the
owner entity.

Temporal predicates (valid_at = posted_at, never supersede):
  - transaction_debit   — money-out transaction
  - transaction_credit  — money-in / refund transaction

Property predicates (valid_at IS NULL, supersession on same predicate + content-key):
  - account      — registered financial account
  - subscription — recurring subscription
  - bill         — payable obligation

NUMERIC precision
-----------------
All monetary amounts are stored as string-encoded values in metadata JSONB so
NUMERIC(14,2) precision is never degraded through floating-point conversion.

Deduplication
-------------
For transactions, source_message_id-based deduplication is preserved:
before inserting a new temporal fact, the code checks whether an active fact
with the same (entity_id, predicate, valid_at) AND the same source_message_id
already exists. When found, the existing fact_id is returned without inserting.

Bulk ingestion
--------------
bulk_record_transactions() provides high-throughput ingestion without calling
embedding_engine.embed() per row. A zero vector is stored as the embedding
placeholder while search_vector (tsvector) is still computed. Composite
idempotency keys are used for rows without source_message_id.

Spending summary
----------------
spending_summary_facts() aggregates debit transaction facts via JSONB extraction
on the facts table, returning the same shape as the original spending_summary().
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from decimal import InvalidOperation as DecimalInvalidOperation
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Predicate constants
# ---------------------------------------------------------------------------

_PREDICATE_TRANSACTION_DEBIT = "transaction_debit"
_PREDICATE_TRANSACTION_CREDIT = "transaction_credit"
_PREDICATE_ACCOUNT = "account"
_PREDICATE_SUBSCRIPTION = "subscription"
_PREDICATE_BILL = "bill"

_TRANSACTION_PREDICATES = [_PREDICATE_TRANSACTION_DEBIT, _PREDICATE_TRANSACTION_CREDIT]

# ---------------------------------------------------------------------------
# Owner entity resolution (canonical: shared.entities WHERE 'owner' = ANY(roles))
# ---------------------------------------------------------------------------

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


async def _get_owner_entity_id(pool: asyncpg.Pool) -> uuid.UUID | None:
    """Resolve the owner entity's id from shared.entities.

    Uses shared.entities WHERE 'owner' = ANY(roles).  Returns None gracefully
    when the table does not exist yet or when no owner entity is present.
    """
    try:
        row = await pool.fetchrow(
            "SELECT id FROM shared.entities WHERE 'owner' = ANY(roles) LIMIT 1"
        )
        return row["id"] if row else None
    except asyncpg.PostgresError:
        logger.debug(
            "_get_owner_entity_id: shared.entities query failed (table may not exist yet)",
            exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _str_amount(amount: Decimal | float | int | str) -> str:
    """Normalize an amount to a string-encoded NUMERIC(14,2) representation."""
    return str(Decimal(str(amount)).quantize(Decimal("0.01")))


def _abs_decimal(amount: Decimal | float | int | str) -> Decimal:
    """Return the absolute value of an amount as a Decimal."""
    return abs(Decimal(str(amount)))


def _infer_direction(amount: Decimal | float | int) -> str:
    """Infer transaction direction from sign. Negative = debit, positive = credit."""
    return "credit" if Decimal(str(amount)) >= 0 else "debit"


def _current_month_bounds() -> tuple[date, date]:
    """Return (start, end) for the current calendar month."""
    today = datetime.now(UTC).date()
    start = today.replace(day=1)
    if today.month == 12:
        end = date(today.year + 1, 1, 1)
    else:
        end = date(today.year, today.month + 1, 1)
    return start, end - timedelta(days=1)


# ---------------------------------------------------------------------------
# store_fact wrapper (imports from memory module at call time)
# ---------------------------------------------------------------------------


async def _store_fact(
    pool: asyncpg.Pool,
    *,
    subject: str,
    predicate: str,
    content: str,
    scope: str,
    entity_id: uuid.UUID | None,
    valid_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
    permanence: str = "stable",
    idempotency_key: str | None = None,
) -> uuid.UUID:
    from butlers.modules.memory.storage import store_fact

    embedding_engine = _get_embedding_engine()
    return await store_fact(
        pool,
        subject=subject,
        predicate=predicate,
        content=content,
        embedding_engine=embedding_engine,
        permanence=permanence,
        scope=scope,
        entity_id=entity_id,
        valid_at=valid_at,
        metadata=metadata or {},
        idempotency_key=idempotency_key,
    )


# ---------------------------------------------------------------------------
# Transaction facts (temporal)
# ---------------------------------------------------------------------------


async def record_transaction_fact(
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
    """Record a transaction as a bitemporal fact anchored to the owner entity.

    Direction is inferred from amount sign:
      - Negative amount  -> transaction_debit  (money out)
      - Positive amount  -> transaction_credit (money in / refund)

    Deduplication: when source_message_id is provided, the function checks for
    an existing active fact with the same (entity_id, predicate, valid_at) and
    source_message_id in metadata. Duplicate inserts return the existing fact ID.

    Amount is stored as a string in metadata to preserve NUMERIC precision.
    valid_at is set to posted_at so the fact is bitemporal.

    Returns a dict with id, direction, merchant, amount, currency, category,
    posted_at, and all metadata fields.
    """
    direction = _infer_direction(amount)
    predicate = (
        _PREDICATE_TRANSACTION_DEBIT if direction == "debit" else _PREDICATE_TRANSACTION_CREDIT
    )
    stored_amount = _abs_decimal(amount)
    owner_entity_id = await _get_owner_entity_id(pool)

    content = f"{merchant} {_str_amount(stored_amount)} {currency.upper()}"

    # Deduplication check: same source_message_id + predicate + valid_at
    if source_message_id is not None and owner_entity_id is not None:
        try:
            existing = await pool.fetchrow(
                """
                SELECT id FROM facts
                WHERE entity_id = $1
                  AND predicate = $2
                  AND valid_at = $3
                  AND validity = 'active'
                  AND metadata->>'source_message_id' = $4
                LIMIT 1
                """,
                owner_entity_id,
                predicate,
                posted_at,
                source_message_id,
            )
            if existing is not None:
                return _transaction_fact_to_dict(
                    fact_id=str(existing["id"]),
                    direction=direction,
                    merchant=merchant,
                    amount=stored_amount,
                    currency=currency.upper(),
                    category=category,
                    posted_at=posted_at,
                    description=description,
                    payment_method=payment_method,
                    account_id=account_id,
                    receipt_url=receipt_url,
                    external_ref=external_ref,
                    source_message_id=source_message_id,
                    extra_metadata=metadata or {},
                )
        except asyncpg.PostgresError:
            # facts table may not exist yet; fall through to store_fact
            pass

    fact_metadata: dict[str, Any] = {
        "merchant": merchant,
        "amount": _str_amount(stored_amount),
        "currency": currency.upper(),
        "category": category,
        "direction": direction,
    }
    if description is not None:
        fact_metadata["description"] = description
    if payment_method is not None:
        fact_metadata["payment_method"] = payment_method
    if account_id is not None:
        fact_metadata["account_id"] = account_id
    if receipt_url is not None:
        fact_metadata["receipt_url"] = receipt_url
    if external_ref is not None:
        fact_metadata["external_ref"] = external_ref
    if source_message_id is not None:
        fact_metadata["source_message_id"] = source_message_id
    if metadata:
        fact_metadata.update(metadata)

    # Build a transaction-specific idempotency key that distinguishes
    # different transactions sharing the same timestamp and predicate.
    idem_parts = "|".join(
        [
            str(owner_entity_id) if owner_entity_id else "",
            predicate,
            posted_at.isoformat(),
            merchant,
            _str_amount(stored_amount),
            currency.upper(),
        ]
    )
    txn_idempotency_key = hashlib.sha256(idem_parts.encode()).hexdigest()[:32]

    fact_id = await _store_fact(
        pool,
        subject="owner",
        predicate=predicate,
        content=content,
        scope="finance",
        entity_id=owner_entity_id,
        valid_at=posted_at,
        metadata=fact_metadata,
        permanence="stable",
        idempotency_key=txn_idempotency_key,
    )

    return _transaction_fact_to_dict(
        fact_id=str(fact_id),
        direction=direction,
        merchant=merchant,
        amount=stored_amount,
        currency=currency.upper(),
        category=category,
        posted_at=posted_at,
        description=description,
        payment_method=payment_method,
        account_id=account_id,
        receipt_url=receipt_url,
        external_ref=external_ref,
        source_message_id=source_message_id,
        extra_metadata=metadata or {},
    )


def _transaction_fact_to_dict(
    *,
    fact_id: str,
    direction: str,
    merchant: str,
    amount: Decimal,
    currency: str,
    category: str,
    posted_at: datetime,
    description: str | None,
    payment_method: str | None,
    account_id: str | None,
    receipt_url: str | None,
    external_ref: str | None,
    source_message_id: str | None,
    extra_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": fact_id,
        "direction": direction,
        "merchant": merchant,
        "amount": str(amount),
        "currency": currency,
        "category": category,
        "posted_at": posted_at.isoformat() if isinstance(posted_at, datetime) else str(posted_at),
        "description": description,
        "payment_method": payment_method,
        "account_id": account_id,
        "receipt_url": receipt_url,
        "external_ref": external_ref,
        "source_message_id": source_message_id,
        "metadata": extra_metadata,
    }


async def list_transaction_facts(
    pool: asyncpg.Pool,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    category: str | None = None,
    merchant: str | None = None,
    account_id: str | None = None,
    min_amount: Decimal | float | int | None = None,
    max_amount: Decimal | float | int | None = None,
    direction: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Return a paginated, filtered list of transaction facts.

    Filters apply to metadata JSONB fields. Results are sorted by valid_at DESC.
    """
    limit = min(max(1, limit), 500)
    offset = max(0, offset)

    # Build predicate condition first to determine the $1 parameter type.
    if direction is not None:
        pred: Any = (
            _PREDICATE_TRANSACTION_DEBIT if direction == "debit" else _PREDICATE_TRANSACTION_CREDIT
        )
        predicate_condition = "predicate = $1"
    else:
        pred = _TRANSACTION_PREDICATES
        predicate_condition = "predicate = ANY($1)"

    conditions: list[str] = [
        predicate_condition,
        "validity = 'active'",
        "scope = 'finance'",
    ]
    params: list[Any] = [pred]
    idx = 2

    if start_date is not None:
        conditions.append(f"valid_at >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"valid_at <= ${idx}")
        params.append(end_date)
        idx += 1

    if category is not None:
        conditions.append(f"metadata->>'category' = ${idx}")
        params.append(category)
        idx += 1

    if merchant is not None:
        conditions.append(f"lower(metadata->>'merchant') LIKE lower(${idx})")
        params.append(f"%{merchant}%")
        idx += 1

    if account_id is not None:
        conditions.append(f"metadata->>'account_id' = ${idx}")
        params.append(account_id)
        idx += 1

    if min_amount is not None:
        conditions.append(f"(metadata->>'amount')::numeric >= ${idx}")
        params.append(Decimal(str(min_amount)))
        idx += 1

    if max_amount is not None:
        conditions.append(f"(metadata->>'amount')::numeric <= ${idx}")
        params.append(Decimal(str(max_amount)))
        idx += 1

    where = "WHERE " + " AND ".join(conditions)

    count_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS total FROM facts {where}",
        *params,
    )
    total = count_row["total"]

    rows = await pool.fetch(
        f"""
        SELECT id, predicate, content, valid_at, created_at, metadata
        FROM facts
        {where}
        ORDER BY valid_at DESC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        limit,
        offset,
    )

    return {
        "items": [_fact_row_to_transaction(dict(r)) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _fact_row_to_transaction(row: dict[str, Any]) -> dict[str, Any]:
    predicate = row.get("predicate", "")
    direction = "debit" if predicate == _PREDICATE_TRANSACTION_DEBIT else "credit"
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    posted_at = row.get("valid_at")
    created_at = row.get("created_at")
    # Overlay fields: prefer normalized_merchant / inferred_category when present
    merchant_display = meta.get("normalized_merchant") or meta.get("merchant", "")
    category_display = meta.get("inferred_category") or meta.get("category", "")
    return {
        "id": str(row["id"]),
        "direction": direction,
        "merchant": meta.get("merchant", ""),
        "normalized_merchant": meta.get("normalized_merchant"),
        "amount": meta.get("amount", "0.00"),
        "currency": meta.get("currency", "USD"),
        "category": meta.get("category", ""),
        "inferred_category": meta.get("inferred_category"),
        "display_merchant": merchant_display,
        "display_category": category_display,
        "posted_at": posted_at.isoformat() if posted_at else None,
        "description": meta.get("description"),
        "payment_method": meta.get("payment_method"),
        "account_id": meta.get("account_id"),
        "receipt_url": meta.get("receipt_url"),
        "external_ref": meta.get("external_ref"),
        "source_message_id": meta.get("source_message_id"),
        "created_at": created_at.isoformat() if created_at else None,
        "metadata": meta,
    }


# ---------------------------------------------------------------------------
# Account facts (property, supersession)
# ---------------------------------------------------------------------------


async def track_account_fact(
    pool: asyncpg.Pool,
    institution: str,
    type: str,
    currency: str = "USD",
    name: str | None = None,
    last_four: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a financial account as a property fact (supersession).

    Content format: '{institution} {type} ****{last_four}' (or '{institution} {type}'
    when last_four is unavailable).

    Because multiple accounts can coexist per person (different banks, different
    last_four), this function uses ``entity_id=None`` and encodes the account
    identity in the *subject* field: ``owner:account:{institution}:{type}:{last_four}``.
    Supersession then uses ``(subject, predicate)`` — the same account is updated
    in-place while different accounts remain independent active facts.

    Passing valid_at=None (default for store_fact) marks this as a property fact.
    """
    last_four_display = f"****{last_four}" if last_four else ""
    parts = [institution, type]
    if last_four_display:
        parts.append(last_four_display)
    content = " ".join(parts)

    # Unique subject per account so different accounts coexist as separate facts.
    # store_fact supersession key (without entity_id) is (subject, predicate).
    acct_key = last_four or "unknown"
    subject = f"owner:account:{institution}:{type}:{acct_key}"

    fact_metadata: dict[str, Any] = {
        "institution": institution,
        "type": type,
        "currency": currency,
    }
    if name is not None:
        fact_metadata["name"] = name
    if last_four is not None:
        fact_metadata["last_four"] = last_four
    if metadata:
        fact_metadata.update(metadata)

    fact_id = await _store_fact(
        pool,
        subject=subject,
        predicate=_PREDICATE_ACCOUNT,
        content=content,
        scope="finance",
        entity_id=None,  # entity_id=None → subject-keyed supersession
        valid_at=None,  # property fact — triggers supersession
        metadata=fact_metadata,
        permanence="stable",
    )

    return {
        "id": str(fact_id),
        "institution": institution,
        "type": type,
        "currency": currency,
        "name": name,
        "last_four": last_four,
        "content": content,
        "metadata": fact_metadata,
    }


# ---------------------------------------------------------------------------
# Subscription facts (property, supersession)
# ---------------------------------------------------------------------------


_VALID_SUBSCRIPTION_STATUSES = ("active", "cancelled", "paused")
_VALID_SUBSCRIPTION_FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly", "custom")


async def track_subscription_fact(
    pool: asyncpg.Pool,
    service: str,
    amount: float | Decimal,
    currency: str,
    frequency: str,
    next_renewal: str | date,
    status: str = "active",
    auto_renew: bool = True,
    payment_method: str | None = None,
    account_id: str | None = None,
    source_message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a subscription commitment as a property fact (supersession).

    Content format: '{service} {amount}/{frequency}'.
    Supersession key: (entity_id, scope='finance', predicate='subscription').
    Since content encodes the service name, different services get distinct facts.

    When an active fact with the same entity + scope + predicate + content
    already exists (same service), it is superseded with the new values.
    """
    if status not in _VALID_SUBSCRIPTION_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}. Must be one of {_VALID_SUBSCRIPTION_STATUSES}"
        )
    if frequency not in _VALID_SUBSCRIPTION_FREQUENCIES:
        raise ValueError(
            f"Invalid frequency {frequency!r}. Must be one of {_VALID_SUBSCRIPTION_FREQUENCIES}"
        )

    # Normalize renewal date
    if isinstance(next_renewal, str):
        renewal_date = date.fromisoformat(next_renewal)
    else:
        renewal_date = next_renewal

    stored_amount = _str_amount(amount)
    content = f"{service} {stored_amount}/{frequency}"

    # Unique subject per subscription service so different services coexist.
    # store_fact supersession key (without entity_id) is (subject, predicate).
    subject = f"owner:subscription:{service}"

    fact_metadata: dict[str, Any] = {
        "service": service,
        "amount": stored_amount,
        "currency": currency.upper(),
        "frequency": frequency,
        "next_renewal": renewal_date.isoformat(),
        "status": status,
        "auto_renew": auto_renew,
    }
    if payment_method is not None:
        fact_metadata["payment_method"] = payment_method
    if account_id is not None:
        fact_metadata["account_id"] = account_id
    if source_message_id is not None:
        fact_metadata["source_message_id"] = source_message_id
    if metadata:
        fact_metadata.update(metadata)

    fact_id = await _store_fact(
        pool,
        subject=subject,
        predicate=_PREDICATE_SUBSCRIPTION,
        content=content,
        scope="finance",
        entity_id=None,  # entity_id=None → subject-keyed supersession
        valid_at=None,  # property fact — supersession
        metadata=fact_metadata,
        permanence="stable",
    )

    return {
        "id": str(fact_id),
        "service": service,
        "amount": stored_amount,
        "currency": currency.upper(),
        "frequency": frequency,
        "next_renewal": renewal_date.isoformat(),
        "status": status,
        "auto_renew": auto_renew,
        "payment_method": payment_method,
        "account_id": account_id,
        "source_message_id": source_message_id,
        "metadata": fact_metadata,
    }


# ---------------------------------------------------------------------------
# Bill facts (property, supersession)
# ---------------------------------------------------------------------------


_VALID_BILL_STATUSES = ("pending", "paid", "overdue")
_VALID_BILL_FREQUENCIES = ("one_time", "weekly", "monthly", "quarterly", "yearly", "custom")


async def track_bill_fact(
    pool: asyncpg.Pool,
    payee: str,
    amount: float | Decimal,
    currency: str,
    due_date: str | date,
    frequency: str = "one_time",
    status: str = "pending",
    payment_method: str | None = None,
    account_id: str | None = None,
    paid_at: datetime | str | None = None,
    source_message_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create or update a bill obligation as a property fact (supersession).

    Content format: '{payee} {amount} due {due_date}'.
    Supersession key: (entity_id, scope='finance', predicate='bill').
    Since content encodes payee+due_date, the same bill (same payee + due date)
    supersedes the previous record, while different bills coexist.
    """
    if status not in _VALID_BILL_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of {_VALID_BILL_STATUSES}")
    if frequency not in _VALID_BILL_FREQUENCIES:
        raise ValueError(
            f"Invalid frequency {frequency!r}. Must be one of {_VALID_BILL_FREQUENCIES}"
        )

    # Normalize due_date
    if isinstance(due_date, str):
        due = date.fromisoformat(due_date)
    else:
        due = due_date

    # Normalize paid_at
    paid_at_dt: datetime | None = None
    if paid_at is not None:
        if isinstance(paid_at, str):
            paid_at_dt = datetime.fromisoformat(paid_at)
        else:
            paid_at_dt = paid_at

    stored_amount = _str_amount(amount)
    content = f"{payee} {stored_amount} due {due.isoformat()}"

    # Unique subject per bill (payee + due_date) so separate bills coexist.
    # store_fact supersession key (without entity_id) is (subject, predicate).
    subject = f"owner:bill:{payee}:{due.isoformat()}"

    fact_metadata: dict[str, Any] = {
        "payee": payee,
        "amount": stored_amount,
        "currency": currency.upper(),
        "due_date": due.isoformat(),
        "frequency": frequency,
        "status": status,
    }
    if payment_method is not None:
        fact_metadata["payment_method"] = payment_method
    if account_id is not None:
        fact_metadata["account_id"] = account_id
    if paid_at_dt is not None:
        fact_metadata["paid_at"] = paid_at_dt.isoformat()
    if source_message_id is not None:
        fact_metadata["source_message_id"] = source_message_id
    if metadata:
        fact_metadata.update(metadata)

    fact_id = await _store_fact(
        pool,
        subject=subject,
        predicate=_PREDICATE_BILL,
        content=content,
        scope="finance",
        entity_id=None,  # entity_id=None → subject-keyed supersession
        valid_at=None,  # property fact — supersession
        metadata=fact_metadata,
        permanence="stable",
    )

    return {
        "id": str(fact_id),
        "payee": payee,
        "amount": stored_amount,
        "currency": currency.upper(),
        "due_date": due.isoformat(),
        "frequency": frequency,
        "status": status,
        "payment_method": payment_method,
        "account_id": account_id,
        "paid_at": paid_at_dt.isoformat() if paid_at_dt else None,
        "source_message_id": source_message_id,
        "metadata": fact_metadata,
    }


# ---------------------------------------------------------------------------
# Spending summary over facts (aggregation)
# ---------------------------------------------------------------------------

_VALID_SPENDING_GROUP_BY_MODES = {"category", "merchant", "week", "month"}


async def spending_summary_facts(
    pool: asyncpg.Pool,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    group_by: str | None = None,
    category_filter: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    """Aggregate outflow (debit) spending from transaction facts over a date range.

    Returns the same shape as the original spending_summary():
        {start_date, end_date, currency, total_spend, groups}
    where amounts in groups are string-encoded for NUMERIC precision.

    group_by values: 'category', 'merchant', 'week', 'month', or None (single bucket).
    """
    if group_by is not None and group_by not in _VALID_SPENDING_GROUP_BY_MODES:
        raise ValueError(
            f"Unsupported group_by value: {group_by!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_SPENDING_GROUP_BY_MODES))}"
        )

    # Resolve date range defaults
    if start_date is None or end_date is None:
        default_start, default_end = _current_month_bounds()
        if start_date is None:
            start_date = default_start
        if end_date is None:
            end_date = default_end

    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    conditions: list[str] = [
        f"predicate = '{_PREDICATE_TRANSACTION_DEBIT}'",
        "validity = 'active'",
        "scope = 'finance'",
        "valid_at::date >= $1",
        "valid_at::date <= $2",
    ]
    params: list[Any] = [start_date, end_date]
    idx = 3

    if category_filter is not None:
        conditions.append(f"metadata->>'category' = ${idx}")
        params.append(category_filter)
        idx += 1

    if account_id is not None:
        conditions.append(f"metadata->>'account_id' = ${idx}")
        params.append(account_id)
        idx += 1

    where_clause = " AND ".join(conditions)

    # Total spend
    total_row = await pool.fetchrow(
        f"""
        SELECT COALESCE(SUM((metadata->>'amount')::numeric), 0) AS total
        FROM facts
        WHERE {where_clause}
        """,
        *params,
    )
    total_spend: Decimal = total_row["total"]

    # Representative currency (most frequent)
    currency_row = await pool.fetchrow(
        f"""
        SELECT metadata->>'currency' AS currency, COUNT(*) AS cnt
        FROM facts
        WHERE {where_clause}
        GROUP BY metadata->>'currency'
        ORDER BY cnt DESC
        LIMIT 1
        """,
        *params,
    )
    currency: str = currency_row["currency"] if currency_row and currency_row["currency"] else "USD"

    # Grouping
    groups: list[dict[str, Any]] = []

    if group_by is None:
        count_row = await pool.fetchrow(
            f"SELECT COUNT(*) AS cnt FROM facts WHERE {where_clause}",
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
            SELECT COALESCE(metadata->>'inferred_category', metadata->>'category') AS key,
                   SUM((metadata->>'amount')::numeric) AS amount,
                   COUNT(*) AS count
            FROM facts
            WHERE {where_clause}
            GROUP BY COALESCE(metadata->>'inferred_category', metadata->>'category')
            ORDER BY SUM((metadata->>'amount')::numeric) DESC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    elif group_by == "merchant":
        rows = await pool.fetch(
            f"""
            SELECT COALESCE(metadata->>'normalized_merchant', metadata->>'merchant') AS key,
                   SUM((metadata->>'amount')::numeric) AS amount,
                   COUNT(*) AS count
            FROM facts
            WHERE {where_clause}
            GROUP BY COALESCE(metadata->>'normalized_merchant', metadata->>'merchant')
            ORDER BY SUM((metadata->>'amount')::numeric) DESC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    elif group_by == "week":
        rows = await pool.fetch(
            f"""
            SELECT TO_CHAR(DATE_TRUNC('week', valid_at), 'IYYY-"W"IW') AS key,
                   SUM((metadata->>'amount')::numeric) AS amount,
                   COUNT(*) AS count
            FROM facts
            WHERE {where_clause}
            GROUP BY DATE_TRUNC('week', valid_at)
            ORDER BY DATE_TRUNC('week', valid_at) ASC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    elif group_by == "month":
        rows = await pool.fetch(
            f"""
            SELECT TO_CHAR(DATE_TRUNC('month', valid_at), 'YYYY-MM') AS key,
                   SUM((metadata->>'amount')::numeric) AS amount,
                   COUNT(*) AS count
            FROM facts
            WHERE {where_clause}
            GROUP BY DATE_TRUNC('month', valid_at)
            ORDER BY DATE_TRUNC('month', valid_at) ASC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "currency": currency,
        "total_spend": str(total_spend),
        "groups": groups,
    }


# ---------------------------------------------------------------------------
# Aggregate query: distinct merchants
# ---------------------------------------------------------------------------

_MAX_DISTINCT_MERCHANTS_LIMIT = 1000


async def list_distinct_merchants(
    pool: asyncpg.Pool,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    min_count: int | None = None,
    unnormalized_only: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    """Return distinct merchants from active transaction facts with aggregates.

    Groups by the unique combination of original merchant and normalized merchant.
    Each distinct (merchant, normalized_merchant) pair is a separate row.
    Returns: {items: [{merchant, normalized_merchant, count, total_amount}], total, limit, offset}

    Filters:
      start_date / end_date  — ISO-8601 date strings or date objects
      min_count              — HAVING COUNT(*) >= min_count
      unnormalized_only      — Only rows where normalized_merchant IS NULL
      limit / offset         — Pagination (limit capped at 1000)
    """
    limit = min(max(1, limit), _MAX_DISTINCT_MERCHANTS_LIMIT)
    offset = max(0, offset)

    if isinstance(start_date, str):
        start_date = date.fromisoformat(start_date)
    if isinstance(end_date, str):
        end_date = date.fromisoformat(end_date)

    conditions: list[str] = [
        f"predicate = ANY(ARRAY{_TRANSACTION_PREDICATES!r}::text[])",
        "validity = 'active'",
        "scope = 'finance'",
    ]
    params: list[Any] = []
    idx = 1

    if start_date is not None:
        conditions.append(f"valid_at::date >= ${idx}")
        params.append(start_date)
        idx += 1

    if end_date is not None:
        conditions.append(f"valid_at::date <= ${idx}")
        params.append(end_date)
        idx += 1

    if unnormalized_only:
        conditions.append("(metadata->>'normalized_merchant') IS NULL")

    where_clause = " AND ".join(conditions)

    # Build HAVING clause for min_count
    having_clause = ""
    if min_count is not None and min_count >= 1:
        having_clause = f"HAVING COUNT(*) >= ${idx}"
        params.append(min_count)
        idx += 1

    # Count query (wrap in subquery to apply HAVING)
    count_sql = f"""
    SELECT COUNT(*) AS total FROM (
        SELECT 1
        FROM facts
        WHERE {where_clause}
        GROUP BY metadata->>'merchant', metadata->>'normalized_merchant'
        {having_clause}
    ) sq
    """
    count_row = await pool.fetchrow(count_sql, *params)
    total = count_row["total"] if count_row else 0

    # Data query
    data_sql = f"""
    SELECT
        metadata->>'merchant' AS merchant,
        metadata->>'normalized_merchant' AS normalized_merchant,
        COUNT(*) AS count,
        SUM((metadata->>'amount')::numeric) AS total_amount
    FROM facts
    WHERE {where_clause}
    GROUP BY metadata->>'merchant', metadata->>'normalized_merchant'
    {having_clause}
    ORDER BY COUNT(*) DESC, metadata->>'merchant' ASC
    LIMIT ${idx} OFFSET ${idx + 1}
    """
    params_data = params + [limit, offset]
    rows = await pool.fetch(data_sql, *params_data)

    items = [
        {
            "merchant": r["merchant"] or "",
            "normalized_merchant": r["normalized_merchant"],
            "count": r["count"],
            "total_amount": str(r["total_amount"] or Decimal("0.00")),
        }
        for r in rows
    ]

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


# ---------------------------------------------------------------------------
# Bulk metadata update (overlay, write-once contract preserved)
# ---------------------------------------------------------------------------

_MAX_BULK_UPDATE_OPS = 200
_ALLOWED_SET_KEYS = frozenset({"normalized_merchant", "inferred_category"})


async def bulk_update_transactions(
    pool: asyncpg.Pool,
    ops: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply bulk metadata overlay to matching transaction facts.

    Each op has the shape:
      {
        "match": {"merchant_pattern": "<ILIKE pattern>"},
        "set":   {"normalized_merchant": "...", "inferred_category": "..."}
      }

    Constraints:
    - Maximum 200 ops per call.
    - Only 'normalized_merchant' and 'inferred_category' keys are settable.
    - Uses JSONB overlay: metadata = metadata || jsonb_build_object(...) so
      original merchant/category and all other fact columns are NEVER modified.
    - Pattern matching is ILIKE on metadata->>'merchant'.

    Returns: {updated_total, results: [{pattern, set, matched, updated}]}
    """
    if not isinstance(ops, list):
        raise ValueError("ops must be a list")
    if len(ops) > _MAX_BULK_UPDATE_OPS:
        raise ValueError(f"Too many ops: {len(ops)} exceeds max {_MAX_BULK_UPDATE_OPS}")

    results = []
    updated_total = 0

    for op in ops:
        if not isinstance(op, dict):
            raise ValueError(f"Each op must be a dict, got: {type(op).__name__}")
        match = op.get("match") or {}
        set_fields = op.get("set") or {}

        merchant_pattern = match.get("merchant_pattern")
        if not merchant_pattern:
            raise ValueError("Each op must have match.merchant_pattern")

        # Validate set keys
        unknown_keys = set(set_fields.keys()) - _ALLOWED_SET_KEYS
        if unknown_keys:
            raise ValueError(
                f"set keys {sorted(unknown_keys)} are not allowed. "
                f"Only {sorted(_ALLOWED_SET_KEYS)} may be set."
            )

        if not set_fields:
            # Nothing to set — count matches but do not update
            matched = await pool.fetchval(
                """
                SELECT COUNT(*) FROM facts
                WHERE predicate = ANY($1::text[])
                  AND validity = 'active'
                  AND scope = 'finance'
                  AND metadata->>'merchant' ILIKE $2
                """,
                _TRANSACTION_PREDICATES,
                merchant_pattern,
            )
            results.append(
                {
                    "pattern": merchant_pattern,
                    "set": set_fields,
                    "matched": matched or 0,
                    "updated": 0,
                }
            )
            continue

        # Build the JSONB overlay: only allowed keys
        overlay_pairs = []
        overlay_values: list[Any] = [_TRANSACTION_PREDICATES, merchant_pattern]
        param_idx = 3
        for key in sorted(set_fields.keys()):
            overlay_pairs.append(f"'{key}', ${param_idx}::text")
            overlay_values.append(set_fields[key])
            param_idx += 1

        overlay_expr = ", ".join(overlay_pairs)

        update_sql = f"""
        UPDATE facts
        SET metadata = metadata || jsonb_build_object({overlay_expr})
        WHERE predicate = ANY($1::text[])
          AND validity = 'active'
          AND scope = 'finance'
          AND metadata->>'merchant' ILIKE $2
        """
        status = await pool.execute(update_sql, *overlay_values)
        # asyncpg returns "UPDATE N"
        try:
            updated = int(status.split()[-1])
        except (ValueError, AttributeError, IndexError):
            updated = 0

        updated_total += updated
        results.append(
            {
                "pattern": merchant_pattern,
                "set": set_fields,
                "matched": updated,
                "updated": updated,
            }
        )

    return {
        "updated_total": updated_total,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Bulk transaction ingestion (embedding-bypass path)
# ---------------------------------------------------------------------------

_MAX_BULK_TRANSACTIONS = 500


# ---------------------------------------------------------------------------
# Cross-source fuzzy dedup helpers
# ---------------------------------------------------------------------------


def _tokenize_merchant(merchant: str) -> frozenset[str]:
    """Split a merchant string into lowercase alphabetic-only tokens (digits stripped).

    Per spec: "lowercased, strip digits/symbols". Stripping digits prevents store-number
    pollution: "STARBUCKS #1234" and "STARBUCKS #5678" both reduce to {"starbucks"}
    and can match each other.

    Example: "WHOLEFDS MKT #10456 AUSTIN TX"
        -> frozenset({"wholefds", "mkt", "austin", "tx"})
    Example: "Whole Foods Market" -> frozenset({"whole", "foods", "market"})
    """
    return frozenset(tok.lower() for tok in re.findall(r"[a-z]+", merchant, flags=re.IGNORECASE))


def _jaccard_similarity(set_a: frozenset[str], set_b: frozenset[str]) -> float:
    """Compute Jaccard similarity: |intersection| / |union|.

    Returns 0.0 when both sets are empty.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _merchant_tokens_match(merchant_a: str, merchant_b: str, threshold: float = 0.5) -> bool:
    """Return True when token-overlap Jaccard similarity is >= threshold."""
    tokens_a = _tokenize_merchant(merchant_a)
    tokens_b = _tokenize_merchant(merchant_b)
    if not tokens_a or not tokens_b:
        return False
    return _jaccard_similarity(tokens_a, tokens_b) >= threshold


async def _fetch_facts_for_date_range(
    pool: asyncpg.Pool,
    start_dt: datetime,
    end_dt: datetime,
    account_id: str | None = None,
) -> list[dict[str, Any]]:
    """Batch pre-fetch active transaction facts in [start_dt, end_dt].

    Used to build an in-memory cache for cross-source fuzzy dedup so we avoid
    N+1 queries while processing a CSV import batch.

    Returns a list of lightweight dicts with: valid_at, amount, merchant, account_id.
    Rows without a source_message_id are excluded (they are CSV-sourced, not email-sourced).
    We want to catch email-sourced facts that match an incoming CSV row, so we
    only keep facts that have a source_message_id.
    """
    try:
        conditions = [
            "predicate = ANY($1::text[])",
            "validity = 'active'",
            "scope = 'finance'",
            "valid_at >= $2",
            "valid_at <= $3",
            "metadata->>'source_message_id' IS NOT NULL",
        ]
        params: list[Any] = [_TRANSACTION_PREDICATES, start_dt, end_dt]
        idx = 4

        if account_id is not None:
            conditions.append(f"metadata->>'account_id' = ${idx}")
            params.append(account_id)
            idx += 1

        where = " AND ".join(conditions)
        rows = await pool.fetch(
            f"""
            SELECT valid_at,
                   metadata->>'amount'     AS amount,
                   metadata->>'merchant'   AS merchant,
                   metadata->>'account_id' AS account_id
            FROM facts
            WHERE {where}
            """,
            *params,
        )
        result = []
        for r in rows:
            meta_amount = r["amount"]
            if meta_amount is None:
                continue
            try:
                amt = Decimal(str(meta_amount))
            except (ValueError, DecimalInvalidOperation):
                continue
            result.append(
                {
                    "valid_at": r["valid_at"],
                    "amount": amt,
                    "merchant": r["merchant"] or "",
                    "account_id": r["account_id"],
                }
            )
        return result
    except asyncpg.PostgresError as e:
        logger.warning("_fetch_facts_for_date_range: DB error building fuzzy-dedup cache: %s", e)
        return []


def _is_cross_source_match(
    incoming_amount: Decimal,
    incoming_posted_at: datetime,
    incoming_merchant: str,
    incoming_account_id: str | None,
    existing_facts: list[dict[str, Any]],
) -> bool:
    """Return True when an existing (email-sourced) fact fuzzy-matches the incoming CSV row.

    Match criteria (all must be satisfied):
    - amount within ±$0.01
    - valid_at (posted_at) within ±1 day
    - merchant token Jaccard similarity ≥ 0.5
    - account_id matches when both sides provide one

    account_id is only a constraint when both the incoming row and the existing
    fact have a non-None account_id; if either is None, account_id is ignored.
    """
    amount_tol = Decimal("0.01")
    date_tol = timedelta(days=1)

    for fact in existing_facts:
        # Amount within ±$0.01
        if abs(fact["amount"] - incoming_amount) > amount_tol:
            continue

        # valid_at within ±1 day
        fact_dt = fact["valid_at"]
        if fact_dt is None:
            continue
        if fact_dt.tzinfo is None:
            fact_dt = fact_dt.replace(tzinfo=UTC)
        if abs(fact_dt - incoming_posted_at) > date_tol:
            continue

        # account_id filter: only applied when both sides specify one
        if incoming_account_id is not None and fact["account_id"] is not None:
            if incoming_account_id != fact["account_id"]:
                continue

        # Merchant token overlap ≥ 0.5
        if not _merchant_tokens_match(incoming_merchant, fact["merchant"]):
            continue

        return True

    return False


def _compute_composite_dedup_key(
    posted_at: datetime,
    amount: Decimal | float | int | str,
    merchant: str,
    account_id: str | None,
) -> str:
    """Compute the composite idempotency key for CSV-sourced transactions.

    Key = sha256(canonical_posted_at|canonical_amount|merchant|canonical_account_id)

    Canonicalization rules:
      - posted_at: UTC ISO 8601 with Z suffix at second precision
        (e.g. "2025-01-15T00:00:00Z")
      - amount: str(Decimal(amount).quantize(Decimal("0.01")))
        (e.g. "-47.32")
      - merchant: used as-is (case-sensitive)
      - account_id: lowercased or empty string when absent
    """
    # Canonicalize posted_at: convert to UTC, strip microseconds, use Z suffix
    if posted_at.tzinfo is None:
        dt_utc = posted_at.replace(tzinfo=UTC)
    else:
        dt_utc = posted_at.astimezone(UTC)
    canonical_posted_at = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Canonicalize amount
    canonical_amount = str(Decimal(str(amount)).quantize(Decimal("0.01")))

    # Canonicalize account_id
    canonical_account_id = account_id.lower() if account_id else ""

    key_input = f"{canonical_posted_at}|{canonical_amount}|{merchant}|{canonical_account_id}"
    return hashlib.sha256(key_input.encode()).hexdigest()


async def bulk_record_transactions(
    pool: asyncpg.Pool,
    transactions: list[dict[str, Any]],
    account_id: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Bulk-ingest normalized transaction objects as bitemporal facts.

    Embeddings are SKIPPED for this path (no embedding_engine.embed() call).
    A zero-vector placeholder is stored. tsvector (search_vector) is computed.

    Deduplication (priority order for CSV-sourced rows):
    1. Rows WITH source_message_id: source_message_id-based dedup (same logic
       as record_transaction_fact). Fuzzy dedup is NOT applied.
    2. Rows WITHOUT source_message_id: cross-source fuzzy dedup first — if an
       existing email-sourced fact matches on amount (±$0.01), date (±1 day),
       and merchant token overlap (Jaccard ≥ 0.5), the row is skipped with
       reason "cross_source_match". Existing facts are pre-fetched in a single
       batch query for the import date range (no N+1 queries).
    3. Composite key dedup: sha256(posted_at|amount|merchant|account_id) with
       canonicalized inputs — applied only when fuzzy dedup did not skip the row.

    Args:
        pool: Database connection pool.
        transactions: List of normalized transaction dicts. Each must have:
            posted_at (ISO 8601 str), merchant (str), amount (str decimal).
            Optional: currency, category, description, payment_method,
            account_id (per-row override), source_message_id, metadata.
        account_id: Top-level account_id inherited by all rows unless per-row
            account_id is set.
        source: Stored as import_source in fact metadata for all rows.

    Returns:
        {total, imported, skipped, errors, error_details}
        error_details items have: {index, reason}
        reason is "duplicate" for dedup skips, "cross_source_match" for fuzzy
        cross-source dedup skips, "invalid_date" for unparseable dates,
        "invalid_amount" for non-numeric amounts.
    """
    if len(transactions) > _MAX_BULK_TRANSACTIONS:
        raise ValueError(
            f"Batch too large: {len(transactions)} exceeds maximum of {_MAX_BULK_TRANSACTIONS}"
        )

    owner_entity_id = await _get_owner_entity_id(pool)

    # Lazy-load search_vector helpers (avoids import-time side effects)
    from butlers.modules.memory.search_vector import preprocess_text, tsvector_sql

    imported = 0
    skipped = 0
    errors = 0
    error_details: list[dict[str, Any]] = []

    now = datetime.now(UTC)

    # ------------------------------------------------------------------
    # Batch pre-fetch: load existing email-sourced facts for the import
    # date range once so cross-source fuzzy dedup runs in-memory.
    # We only need this for CSV-sourced rows (no source_message_id); skip
    # when all rows have a source_message_id.
    # ------------------------------------------------------------------
    csv_rows = [t for t in transactions if not t.get("source_message_id")]
    existing_facts_cache: list[dict[str, Any]] = []
    if csv_rows:
        parsed_dates: list[datetime] = []
        for txn in csv_rows:
            raw = txn.get("posted_at")
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                parsed_dates.append(dt)
            except (ValueError, TypeError):
                logger.warning(
                    "bulk_record_transactions: invalid posted_at in CSV row during pre-fetch"
                    " (row excluded from fuzzy-dedup date range): %r",
                    raw,
                )
        if parsed_dates:
            range_start = min(parsed_dates) - timedelta(days=1)
            range_end = max(parsed_dates) + timedelta(days=1)
            existing_facts_cache = await _fetch_facts_for_date_range(
                pool,
                start_dt=range_start,
                end_dt=range_end,
                account_id=account_id,
            )

    for idx, txn in enumerate(transactions):
        # ------------------------------------------------------------------
        # 1. Parse and validate required fields
        # ------------------------------------------------------------------
        try:
            raw_posted_at = txn.get("posted_at")
            if not raw_posted_at:
                raise ValueError("missing posted_at")
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
        except (ValueError, TypeError, DecimalInvalidOperation):
            errors += 1
            error_details.append({"index": idx, "reason": "invalid_amount"})
            continue

        merchant = txn.get("merchant")
        if not merchant:
            errors += 1
            error_details.append({"index": idx, "reason": "missing_merchant"})
            continue

        # ------------------------------------------------------------------
        # 2. Resolve account_id (per-row overrides top-level)
        # ------------------------------------------------------------------
        effective_account_id: str | None = txn.get("account_id") or account_id

        # ------------------------------------------------------------------
        # 3. Compute derived fields
        # ------------------------------------------------------------------
        currency = (txn.get("currency") or "USD").upper()
        category = txn.get("category") or "uncategorized"
        description = txn.get("description")
        payment_method = txn.get("payment_method")
        source_message_id = txn.get("source_message_id")
        extra_metadata: dict[str, Any] = dict(txn.get("metadata") or {})

        direction = _infer_direction(amount_decimal)
        predicate = (
            _PREDICATE_TRANSACTION_DEBIT if direction == "debit" else _PREDICATE_TRANSACTION_CREDIT
        )
        stored_amount = _abs_decimal(amount_decimal)

        content = f"{merchant} {_str_amount(stored_amount)} {currency}"

        # ------------------------------------------------------------------
        # 4. Determine idempotency key
        # ------------------------------------------------------------------
        if source_message_id is not None:
            # Email-based dedup: check existing active fact by source_message_id
            try:
                existing = await pool.fetchrow(
                    """
                    SELECT id FROM facts
                    WHERE predicate = $1
                      AND validity = 'active'
                      AND valid_at = $2
                      AND metadata->>'source_message_id' = $3
                    LIMIT 1
                    """,
                    predicate,
                    posted_at,
                    source_message_id,
                )
                if existing is not None:
                    skipped += 1
                    error_details.append({"index": idx, "reason": "duplicate"})
                    continue
            except asyncpg.PostgresError:
                pass

            # Build a source_message_id-based idempotency key
            idem_parts = "|".join(
                [
                    str(owner_entity_id) if owner_entity_id else "",
                    predicate,
                    posted_at.isoformat(),
                    source_message_id,
                ]
            )
            idempotency_key = hashlib.sha256(idem_parts.encode()).hexdigest()
        else:
            # Fuzzy cross-source dedup: check in-memory cache of existing
            # email-sourced facts before falling through to composite key dedup.
            if _is_cross_source_match(
                incoming_amount=stored_amount,
                incoming_posted_at=posted_at,
                incoming_merchant=merchant,
                incoming_account_id=effective_account_id,
                existing_facts=existing_facts_cache,
            ):
                skipped += 1
                error_details.append({"index": idx, "reason": "cross_source_match"})
                continue

            # Composite dedup key for CSV-sourced rows
            idempotency_key = _compute_composite_dedup_key(
                posted_at=posted_at,
                amount=amount_decimal,
                merchant=merchant,
                account_id=effective_account_id,
            )

        # ------------------------------------------------------------------
        # 5. Check idempotency key (dedup)
        # ------------------------------------------------------------------
        try:
            existing_idem = await pool.fetchval(
                "SELECT id FROM facts WHERE tenant_id = 'owner' AND idempotency_key = $1",
                idempotency_key,
            )
            if existing_idem is not None:
                skipped += 1
                error_details.append({"index": idx, "reason": "duplicate"})
                continue
        except asyncpg.PostgresError:
            pass

        # ------------------------------------------------------------------
        # 6. Build fact metadata
        # ------------------------------------------------------------------
        fact_metadata: dict[str, Any] = {
            "merchant": merchant,
            "amount": _str_amount(stored_amount),
            "currency": currency,
            "category": category,
            "direction": direction,
        }
        if description is not None:
            fact_metadata["description"] = description
        if payment_method is not None:
            fact_metadata["payment_method"] = payment_method
        if effective_account_id is not None:
            fact_metadata["account_id"] = effective_account_id
        if source_message_id is not None:
            fact_metadata["source_message_id"] = source_message_id
        if source is not None:
            fact_metadata["import_source"] = source
        if extra_metadata:
            fact_metadata.update(extra_metadata)

        # ------------------------------------------------------------------
        # 7. Insert fact directly (no embedding_engine.embed() call)
        #    embedding = NULL (zero-vector bypass per spec)
        #    search_vector computed via tsvector_sql
        # ------------------------------------------------------------------
        fact_id = uuid.uuid4()
        searchable = preprocess_text(f"owner {predicate} {content}")
        meta_json = json.dumps(fact_metadata)

        try:
            sql = f"""
                INSERT INTO facts (
                    id, subject, predicate, content, embedding, search_vector,
                    importance, confidence, decay_rate, permanence, source_butler,
                    source_episode_id, supersedes_id, validity, scope,
                    created_at, last_confirmed_at, tags, metadata, entity_id,
                    valid_at, tenant_id, idempotency_key, observed_at,
                    retention_class, sensitivity
                )
                VALUES (
                    $1, $2, $3, $4, NULL, {tsvector_sql("$5")},
                    5.0, 1.0, 0.002, 'stable', NULL,
                    NULL, NULL, 'active', 'finance',
                    $6, $6, '[]'::jsonb, $7::jsonb, $8,
                    $9, 'owner', $10, $6,
                    'operational', 'normal'
                )
                ON CONFLICT DO NOTHING
            """
            await pool.execute(
                sql,
                fact_id,  # $1
                "owner",  # $2 subject
                predicate,  # $3
                content,  # $4
                searchable,  # $5 search_vector text
                now,  # $6 created_at / last_confirmed_at / observed_at
                meta_json,  # $7 metadata
                owner_entity_id,  # $8 entity_id (may be None)
                posted_at,  # $9 valid_at
                idempotency_key,  # $10
            )
            imported += 1
        except asyncpg.UniqueViolationError:
            # Race condition: another process inserted same idempotency_key
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
    }
