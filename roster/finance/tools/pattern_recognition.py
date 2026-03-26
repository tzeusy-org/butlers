"""Finance butler pattern recognition — recurring charge detection, merchant categorization.

Provides analytical functions for detecting recurring charges, learning merchant
category mappings, and predicting upcoming bills from transaction history.
"""

from __future__ import annotations

import logging
import statistics
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Interval tolerance: two intervals are "consistent" if they differ by <= this fraction.
_INTERVAL_TOLERANCE = 0.20  # 20% tolerance for interval regularity

# Amount variance thresholds for confidence scoring.
_HIGH_CONFIDENCE_VARIANCE = 0.05  # < 5% amount variance → high confidence
_MEDIUM_CONFIDENCE_VARIANCE = 0.10  # < 10% amount variance → medium confidence

# Minimum occurrences required to qualify as recurring for confidence tiers.
_HIGH_CONFIDENCE_MIN_COUNT = 6
_MEDIUM_CONFIDENCE_MIN_COUNT = 3

# Price change detection threshold: > 5% difference triggers price_change_detected.
_PRICE_CHANGE_THRESHOLD = 0.05

# Known frequency ranges in days (min, max) for frequency classification.
_FREQUENCY_RANGES: list[tuple[str, int, int]] = [
    ("weekly", 5, 9),
    ("monthly", 25, 35),
    ("quarterly", 80, 100),
    ("yearly", 350, 380),
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _classify_frequency(median_interval_days: float) -> str:
    """Classify a median interval (in days) into a named frequency bucket."""
    for name, low, high in _FREQUENCY_RANGES:
        if low <= median_interval_days <= high:
            return name
    return "custom"


def _amount_variance(amounts: list[Decimal]) -> float:
    """Return the coefficient of variation (stddev / mean) for a list of amounts.

    Returns 0.0 for a single-element list (no variance).
    """
    if len(amounts) <= 1:
        return 0.0
    mean = float(sum(amounts)) / len(amounts)
    if mean == 0:
        return 0.0
    std = statistics.stdev(float(a) for a in amounts)
    return std / mean


def _confidence_level(count: int, variance: float) -> str:
    """Compute confidence level from occurrence count and amount variance.

    Rules (evaluated in priority order):
    - high:   6+ occurrences AND < 5% amount variance
    - medium: 3+ occurrences AND < 10% amount variance
    - low:    otherwise
    """
    if count >= _HIGH_CONFIDENCE_MIN_COUNT and variance < _HIGH_CONFIDENCE_VARIANCE:
        return "high"
    if count >= _MEDIUM_CONFIDENCE_MIN_COUNT and variance < _MEDIUM_CONFIDENCE_VARIANCE:
        return "medium"
    return "low"


def _intervals_are_regular(intervals_days: list[float]) -> bool:
    """Return True when interval consistency is within the tolerance window.

    Uses coefficient of variation of the intervals list.  A fully regular
    sequence (e.g. exactly 30 days every time) has CV = 0.  We allow up to
    ``_INTERVAL_TOLERANCE`` (20%) CV to handle real-world jitter (billing dates
    that shift by a few days, weekends, etc.).
    """
    if len(intervals_days) < 2:
        # Only one interval — cannot assess regularity from a single gap.
        return True
    mean = statistics.mean(intervals_days)
    if mean == 0:
        return False
    std = statistics.stdev(intervals_days)
    cv = std / mean
    return cv <= _INTERVAL_TOLERANCE


# ---------------------------------------------------------------------------
# 4.1–4.5  detect_recurring
# ---------------------------------------------------------------------------


async def detect_recurring(
    pool: asyncpg.Pool,
    min_occurrences: int = 3,
) -> dict[str, Any]:
    """Detect merchants with recurring charges from transaction history.

    Queries ``finance.transactions WHERE deleted_at IS NULL``, groups by
    merchant, and evaluates each group for regular charge intervals and
    consistent amounts (within 10% variance).  Detected patterns are stored
    (upserted) into ``finance.recurring_groups``.

    Confidence scoring:
    - ``high``:   6+ occurrences AND amount variance < 5%
    - ``medium``: 3+ occurrences AND amount variance < 10%
    - ``low``:    otherwise

    Cross-references ``finance.subscriptions`` (if the table exists) to set:
    - ``already_tracked``: True when the merchant name matches an active subscription.
    - ``price_change_detected``: True when the detected average amount differs
      from the tracked subscription amount by more than 5%.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    min_occurrences:
        Minimum number of charges required to consider a merchant recurring.
        Default: 3.

    Returns
    -------
    dict
        ``{patterns: [...], total_detected, status, as_of}``

        Each pattern: ``{merchant, avg_amount, currency, estimated_frequency,
        occurrence_count, confidence, already_tracked, price_change_detected,
        last_seen_date, next_expected_date}``
    """
    # Check whether the transactions table has a deleted_at column.
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'transactions' AND column_name = 'deleted_at'
        )
        """
    )
    deleted_filter = "AND deleted_at IS NULL" if has_deleted_at else ""

    # Fetch all debit transactions grouped by merchant, ordered by date.
    # We need the individual posted_at dates and amounts to compute intervals.
    rows = await pool.fetch(
        f"""
        SELECT merchant, posted_at, amount, currency
        FROM transactions
        WHERE direction = 'debit'
          {deleted_filter}
        ORDER BY merchant ASC, posted_at ASC
        """
    )

    if not rows:
        return {
            "patterns": [],
            "total_detected": 0,
            "status": "insufficient_data",
            "as_of": datetime.now(UTC).isoformat(),
        }

    # Group by merchant.
    from collections import defaultdict

    merchant_data: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        merchant_data[row["merchant"]].append(
            {
                "posted_at": row["posted_at"],
                "amount": Decimal(str(row["amount"])),
                "currency": row["currency"],
            }
        )

    # Build subscription lookup for already_tracked / price_change_detected.
    subscriptions_by_merchant: dict[str, dict[str, Any]] = {}
    has_subscriptions = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'subscriptions'
        )
        """
    )
    if has_subscriptions:
        sub_rows = await pool.fetch(
            """
            SELECT service, amount, currency, status
            FROM subscriptions
            WHERE status = 'active'
            """
        )
        for sub in sub_rows:
            subscriptions_by_merchant[sub["service"].lower()] = {
                "amount": Decimal(str(sub["amount"])),
                "currency": sub["currency"],
            }

    # Build the recurring_groups table guard (CREATE IF NOT EXISTS).
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS recurring_groups (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant             TEXT NOT NULL UNIQUE,
            estimated_frequency  TEXT
                                     CHECK (estimated_frequency IS NULL OR estimated_frequency IN (
                                         'weekly', 'monthly', 'quarterly', 'yearly', 'custom'
                                     )),
            avg_amount           NUMERIC(14, 2) NOT NULL,
            currency             CHAR(3) DEFAULT 'USD',
            last_seen_date       DATE,
            next_expected_date   DATE,
            is_active            BOOLEAN NOT NULL DEFAULT true,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    patterns: list[dict[str, Any]] = []

    for merchant, charges in merchant_data.items():
        count = len(charges)
        if count < min_occurrences:
            continue

        amounts = [c["amount"] for c in charges]
        currency = charges[0]["currency"]  # Use currency from first charge

        # Compute amount variance.
        variance = _amount_variance(amounts)

        # Only accept merchants whose amount variance is within the 10% threshold.
        if variance > _MEDIUM_CONFIDENCE_VARIANCE:
            continue

        # Compute intervals in days between consecutive charges.
        posted_dates = [c["posted_at"] for c in charges]
        intervals_days: list[float] = []
        for i in range(1, len(posted_dates)):
            delta = (posted_dates[i] - posted_dates[i - 1]).total_seconds() / 86400.0
            if delta > 0:
                intervals_days.append(delta)

        if not intervals_days:
            continue

        # Check interval regularity.
        if not _intervals_are_regular(intervals_days):
            continue

        # Compute derived fields.
        avg_amount = Decimal(str(round(sum(float(a) for a in amounts) / len(amounts), 2)))
        median_interval = statistics.median(intervals_days)
        estimated_frequency = _classify_frequency(median_interval)
        confidence = _confidence_level(count, variance)

        last_seen = posted_dates[-1]
        last_seen_date = last_seen.date() if hasattr(last_seen, "date") else last_seen
        next_expected_date = last_seen_date + timedelta(days=round(median_interval))

        # Cross-reference subscriptions for already_tracked / price_change_detected.
        already_tracked = False
        price_change_detected = False
        merchant_lower = merchant.lower()
        for sub_name, sub_data in subscriptions_by_merchant.items():
            if sub_name in merchant_lower or merchant_lower in sub_name:
                already_tracked = True
                sub_amount = sub_data["amount"]
                if sub_amount > 0:
                    price_diff = abs(float(avg_amount) - float(sub_amount)) / float(sub_amount)
                    if price_diff > _PRICE_CHANGE_THRESHOLD:
                        price_change_detected = True
                break

        # Upsert into finance.recurring_groups.
        await pool.execute(
            """
            INSERT INTO recurring_groups (
                merchant, estimated_frequency, avg_amount, currency,
                last_seen_date, next_expected_date, is_active, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, true, now())
            ON CONFLICT (merchant)
            DO UPDATE SET
                estimated_frequency = EXCLUDED.estimated_frequency,
                avg_amount          = EXCLUDED.avg_amount,
                currency            = EXCLUDED.currency,
                last_seen_date      = EXCLUDED.last_seen_date,
                next_expected_date  = EXCLUDED.next_expected_date,
                is_active           = true,
                updated_at          = now()
            """,
            merchant,
            estimated_frequency,
            avg_amount,
            currency,
            last_seen_date,
            next_expected_date,
        )

        patterns.append(
            {
                "merchant": merchant,
                "avg_amount": str(avg_amount),
                "currency": currency,
                "estimated_frequency": estimated_frequency,
                "occurrence_count": count,
                "confidence": confidence,
                "already_tracked": already_tracked,
                "price_change_detected": price_change_detected,
                "last_seen_date": last_seen_date.isoformat(),
                "next_expected_date": next_expected_date.isoformat(),
            }
        )

    return {
        "patterns": patterns,
        "total_detected": len(patterns),
        "status": "ok" if patterns else "insufficient_data",
        "as_of": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# 2.1  learn_merchant_categories
# ---------------------------------------------------------------------------


async def learn_merchant_categories(
    pool: asyncpg.Pool,
) -> dict[str, Any]:
    """Aggregate category assignments per merchant and upsert into merchant_mappings.

    Queries ``finance.transactions WHERE deleted_at IS NULL``, computes the
    most frequently assigned category per merchant, and upserts the result into
    ``finance.merchant_mappings``.

    Returns
    -------
    dict
        ``{upserted: <count>, as_of}``
    """
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'transactions' AND column_name = 'deleted_at'
        )
        """
    )
    deleted_filter = "AND deleted_at IS NULL" if has_deleted_at else ""

    rows = await pool.fetch(
        f"""
        SELECT merchant, category, COUNT(*) AS freq
        FROM transactions
        WHERE direction = 'debit'
          {deleted_filter}
          AND category IS NOT NULL
        GROUP BY merchant, category
        ORDER BY merchant ASC, freq DESC
        """
    )

    # For each merchant, pick the most frequent category.
    merchant_category: dict[str, tuple[str, int]] = {}
    for row in rows:
        merchant = row["merchant"]
        category = row["category"]
        freq = int(row["freq"])
        if merchant not in merchant_category or freq > merchant_category[merchant][1]:
            merchant_category[merchant] = (category, freq)

    if not merchant_category:
        return {"upserted": 0, "as_of": datetime.now(UTC).isoformat()}

    # Ensure table exists.
    await pool.execute(
        """
        CREATE TABLE IF NOT EXISTS merchant_mappings (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            merchant        TEXT NOT NULL UNIQUE,
            category        TEXT NOT NULL,
            confidence      NUMERIC(5, 4) NOT NULL DEFAULT 0.5,
            sample_count    INT NOT NULL DEFAULT 1,
            is_active       BOOLEAN NOT NULL DEFAULT true,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    upserted = 0
    for merchant, (category, freq) in merchant_category.items():
        # Confidence is capped at 0.99; grows with sample count.
        confidence = min(0.99, 0.5 + (freq - 1) * 0.05)
        await pool.execute(
            """
            INSERT INTO merchant_mappings (merchant, category, confidence, sample_count)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (merchant)
            DO UPDATE SET
                category     = EXCLUDED.category,
                confidence   = EXCLUDED.confidence,
                sample_count = EXCLUDED.sample_count,
                updated_at   = now()
            """,
            merchant,
            category,
            confidence,
            freq,
        )
        upserted += 1

    return {"upserted": upserted, "as_of": datetime.now(UTC).isoformat()}


# ---------------------------------------------------------------------------
# 2.2  suggest_categories
# ---------------------------------------------------------------------------


async def suggest_categories(
    pool: asyncpg.Pool,
    transaction_ids: list[str] | None = None,
    merchant: str | None = None,
) -> dict[str, Any]:
    """Look up merchants in finance.merchant_mappings and return category suggestions.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    transaction_ids:
        Optional list of transaction UUIDs to look up.  When provided, the
        merchant name for each transaction is resolved from the transactions table.
    merchant:
        Optional direct merchant name pattern for ILIKE lookup.

    Returns
    -------
    dict
        ``{suggestions: [{merchant, category, confidence}], as_of}``
    """
    has_mappings = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'merchant_mappings'
        )
        """
    )
    if not has_mappings:
        return {"suggestions": [], "as_of": datetime.now(UTC).isoformat()}

    suggestions: list[dict[str, Any]] = []

    if merchant:
        rows = await pool.fetch(
            """
            SELECT merchant, category, confidence
            FROM merchant_mappings
            WHERE is_active = true AND merchant ILIKE $1
            ORDER BY confidence DESC
            """,
            f"%{merchant}%",
        )
        for row in rows:
            suggestions.append(
                {
                    "merchant": row["merchant"],
                    "category": row["category"],
                    "confidence": float(row["confidence"]),
                }
            )

    if transaction_ids:
        for txn_id in transaction_ids:
            txn = await pool.fetchrow(
                "SELECT merchant FROM transactions WHERE id = $1::uuid LIMIT 1",
                txn_id,
            )
            if txn is None:
                continue
            mapping = await pool.fetchrow(
                """
                SELECT merchant, category, confidence
                FROM merchant_mappings
                WHERE is_active = true AND merchant ILIKE $1
                ORDER BY confidence DESC
                LIMIT 1
                """,
                f"%{txn['merchant']}%",
            )
            if mapping:
                suggestions.append(
                    {
                        "transaction_id": txn_id,
                        "merchant": txn["merchant"],
                        "suggested_category": mapping["category"],
                        "confidence": float(mapping["confidence"]),
                    }
                )

    return {"suggestions": suggestions, "as_of": datetime.now(UTC).isoformat()}


# ---------------------------------------------------------------------------
# 2.2a  recall_merchant_mappings
# ---------------------------------------------------------------------------


async def recall_merchant_mappings(
    pool: asyncpg.Pool,
    merchant_pattern: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """Query learned merchant-to-category mappings with optional filters.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    merchant_pattern:
        Optional ILIKE pattern to filter by merchant name.
    category:
        Optional exact category name to filter by.

    Returns
    -------
    dict
        ``{mappings: [{merchant, category, confidence, sample_count}], as_of}``
    """
    has_mappings = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'merchant_mappings'
        )
        """
    )
    if not has_mappings:
        return {"mappings": [], "as_of": datetime.now(UTC).isoformat()}

    conditions = ["is_active = true"]
    params: list[Any] = []

    if merchant_pattern:
        params.append(f"%{merchant_pattern}%")
        conditions.append(f"merchant ILIKE ${len(params)}")

    if category:
        params.append(category)
        conditions.append(f"category = ${len(params)}")

    where_clause = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT merchant, category, confidence, sample_count
        FROM merchant_mappings
        WHERE {where_clause}
        ORDER BY confidence DESC, merchant ASC
        """,
        *params,
    )

    mappings = [
        {
            "merchant": row["merchant"],
            "category": row["category"],
            "confidence": float(row["confidence"]),
            "sample_count": row["sample_count"],
        }
        for row in rows
    ]
    return {"mappings": mappings, "as_of": datetime.now(UTC).isoformat()}


# ---------------------------------------------------------------------------
# 5.1  predict_bills
# ---------------------------------------------------------------------------


async def predict_bills(
    pool: asyncpg.Pool,
    days_ahead: int = 30,
) -> dict[str, Any]:
    """Predict upcoming bill payments from historical transaction patterns.

    Analyzes ``finance.transactions WHERE deleted_at IS NULL`` for payees with
    3+ regular payments and computes the predicted next payment date from the
    median interval.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    days_ahead:
        Number of days ahead to include predictions (default 30).

    Returns
    -------
    dict
        ``{predictions: [{merchant, predicted_date, predicted_amount, frequency,
        is_tracked, amount_drift}], as_of}``
    """
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'transactions' AND column_name = 'deleted_at'
        )
        """
    )
    deleted_filter = "AND deleted_at IS NULL" if has_deleted_at else ""

    rows = await pool.fetch(
        f"""
        SELECT merchant, posted_at, amount, currency
        FROM transactions
        WHERE direction = 'debit'
          {deleted_filter}
        ORDER BY merchant ASC, posted_at ASC
        """
    )

    if not rows:
        return {
            "predictions": [],
            "status": "insufficient_data",
            "as_of": datetime.now(UTC).isoformat(),
        }

    from collections import defaultdict

    merchant_data: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        merchant_data[row["merchant"]].append(
            {
                "posted_at": row["posted_at"],
                "amount": Decimal(str(row["amount"])),
                "currency": row["currency"],
            }
        )

    # Lookup tracked bills for is_tracked / amount_drift.
    bills_by_payee: dict[str, dict[str, Any]] = {}
    has_bills = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_name = 'bills'
        )
        """
    )
    if has_bills:
        bill_rows = await pool.fetch(
            """
            SELECT payee, amount, currency
            FROM bills
            WHERE status IN ('pending', 'overdue')
            """
        )
        for bill in bill_rows:
            bills_by_payee[bill["payee"].lower()] = {
                "amount": Decimal(str(bill["amount"])),
                "currency": bill["currency"],
            }

    today = datetime.now(UTC).date()
    horizon = today + timedelta(days=days_ahead)
    predictions: list[dict[str, Any]] = []

    for merchant, charges in merchant_data.items():
        if len(charges) < 3:
            continue

        posted_dates = [c["posted_at"] for c in charges]
        amounts = [c["amount"] for c in charges]
        currency = charges[0]["currency"]

        intervals_days: list[float] = []
        for i in range(1, len(posted_dates)):
            delta = (posted_dates[i] - posted_dates[i - 1]).total_seconds() / 86400.0
            if delta > 0:
                intervals_days.append(delta)

        if not intervals_days:
            continue

        if not _intervals_are_regular(intervals_days):
            continue

        median_interval = statistics.median(intervals_days)
        last_seen = posted_dates[-1]
        last_seen_date = last_seen.date() if hasattr(last_seen, "date") else last_seen
        predicted_date = last_seen_date + timedelta(days=round(median_interval))

        if not (today <= predicted_date <= horizon):
            continue

        avg_amount = Decimal(str(round(sum(float(a) for a in amounts) / len(amounts), 2)))
        frequency = _classify_frequency(median_interval)

        # is_tracked / amount_drift.
        is_tracked = False
        amount_drift: str | None = None
        merchant_lower = merchant.lower()
        for payee_name, bill_data in bills_by_payee.items():
            if payee_name in merchant_lower or merchant_lower in payee_name:
                is_tracked = True
                tracked_amount = bill_data["amount"]
                if tracked_amount > 0:
                    drift = (float(avg_amount) - float(tracked_amount)) / float(tracked_amount)
                    if abs(drift) > 0.10:
                        amount_drift = f"{drift:+.1%}"
                break

        predictions.append(
            {
                "merchant": merchant,
                "predicted_date": predicted_date.isoformat(),
                "predicted_amount": str(avg_amount),
                "currency": currency,
                "frequency": frequency,
                "is_tracked": is_tracked,
                "amount_drift": amount_drift,
            }
        )

    predictions.sort(key=lambda x: x["predicted_date"])

    return {
        "predictions": predictions,
        "status": "ok" if predictions else "no_predictions",
        "as_of": datetime.now(UTC).isoformat(),
    }
