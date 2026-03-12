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

Spending summary
----------------
spending_summary_facts() aggregates debit transaction facts via JSONB extraction
on the facts table, returning the same shape as the original spending_summary().
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
    return {
        "id": str(row["id"]),
        "direction": direction,
        "merchant": meta.get("merchant", ""),
        "amount": meta.get("amount", "0.00"),
        "currency": meta.get("currency", "USD"),
        "category": meta.get("category", ""),
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
            SELECT metadata->>'category' AS key,
                   SUM((metadata->>'amount')::numeric) AS amount,
                   COUNT(*) AS count
            FROM facts
            WHERE {where_clause}
            GROUP BY metadata->>'category'
            ORDER BY SUM((metadata->>'amount')::numeric) DESC
            """,
            *params,
        )
        groups = [{"key": r["key"], "amount": str(r["amount"]), "count": r["count"]} for r in rows]

    elif group_by == "merchant":
        rows = await pool.fetch(
            f"""
            SELECT metadata->>'merchant' AS key,
                   SUM((metadata->>'amount')::numeric) AS amount,
                   COUNT(*) AS count
            FROM facts
            WHERE {where_clause}
            GROUP BY metadata->>'merchant'
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
