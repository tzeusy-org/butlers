"""Finance butler alert system — configure alert rules and detect price changes.

Alert configurations are stored as memory facts with predicate='alert_config'
in the facts table. This avoids creating a dedicated alerts table and integrates
with the existing fact-layer infrastructure.

Supported alert types:
  - large_transaction  : Flag transactions above an amount threshold
  - budget_exceeded    : Flag when spending exceeds a budget category
  - new_merchant       : Flag transactions from merchants not seen before
  - price_change       : Flag when a tracked subscription amount changes

detect_price_changes() compares recent transaction amounts for tracked
subscription merchants against the recorded amounts in finance.subscriptions.
Changes greater than 5% are flagged as price changes.

register_obligations() derives a forward obligation ledger row (warn-by date,
unknown-door flag, pre-charge price-change flag) for every active
subscription's next renewal -- see finance.obligation_ledger.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PREDICATE_ALERT_CONFIG = "alert_config"

_VALID_ALERT_TYPES = frozenset(
    {"large_transaction", "budget_exceeded", "new_merchant", "price_change"}
)

# Minimum price change fraction to flag as significant (5%)
_PRICE_CHANGE_THRESHOLD = Decimal("0.05")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert asyncpg Record to dict with basic type serialization."""
    import uuid

    d = dict(row)
    for key, val in d.items():
        if isinstance(val, uuid.UUID):
            d[key] = str(val)
        elif isinstance(val, datetime):
            d[key] = val.isoformat()
    return d


def _parse_alert_fact(row: asyncpg.Record) -> dict[str, Any]:
    """Parse an alert_config fact row into a structured alert dict.

    The fact content field holds the alert type, and the metadata JSONB
    holds threshold, currency, and enabled fields.
    """
    import uuid

    fact_id = str(row["id"]) if isinstance(row["id"], uuid.UUID) else row["id"]
    alert_type = row["content"]

    raw_metadata = row["metadata"]
    if isinstance(raw_metadata, str):
        try:
            metadata = json.loads(raw_metadata)
        except (json.JSONDecodeError, ValueError):
            metadata = {}
    elif isinstance(raw_metadata, dict):
        metadata = raw_metadata
    else:
        metadata = {}

    return {
        "type": alert_type,
        "threshold": metadata.get("threshold"),
        "currency": metadata.get("currency", "USD"),
        "enabled": metadata.get("enabled", True),
        "fact_id": fact_id,
    }


# ---------------------------------------------------------------------------
# alert_configure
# ---------------------------------------------------------------------------


async def alert_configure(
    pool: asyncpg.Pool,
    alert_type: str,
    threshold: float | None = None,
    currency: str = "USD",
    enabled: bool = True,
) -> dict[str, Any]:
    """Configure or update a spending alert rule.

    Stores alert configuration as a memory fact with predicate='alert_config'.
    Supersedes any existing alert configuration of the same type via the
    (subject, predicate) uniqueness contract of the facts table.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    alert_type:
        Alert type identifier. One of: large_transaction, budget_exceeded,
        new_merchant, price_change.
    threshold:
        Amount threshold for large_transaction alerts. Ignored for other types.
    currency:
        ISO-4217 currency code (default "USD").
    enabled:
        Whether the alert is active (default True).

    Returns
    -------
    dict
        {type, threshold, currency, enabled, fact_id}
    """
    if alert_type not in _VALID_ALERT_TYPES:
        raise ValueError(
            f"Invalid alert type {alert_type!r}. Must be one of {sorted(_VALID_ALERT_TYPES)}"
        )
    if alert_type == "large_transaction" and threshold is None:
        raise ValueError("threshold is required for large_transaction alerts")

    metadata: dict[str, Any] = {
        "currency": currency,
        "enabled": enabled,
    }
    if threshold is not None:
        metadata["threshold"] = threshold

    # Supersede any existing alert_config fact for the same type.
    # subject = alert type ensures one config per alert type.
    existing = await pool.fetchrow(
        """
        SELECT id FROM facts
        WHERE subject = $1
          AND predicate = $2
          AND validity = 'active'
          AND valid_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        alert_type,
        _PREDICATE_ALERT_CONFIG,
    )

    now = datetime.now(UTC)

    if existing is not None:
        # Supersede existing fact atomically: UPDATE old fact first so the unique
        # partial index on active (scope, subject, predicate, valid_at) facts is
        # not violated when inserting the new active row.
        old_id = existing["id"]
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "UPDATE facts SET validity = 'superseded' WHERE id = $1",
                    old_id,
                )
                new_row = await conn.fetchrow(
                    """
                    INSERT INTO facts (subject, predicate, content, importance, permanence,
                                       scope, metadata, supersedes_id, created_at, observed_at)
                    VALUES ($1, $2, $3, 7.0, 'stable', 'global', $4, $5, $6, $6)
                    RETURNING id
                    """,
                    alert_type,
                    _PREDICATE_ALERT_CONFIG,
                    alert_type,
                    metadata,
                    old_id,
                    now,
                )
        fact_id = str(new_row["id"])
    else:
        new_row = await pool.fetchrow(
            """
            INSERT INTO facts (subject, predicate, content, importance, permanence,
                               scope, metadata, created_at, observed_at)
            VALUES ($1, $2, $3, 7.0, 'stable', 'global', $4, $5, $5)
            RETURNING id
            """,
            alert_type,
            _PREDICATE_ALERT_CONFIG,
            alert_type,
            metadata,
            now,
        )
        fact_id = str(new_row["id"])

    return {
        "type": alert_type,
        "threshold": threshold,
        "currency": currency,
        "enabled": enabled,
        "fact_id": fact_id,
    }


# ---------------------------------------------------------------------------
# alert_list
# ---------------------------------------------------------------------------


async def alert_list(pool: asyncpg.Pool) -> dict[str, Any]:
    """Return all configured alert rules.

    Queries the facts table for active alert_config facts and returns
    a structured list of alert configurations.

    Parameters
    ----------
    pool:
        asyncpg connection pool.

    Returns
    -------
    dict
        {alerts: [{type, threshold, currency, enabled, fact_id}], total}
    """
    rows = await pool.fetch(
        """
        SELECT id, subject, content, metadata
        FROM facts
        WHERE predicate = $1
          AND validity = 'active'
          AND valid_at IS NULL
        ORDER BY created_at ASC
        """,
        _PREDICATE_ALERT_CONFIG,
    )

    alerts = [_parse_alert_fact(row) for row in rows]
    return {
        "alerts": alerts,
        "total": len(alerts),
    }


# ---------------------------------------------------------------------------
# Large transaction alert evaluation
# ---------------------------------------------------------------------------


async def get_large_transaction_alert_config(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Return the active+enabled ``large_transaction`` alert config, or None.

    Reuses the same ``alert_config`` fact lookup contract as ``alert_list`` /
    ``alert_configure`` (predicate=``alert_config``, content=``large_transaction``)
    so that the threshold surfaced to transaction recording is the single source
    of truth set via ``alert_configure``.

    Returns the parsed config dict (``{type, threshold, currency, enabled,
    fact_id}``) only when a large_transaction alert_config fact exists, is the
    active (non-superseded) row, is enabled, and carries a numeric threshold.
    Otherwise returns ``None`` so callers skip the flag.
    """
    row = await pool.fetchrow(
        """
        SELECT id, subject, content, metadata
        FROM facts
        WHERE predicate = $1
          AND content = 'large_transaction'
          AND validity = 'active'
          AND valid_at IS NULL
        ORDER BY created_at DESC
        LIMIT 1
        """,
        _PREDICATE_ALERT_CONFIG,
    )
    if row is None:
        return None

    config = _parse_alert_fact(row)
    if not config.get("enabled", True):
        return None
    if config.get("threshold") is None:
        return None
    return config


def evaluate_large_transaction_alert(
    amount: Decimal | float | int,
    merchant: str,
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build the ``large_transaction_alert`` flag for a recorded transaction.

    Compares the transaction's absolute amount against the configured
    ``large_transaction`` threshold. Returns the flag dict required by the
    finance-alerts spec (``threshold``, ``amount``, ``merchant``, ``exceeds_by``)
    when the amount exceeds the threshold, otherwise ``None``.

    ``config`` is the dict returned by :func:`get_large_transaction_alert_config`
    (or ``None`` when no enabled large_transaction alert is configured).
    """
    if not config:
        return None
    threshold = config.get("threshold")
    if threshold is None:
        return None

    amount_abs = abs(Decimal(str(amount)))
    threshold_dec = Decimal(str(threshold))
    if amount_abs <= threshold_dec:
        return None

    return {
        "threshold": float(threshold_dec),
        "amount": float(amount_abs),
        "merchant": merchant,
        "exceeds_by": float(amount_abs - threshold_dec),
    }


# ---------------------------------------------------------------------------
# detect_price_changes
# ---------------------------------------------------------------------------


async def detect_price_changes(
    pool: asyncpg.Pool,
    days_back: int = 60,
) -> dict[str, Any]:
    """Detect price changes in tracked subscription charges.

    Compares recent transaction amounts for tracked subscription merchants
    against their recorded amounts in the subscriptions table. Flags
    changes greater than 5%.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    days_back:
        How many days back to scan for recent charges (default 60).

    Returns
    -------
    dict
        {changes: [{service, tracked_amount, recent_charge, change_pct,
                    direction, last_seen_at}], total}
    """
    since = datetime.now(UTC) - timedelta(days=days_back)

    # Fetch all active subscriptions
    subscriptions = await pool.fetch(
        """
        SELECT id, service, amount, currency, status
        FROM subscriptions
        WHERE status = 'active'
        ORDER BY service
        """,
    )

    if not subscriptions:
        return {"changes": [], "total": 0}

    changes: list[dict[str, Any]] = []

    for sub in subscriptions:
        service = sub["service"]
        tracked_amount = Decimal(str(sub["amount"]))
        currency = sub["currency"]

        # Find the most recent transaction for this merchant (ILIKE match)
        # within the look-back window. Escape LIKE metacharacters in the service
        # name so that '%' or '_' in the name do not broaden the search.
        escaped_service = service.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        recent_rows = await pool.fetch(
            """
            SELECT amount, posted_at
            FROM transactions
            WHERE merchant ILIKE $1 ESCAPE '\\'
              AND posted_at >= $2
              AND direction = 'debit'
              AND deleted_at IS NULL
            ORDER BY posted_at DESC
            LIMIT 10
            """,
            f"%{escaped_service}%",
            since,
        )

        if not recent_rows:
            continue

        # Use most-recent charge for comparison (task spec: "compare recent charges")
        most_recent = recent_rows[0]
        recent_amount = Decimal(str(most_recent["amount"]))
        last_seen_at = most_recent["posted_at"]

        if tracked_amount == Decimal("0"):
            # Avoid division by zero; flag if any charge detected
            if recent_amount > Decimal("0"):
                changes.append(
                    {
                        "service": service,
                        "tracked_amount": float(tracked_amount),
                        "recent_charge": float(recent_amount),
                        "change_pct": None,
                        "direction": "increase",
                        "currency": currency,
                        "last_seen_at": (
                            last_seen_at.isoformat()
                            if isinstance(last_seen_at, datetime)
                            else str(last_seen_at)
                        ),
                    }
                )
            continue

        change_fraction = abs(recent_amount - tracked_amount) / tracked_amount

        if change_fraction > _PRICE_CHANGE_THRESHOLD:
            direction = "increase" if recent_amount > tracked_amount else "decrease"
            change_pct = float((recent_amount - tracked_amount) / tracked_amount * Decimal("100"))
            changes.append(
                {
                    "service": service,
                    "tracked_amount": float(tracked_amount),
                    "recent_charge": float(recent_amount),
                    "change_pct": round(change_pct, 2),
                    "direction": direction,
                    "currency": currency,
                    "last_seen_at": (
                        last_seen_at.isoformat()
                        if isinstance(last_seen_at, datetime)
                        else str(last_seen_at)
                    ),
                }
            )

    return {
        "changes": changes,
        "total": len(changes),
    }


# ---------------------------------------------------------------------------
# register_obligations
# ---------------------------------------------------------------------------


async def register_obligations(pool: asyncpg.Pool) -> dict[str, Any]:
    """Derive and upsert a forward obligation ledger row for every active
    subscription's next renewal (bu-8cdl1.10 slice 2).

    For each active subscription, registers one ``obligation_ledger`` row
    keyed by ``(subscription_id, period)`` where ``period`` is the
    subscription's current ``next_renewal`` date:

    - ``warn_by`` = ``cancel_by - notice_period_days`` when all three Slice-1
      cancellation-door fields (``cancellation_url``, ``notice_period_days``,
      ``cancel_by``) are known.
    - ``unknown_door`` = True (and ``warn_by`` left NULL) when any one of
      those three door fields is missing -- a URL-less door is exactly as
      unusable to the owner as a missing date -- so the owner is prompted to
      enrich it rather than the obligation silently warning at the wrong
      time or not at all.
    - ``price_change_amount``/``price_change_direction`` are set when the
      subscription's ``metadata`` carries a ``next_amount`` that differs from
      the currently tracked amount -- a pre-charge warning, ahead of
      :func:`detect_price_changes`'s post-charge comparison against posted
      transactions.

    Idempotent: re-running for the same subscription and period upserts the
    existing row in place rather than inserting a duplicate.

    Parameters
    ----------
    pool:
        asyncpg connection pool.

    Returns
    -------
    dict
        {registered, unknown_door, price_changes}: counts of obligation rows
        upserted this run, how many carry the unknown-door flag, and how many
        carry a pre-charge price-change flag.
    """
    subscriptions = await pool.fetch(
        """
        SELECT id, amount, next_renewal, cancellation_url, notice_period_days,
               cancel_by, metadata
        FROM subscriptions
        WHERE status = 'active'
        """
    )

    registered = 0
    unknown_door_count = 0
    price_change_count = 0

    for sub in subscriptions:
        period = sub["next_renewal"]
        cancellation_url = sub["cancellation_url"]
        notice_period_days = sub["notice_period_days"]
        cancel_by = sub["cancel_by"]

        if (
            cancellation_url is not None
            and cancel_by is not None
            and notice_period_days is not None
        ):
            warn_by = cancel_by - timedelta(days=notice_period_days)
            unknown_door = False
        else:
            warn_by = None
            unknown_door = True
            unknown_door_count += 1

        raw_metadata = sub["metadata"]
        if isinstance(raw_metadata, str):
            try:
                metadata = json.loads(raw_metadata)
            except (json.JSONDecodeError, ValueError):
                metadata = {}
        elif isinstance(raw_metadata, dict):
            metadata = raw_metadata
        else:
            metadata = {}

        price_change_amount = None
        price_change_direction = None
        next_amount_raw = metadata.get("next_amount")
        if next_amount_raw is not None:
            try:
                next_amount = Decimal(str(next_amount_raw))
            except Exception:
                next_amount = None
            tracked_amount = Decimal(str(sub["amount"]))
            if next_amount is not None and next_amount != tracked_amount:
                price_change_amount = next_amount
                price_change_direction = "increase" if next_amount > tracked_amount else "decrease"
                price_change_count += 1

        await pool.execute(
            """
            INSERT INTO obligation_ledger (
                subscription_id, period, warn_by, unknown_door,
                price_change_amount, price_change_direction
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (subscription_id, period) DO UPDATE SET
                warn_by                 = EXCLUDED.warn_by,
                unknown_door            = EXCLUDED.unknown_door,
                price_change_amount     = EXCLUDED.price_change_amount,
                price_change_direction  = EXCLUDED.price_change_direction,
                updated_at              = now()
            """,
            sub["id"],
            period,
            warn_by,
            unknown_door,
            price_change_amount,
            price_change_direction,
        )
        registered += 1

    return {
        "registered": registered,
        "unknown_door": unknown_door_count,
        "price_changes": price_change_count,
    }
