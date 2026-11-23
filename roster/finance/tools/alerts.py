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

import asyncio
import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from butlers.core.goal_lineage import taint_insert

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
                    "UPDATE facts SET validity = 'superseded', dropped_at = CASE WHEN "
                    "validity IN ('superseded', 'expired') THEN dropped_at ELSE $2 END "
                    "WHERE id = $1",
                    old_id,
                    now,
                )
                taint = taint_insert(7)
                new_row = await conn.fetchrow(
                    f"""
                    INSERT INTO facts (subject, predicate, content, importance, permanence,
                                       scope, metadata, supersedes_id, created_at, observed_at
                                       {taint.columns})
                    VALUES ($1, $2, $3, 7.0, 'stable', 'global', $4, $5, $6, $6
                            {taint.placeholders})
                    RETURNING id
                    """,
                    alert_type,
                    _PREDICATE_ALERT_CONFIG,
                    alert_type,
                    metadata,
                    old_id,
                    now,
                    *taint.values,
                )
        fact_id = str(new_row["id"])
    else:
        taint = taint_insert(6)
        new_row = await pool.fetchrow(
            f"""
            INSERT INTO facts (subject, predicate, content, importance, permanence,
                               scope, metadata, created_at, observed_at{taint.columns})
            VALUES ($1, $2, $3, 7.0, 'stable', 'global', $4, $5, $5{taint.placeholders})
            RETURNING id
            """,
            alert_type,
            _PREDICATE_ALERT_CONFIG,
            alert_type,
            metadata,
            now,
            *taint.values,
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
# Owner-watch cover (bu-4c7nvp.1.5 S7)
# ---------------------------------------------------------------------------
#
# Trust: the Switchboard is the watch trust root. Finance trusts the cover answer and fails open
# on anything else, so the legacy flag still fires when the answer is missing, wrong-typed, late
# or an error. The check never raises into the caller.

_COVER_TIMEOUT_S = 2.0
_COVER_SQL = "SELECT public.owner_watch_covers_large_transaction($1::text, $2::numeric)"


def _count_cover_failure() -> None:
    try:
        from opentelemetry import metrics

        metrics.get_meter("butlers").create_counter(
            name="butlers.finance.owner_watch_cover_failed",
            description="Owner-watch cover reads that failed (the legacy alert fired instead)",
            unit="reads",
        ).add(1)
    except Exception:  # noqa: BLE001
        logger.debug("owner_watch_cover_failed counter unavailable", exc_info=True)


async def owner_watch_covers(executor: Any, currency: str, amount: Decimal | float | int) -> bool:
    """True only when the Switchboard says a live owner watch covers this transaction.

    Any other outcome (false, NULL, error, timeout, missing function or grant) is ``False``: the
    caller keeps its flag. A connection runs the read in a savepoint, so a failure cannot poison
    the caller's transaction.
    """
    try:
        args = (str(currency), Decimal(str(abs(Decimal(str(amount))))))
        if hasattr(executor, "transaction"):

            async def _read() -> Any:
                async with executor.transaction():
                    return await executor.fetchval(_COVER_SQL, *args)

            answer = await asyncio.wait_for(_read(), _COVER_TIMEOUT_S)
        else:
            answer = await asyncio.wait_for(executor.fetchval(_COVER_SQL, *args), _COVER_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "owner_watch_cover_unavailable error=%s; legacy alert fires", type(exc).__name__
        )
        _count_cover_failure()
        return False
    return answer is True


#: Alert types that an owner watch supersedes: the watch is the one voice for them.
_SUPERSEDED_ALERT_TYPES = ("price_change", "budget_exceeded", "new_merchant")


def _proposal_digest(watch: dict[str, Any]) -> str:
    canonical = json.dumps(watch, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def alert_watch_proposals(pool: asyncpg.Pool) -> dict[str, Any]:
    """Propose owner watches for the configured alerts. Read-only; creates nothing.

    An enabled ``large_transaction`` alert with a numeric threshold proposes one ``read_check``
    watch (same threshold and currency) for the Switchboard to park as an owner card. A disabled
    alert proposes nothing. The other alert types are superseded and propose no watch. ``digest``
    is stable for the same proposal, so parking it twice is idempotent.
    """
    listing = await alert_list(pool)
    proposals: list[dict[str, Any]] = []
    for alert in listing["alerts"]:
        kind = alert["type"]
        entry: dict[str, Any] = {"type": kind, "fact_id": alert["fact_id"]}
        if kind in _SUPERSEDED_ALERT_TYPES:
            entry.update(status="superseded", watch=None, digest=None)
        elif kind != "large_transaction" or not alert.get("enabled", True):
            entry.update(status="not_proposed", watch=None, digest=None)
        else:
            threshold = alert.get("threshold")
            currency = str(alert.get("currency") or "").lower()
            if (
                isinstance(threshold, bool)
                or not isinstance(threshold, int | float)
                or len(currency) != 3
                or not currency.isalpha()
            ):
                entry.update(status="not_proposed", watch=None, digest=None)
            else:
                watch = {
                    "tag": "read_check",
                    "capability": f"finance.transaction_amount.{currency}",
                    "field": "amount",
                    "op": "gt",
                    "value": threshold,
                }
                entry.update(status="proposed", watch=watch, digest=_proposal_digest(watch))
        proposals.append(entry)
    return {"proposals": proposals, "total": len(proposals)}


# ---------------------------------------------------------------------------
# transaction_amounts_since (the owner-watch read feed, bu-4c7nvp.1.5)
# ---------------------------------------------------------------------------

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_AMOUNTS_MAX_LIMIT = 100
#: A row committed this recently may still be in flight beside an older timestamp; the feed reads
#: only past this settle lag so a later commit is never skipped by an earlier cursor.
_AMOUNTS_SETTLE_LAG = timedelta(seconds=60)


async def transaction_amounts_since(
    pool: asyncpg.Pool, currency: str, after_us: int, limit: int
) -> dict[str, Any]:
    """The largest absolute amount recorded at each instant in one currency, after a cursor.

    Read-only and content-blind: each row is ``{id, amount, currency, recorded_us}`` and nothing
    else (no merchant, description, account or category). Rows page by insertion time
    (``created_at``, microseconds since the epoch), never by transaction date, so a late import of
    an old-dated transaction is not skipped. Rows created in the same instant (one import) are one
    reading, the largest. Deleted rows and rows inside the settle lag are excluded.
    """
    if type(currency) is not str or len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter code")
    if type(after_us) is not int or after_us < 0:
        raise ValueError("after_us must be a non-negative integer")
    if type(limit) is not int or not 1 <= limit <= _AMOUNTS_MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_AMOUNTS_MAX_LIMIT}")
    after = _EPOCH + timedelta(microseconds=after_us)
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (created_at) id, abs(amount) AS amount, currency, created_at
        FROM transactions
        WHERE deleted_at IS NULL
          AND upper(currency) = upper($1)
          AND created_at > $2
          AND created_at <= now() - $3::interval
        ORDER BY created_at, abs(amount) DESC, id
        LIMIT $4
        """,
        currency,
        after,
        _AMOUNTS_SETTLE_LAG,
        limit,
    )
    return {
        "rows": [
            {
                "id": str(row["id"]),
                "amount": f"{row['amount']:.2f}",
                "currency": str(row["currency"]).upper(),
                "recorded_us": (row["created_at"] - _EPOCH) // timedelta(microseconds=1),
            }
            for row in rows
        ]
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


async def register_obligations(pool: asyncpg.Pool, *, today: date | None = None) -> dict[str, Any]:
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
        SELECT id, amount, next_renewal, field_claims, cancellation_url, notice_period_days,
               cancel_by, metadata
        FROM subscriptions
        WHERE status = 'active'
        """
    )

    registered = 0
    unknown_door_count = 0
    price_change_count = 0

    for sub in subscriptions:
        from butlers.tools.finance.subscriptions import selected_renewal

        start = today or datetime.now(UTC).date()
        period = selected_renewal(sub, start, start + timedelta(days=14))
        if period is None:
            # Retain the baseline for the ledger outside the warning window; this does not
            # mint an elapsed renewal/overdue alert.
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
