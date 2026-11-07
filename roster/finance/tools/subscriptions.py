"""Finance butler subscription tools — create and update recurring service commitments."""

from __future__ import annotations

import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _row_to_dict
from butlers.tools.finance.expected_signals import (
    FinanceSignalSource,
    metadata_with_signal_source,
    runtime_signal_source,
)

logger = logging.getLogger(__name__)

_VALID_STATUSES = ("active", "cancelled", "paused")
_VALID_FREQUENCIES = ("weekly", "monthly", "quarterly", "yearly", "custom")


def _normalize_renewal_date(next_renewal: str | date) -> date:
    """Normalize renewal value to a canonical date boundary (midnight, day start).

    Accepts ISO date strings (YYYY-MM-DD) or date objects.
    """
    if isinstance(next_renewal, date):
        return next_renewal
    return date.fromisoformat(str(next_renewal))


def _normalize_optional_date(value: str | date | None) -> date | None:
    """Normalize an optional date value. Accepts ISO date strings, date objects, or None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


async def _writer_is_owner(pool: asyncpg.Pool) -> bool:
    """True only when server-held routing context attests an owner session.

    The answer never comes from a tool argument.  A routed email, a missing context
    and an unreadable attestation are all "not the owner", so a failure here makes
    the write extracted (fail closed).
    """
    try:
        source = await runtime_signal_source(pool)
    except Exception:  # noqa: BLE001 -- unavailable attestation fails closed
        logger.error("Subscription writer attestation failed; treating write as extracted")
        return False
    return source is not None and source.producer == "owner"


def field_claims(row: Any) -> list[dict[str, Any]]:
    """Retained immutable value claims; missing legacy state conveys no authority."""
    value = row.get("field_claims", [])
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or any(not isinstance(c, dict) for c in value):
        raise ValueError("invalid subscription claims")
    return [dict(c) for c in value]


def selected_renewal(row: Any, start: date, end: date) -> date | None:
    """Filter EACH date before choosing, so elapsed history cannot hide a current claim."""
    dates = [_normalize_renewal_date(row["next_renewal"])]
    dates.extend(
        _normalize_renewal_date(c["value"])
        for c in field_claims(row)
        if c.get("field") == "renewal" and not c.get("resolved", False)
    )
    return min((d for d in dates if start <= d <= end), default=None)


def merge_field_claims(
    existing: Any,
    renewal: date,
    amount: Any,
    currency: str,
    owner: bool,
    source: FinanceSignalSource | None,
) -> dict[str, Any]:
    """Called under the owning row lock. A claim is never a replacement baseline."""
    from decimal import Decimal

    claims = field_claims(existing)
    state = dict(existing)
    state["field_claims"] = claims
    binding = source.as_metadata() if source is not None else None
    for field, value, current, epoch, origin in (
        (
            "renewal",
            renewal.isoformat(),
            _normalize_renewal_date(existing["next_renewal"]).isoformat(),
            "date_epoch",
            "renewal_source",
        ),
        (
            "price",
            {"amount": str(Decimal(str(amount)).normalize()), "currency": currency},
            {
                "amount": str(Decimal(str(existing["amount"])).normalize()),
                "currency": existing["currency"],
            },
            "price_epoch",
            "price_source",
        ),
    ):
        if owner:
            state[epoch] = int(existing.get(epoch, 0)) + 1
            state[origin] = "owner"
            for claim in claims:
                if claim["field"] == field and not claim.get("resolved", False):
                    claim["resolved"] = True
                    claim["resolved_epoch"] = state[epoch]
        elif value != current:
            claim = {
                "field": field,
                "value": value,
                "epoch": int(existing.get(epoch, 0)),
                "source": binding,
                "resolved": False,
            }
            if claim not in claims:
                claims.append(claim)
    state["next_renewal"] = renewal if owner else existing["next_renewal"]
    state["amount"] = amount if owner else existing["amount"]
    state["currency"] = currency if owner else existing["currency"]
    return state


@asynccontextmanager
async def _owning_connection(pool):
    async with pool.acquire() as connection:
        async with connection.transaction():
            yield connection


async def subscription_warning_still_relevant(connection, subject, selected_date, epoch):
    """Fixed owning SQL only. Invocation authorization belongs to the registered closure."""
    row = await connection.fetchrow(
        "SELECT next_renewal, date_epoch, field_claims, status, status_source "
        "FROM subscriptions WHERE id = $1",
        subject,
    )
    if row is None:
        return None
    if row["date_epoch"] != epoch:
        return False
    if row["status"] != "active" and row["status_source"] == "owner":
        return False
    from datetime import timedelta

    from butlers.tools.finance.budgets import _period_anchor, resolve_budget_zone

    today = _period_anchor(await resolve_budget_zone(connection))
    return selected_renewal(row, today, today + timedelta(days=14)) == selected_date


async def track_subscription(
    pool: asyncpg.Pool,
    service: str,
    amount: float,
    currency: str,
    frequency: str,
    next_renewal: str | date,
    status: str = "active",
    auto_renew: bool = True,
    payment_method: str | None = None,
    account_id: str | uuid.UUID | None = None,
    source_message_id: str | None = None,
    cancellation_url: str | None = None,
    notice_period_days: int | None = None,
    cancel_by: str | date | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    _expected_signal_source: FinanceSignalSource | None = None,
) -> dict[str, Any]:
    """Create or update a subscription lifecycle record in finance.subscriptions.

    Upsert logic: match on (service, frequency). If an existing record is found,
    update all provided fields and refresh updated_at. If no match is found,
    insert a new record.

    Provenance (bu-svqpzo.10): ``cancellation_url`` and ``status`` are stored with
    the source that wrote them, derived from the server-held runtime routing
    context (``owner`` only for an owner-attested session; otherwise
    ``extracted``).  An extracted write never overwrites an owner-set
    ``cancellation_url`` or ``status``; the rule lives in the UPDATE itself so
    concurrent writers cannot race it.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    service:
        Service name (e.g. "Netflix", "Spotify", "Adobe Creative Cloud").
    amount:
        Recurring charge amount as a decimal.
    currency:
        ISO-4217 uppercase currency code (e.g. "USD", "EUR").
    frequency:
        Recurrence frequency. One of: weekly, monthly, quarterly, yearly, custom.
    next_renewal:
        Next renewal date. Accepts ISO date strings (YYYY-MM-DD) or date objects.
    status:
        Subscription status. One of: active, cancelled, paused. Default: active.
    auto_renew:
        Whether the subscription auto-renews. Default: True.
    payment_method:
        Payment method description (e.g. "Visa ending in 4242").
    account_id:
        UUID of linked financial account in finance.accounts.
    source_message_id:
        Source email or provider message ID for provenance.
    cancellation_url:
        URL where the subscription can be cancelled. Optional. Stored as
        ``extracted`` unless the writer is an owner-attested session.
    notice_period_days:
        Number of days' notice the provider requires before cancellation
        takes effect. Optional.
    cancel_by:
        Latest date by which cancellation must be initiated to avoid the
        next renewal charge. Accepts ISO date strings (YYYY-MM-DD), date
        objects, or None.
    metadata:
        Arbitrary JSON metadata for extended attributes.

    Returns
    -------
    dict
        SubscriptionRecord with all persisted fields.
    """
    from decimal import Decimal

    if not Decimal(str(amount)).is_finite():
        raise ValueError("amount must be finite")
    if (
        not isinstance(currency, str)
        or len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
        or not currency.isupper()
    ):
        raise ValueError("currency must be an uppercase three-letter code")
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of {_VALID_STATUSES}")
    if frequency not in _VALID_FREQUENCIES:
        raise ValueError(f"Invalid frequency {frequency!r}. Must be one of {_VALID_FREQUENCIES}")

    async with _owning_connection(pool) as connection:
        return await _write_subscription(
            connection,
            service,
            amount,
            currency,
            frequency,
            next_renewal,
            status,
            auto_renew,
            payment_method,
            account_id,
            source_message_id,
            cancellation_url,
            notice_period_days,
            cancel_by,
            metadata,
        )


async def _write_subscription(
    pool,
    service,
    amount,
    currency,
    frequency,
    next_renewal,
    status,
    auto_renew,
    payment_method,
    account_id,
    source_message_id,
    cancellation_url,
    notice_period_days,
    cancel_by,
    metadata,
):
    renewal_date = _normalize_renewal_date(next_renewal)
    cancel_by_date = _normalize_optional_date(cancel_by)
    metadata_value = metadata_with_signal_source(metadata, None)
    account_uuid = uuid.UUID(str(account_id)) if account_id is not None else None

    class Unattested(Exception):
        pass

    try:
        # The legacy attestor deliberately absorbs read errors. Roll back an
        # unattested read's savepoint so a PostgreSQL error cannot poison the
        # owning write transaction, while keeping its exact held connection.
        async with pool.transaction():
            actual_source = await runtime_signal_source(pool)
            if actual_source is None:
                raise Unattested
    except Exception:
        actual_source = None
    write_source = (
        "owner" if actual_source is not None and actual_source.producer == "owner" else "extracted"
    )
    metadata_value = metadata_with_signal_source(metadata, actual_source)
    # Serialize missing-row creators as well as existing writers. No advisory lock escapes TX.
    await pool.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))",
        json.dumps([service, frequency], separators=(",", ":")),
    )

    # Upsert: look up existing record by (service, frequency)
    existing = await pool.fetchrow(
        "SELECT * FROM subscriptions WHERE service = $1 AND frequency = $2 FOR UPDATE",
        service,
        frequency,
    )

    if existing is not None:
        fields = merge_field_claims(
            existing, renewal_date, amount, currency, write_source == "owner", actual_source
        )
        amount, currency, renewal_date = (
            fields["amount"],
            fields["currency"],
            fields["next_renewal"],
        )
        row = await pool.fetchrow(
            """
            UPDATE subscriptions
            SET
                amount              = $1,
                currency            = $2,
                next_renewal        = $3,
                status              = CASE WHEN $14::text = 'owner' OR status_source <> 'owner'
                                           THEN $4 ELSE status END,
                status_source       = CASE WHEN $14::text = 'owner' OR status_source = 'owner'
                                           THEN 'owner' ELSE 'extracted' END,
                auto_renew          = $5,
                payment_method      = COALESCE($6, payment_method),
                account_id          = COALESCE($7, account_id),
                source_message_id   = COALESCE($8, source_message_id),
                cancellation_url    = CASE
                    WHEN $9::text IS NULL THEN cancellation_url
                    WHEN $14::text = 'owner' THEN $9::text
                    WHEN cancellation_url_source = 'owner' AND cancellation_url IS NOT NULL
                        THEN cancellation_url
                    ELSE $9::text END,
                cancellation_url_source = CASE
                    WHEN $9::text IS NULL THEN cancellation_url_source
                    WHEN $14::text = 'owner' THEN 'owner'
                    WHEN cancellation_url_source = 'owner' AND cancellation_url IS NOT NULL
                        THEN 'owner'
                    ELSE 'extracted' END,
                notice_period_days  = COALESCE($10, notice_period_days),
                cancel_by           = COALESCE($11, cancel_by),
                metadata            = (metadata - 'expected_signal_source') || $12,
                service_source      = CASE WHEN $14::text = 'owner' THEN 'owner'
                                           ELSE service_source END,
                field_claims        = $15::jsonb,
                renewal_source      = $16,
                price_source        = $17,
                date_epoch          = $18,
                price_epoch         = $19,
                value_revision      = value_revision + 1,
                updated_at          = now()
            WHERE id = $13
            RETURNING *
            """,
            amount,
            currency,
            renewal_date,
            status,
            auto_renew,
            payment_method,
            account_uuid,
            source_message_id,
            cancellation_url,
            notice_period_days,
            cancel_by_date,
            metadata_value,
            existing["id"],
            write_source,
            fields["field_claims"],
            fields.get("renewal_source", "extracted"),
            fields.get("price_source", "extracted"),
            fields.get("date_epoch", 0),
            fields.get("price_epoch", 0),
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO subscriptions (
                service, amount, currency, frequency, next_renewal, status,
                auto_renew, payment_method, account_id, source_message_id,
                cancellation_url, notice_period_days, cancel_by, metadata,
                cancellation_url_source, status_source, renewal_source, price_source, service_source
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                    $15, $15, $15, $15, $15)
            RETURNING *
            """,
            service,
            amount,
            currency,
            frequency,
            renewal_date,
            status,
            auto_renew,
            payment_method,
            account_uuid,
            source_message_id,
            cancellation_url,
            notice_period_days,
            cancel_by_date,
            metadata_value,
            write_source,
        )

    return _row_to_dict(row)
