"""Finance butler subscription tools — create and update recurring service commitments."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import asyncpg

from butlers.tools.finance._helpers import _row_to_dict
from butlers.tools.finance.expected_signals import (
    FinanceSignalSource,
    metadata_with_signal_source,
)

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
        URL where the subscription can be cancelled. Optional; may be
        populated later by owner enrichment.
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
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status {status!r}. Must be one of {_VALID_STATUSES}")
    if frequency not in _VALID_FREQUENCIES:
        raise ValueError(f"Invalid frequency {frequency!r}. Must be one of {_VALID_FREQUENCIES}")

    renewal_date = _normalize_renewal_date(next_renewal)
    cancel_by_date = _normalize_optional_date(cancel_by)
    metadata_value = metadata_with_signal_source(metadata, _expected_signal_source)
    account_uuid = uuid.UUID(str(account_id)) if account_id is not None else None

    # Upsert: look up existing record by (service, frequency)
    existing = await pool.fetchrow(
        "SELECT id FROM subscriptions WHERE service = $1 AND frequency = $2 LIMIT 1",
        service,
        frequency,
    )

    if existing is not None:
        row = await pool.fetchrow(
            """
            UPDATE subscriptions
            SET
                amount              = $1,
                currency            = $2,
                next_renewal        = $3,
                status              = $4,
                auto_renew          = $5,
                payment_method      = COALESCE($6, payment_method),
                account_id          = COALESCE($7, account_id),
                source_message_id   = COALESCE($8, source_message_id),
                cancellation_url    = COALESCE($9, cancellation_url),
                notice_period_days  = COALESCE($10, notice_period_days),
                cancel_by           = COALESCE($11, cancel_by),
                metadata            = (metadata - 'expected_signal_source') || $12,
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
        )
    else:
        row = await pool.fetchrow(
            """
            INSERT INTO subscriptions (
                service, amount, currency, frequency, next_renewal, status,
                auto_renew, payment_method, account_id, source_message_id,
                cancellation_url, notice_period_days, cancel_by, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
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
        )

    return _row_to_dict(row)
