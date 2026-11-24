"""Non-transaction Finance memory: account, subscription, and bill property facts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

_PREDICATE_ACCOUNT = "account"
_PREDICATE_SUBSCRIPTION = "subscription"
_PREDICATE_BILL = "bill"
_VALID_SUBSCRIPTION_STATUSES = ("active", "cancelled", "paused")
_VALID_SUBSCRIPTION_FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly", "custom")
_VALID_BILL_STATUSES = ("pending", "paid", "overdue")
_VALID_BILL_FREQUENCIES = ("one_time", "weekly", "monthly", "quarterly", "yearly", "custom")
_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _str_amount(amount: Decimal | float | int | str) -> str:
    """Normalize an amount to a string-encoded NUMERIC(14,2) representation."""
    return str(Decimal(str(amount)).quantize(Decimal("0.01")))


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
) -> dict[str, Any]:
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
        source_butler="finance",
    )


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

    fact_id = (
        await _store_fact(
            pool,
            subject=subject,
            predicate=_PREDICATE_ACCOUNT,
            content=content,
            scope="finance",
            entity_id=None,
            valid_at=None,  # property fact — triggers supersession
            metadata=fact_metadata,
            permanence="stable",
        )
    )["id"]

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

    fact_id = (
        await _store_fact(
            pool,
            subject=subject,
            predicate=_PREDICATE_SUBSCRIPTION,
            content=content,
            scope="finance",
            entity_id=None,
            valid_at=None,  # property fact — supersession
            metadata=fact_metadata,
            permanence="stable",
        )
    )["id"]

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


async def _write_bill_fact(
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

    fact_id = (
        await _store_fact(
            pool,
            subject=subject,
            predicate=_PREDICATE_BILL,
            content=content,
            scope="finance",
            entity_id=None,
            valid_at=None,  # property fact — supersession
            metadata=fact_metadata,
            permanence="stable",
        )
    )["id"]

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
