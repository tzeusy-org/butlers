"""Finance butler pattern recognition — recurring charge detection, merchant categorization,
and bill prediction.

Provides analytical functions for detecting recurring charges, learning merchant
category mappings, and predicting upcoming bills from transaction history.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
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

# predict_bills constants
_MIN_OCCURRENCES = 3  # minimum charges needed to detect a bill pattern
_AMOUNT_VARIANCE_THRESHOLD = 0.10  # 10% max variance for predict_bills amount consistency
_BILL_DRIFT_THRESHOLD = 0.10  # 10% drift to flag amount_drift


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


def _median_interval_days(dates: list[date]) -> float | None:
    """Return the median number of days between consecutive *sorted* dates.

    Returns *None* when fewer than 2 dates are supplied (interval undefined).
    """
    if len(dates) < 2:
        return None
    sorted_dates = sorted(dates)
    intervals = [(sorted_dates[i + 1] - sorted_dates[i]).days for i in range(len(sorted_dates) - 1)]
    return statistics.median(intervals)


def _median_amount(amounts: list[Decimal]) -> Decimal:
    """Return median amount from a list of Decimal values."""
    sorted_amounts = sorted(amounts)
    n = len(sorted_amounts)
    mid = n // 2
    if n % 2 == 0:
        return (sorted_amounts[mid - 1] + sorted_amounts[mid]) / 2
    return sorted_amounts[mid]


def _amount_variance_fraction(amounts: list[Decimal]) -> float:
    """Return the fraction of variation as (max - min) / median.

    Returns 0.0 for single-element lists.
    """
    if len(amounts) <= 1:
        return 0.0
    med = _median_amount(amounts)
    if med == 0:
        return 0.0
    return float((max(amounts) - min(amounts)) / med)


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

    # Fetch debit transactions only for merchants that already meet min_occurrences.
    # The HAVING clause pre-filters at the SQL level, avoiding expensive transfer of
    # all transactions for merchants that will never qualify.
    rows = await pool.fetch(
        f"""
        SELECT merchant, posted_at, amount, currency
        FROM transactions
        WHERE direction = 'debit'
          {deleted_filter}
          AND merchant IN (
              SELECT merchant
              FROM transactions
              WHERE direction = 'debit'
                {deleted_filter}
              GROUP BY merchant
              HAVING COUNT(*) >= $1
          )
        ORDER BY merchant ASC, posted_at ASC
        """,
        min_occurrences,
    )

    if not rows:
        return {
            "patterns": [],
            "total_detected": 0,
            "status": "insufficient_data",
            "as_of": datetime.now(UTC).isoformat(),
        }

    # Group by merchant.
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
            # Use exact match or whole-word containment (min 4 chars) to avoid
            # spurious matches like "a" matching any merchant name.
            if sub_name == merchant_lower or (
                len(sub_name) >= 4 and (sub_name in merchant_lower or merchant_lower in sub_name)
            ):
                already_tracked = True
                sub_amount = sub_data["amount"]
                if sub_amount > 0:
                    price_diff = abs(avg_amount - sub_amount) / sub_amount
                    if price_diff > Decimal(str(_PRICE_CHANGE_THRESHOLD)):
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
        # Batch-fetch transaction merchants and all active mappings to avoid N+1.
        txn_rows = await pool.fetch(
            """
            SELECT id::text, merchant FROM transactions WHERE id = ANY($1::uuid[])
            """,
            transaction_ids,
        )
        txn_by_id = {r["id"]: r["merchant"] for r in txn_rows}

        # Pre-fetch all active mappings once (already fetched if `merchant` was provided,
        # but merchant_mappings is typically small enough to fetch unconditionally).
        all_active_mappings = await pool.fetch(
            """
            SELECT merchant, category, confidence
            FROM merchant_mappings
            WHERE is_active = true
            ORDER BY confidence DESC
            """
        )

        for txn_id in transaction_ids:
            merchant_name = txn_by_id.get(txn_id)
            if merchant_name is None:
                continue
            merchant_lower = merchant_name.lower()
            # Match in Python — avoids one query per transaction and ensures is_active
            # is applied uniformly (the original per-row SQL had an operator-precedence
            # bug where AND bound tighter than OR, letting inactive mappings leak through).
            for m in all_active_mappings:
                m_lower = m["merchant"].lower()
                if m_lower in merchant_lower or merchant_lower in m_lower:
                    suggestions.append(
                        {
                            "transaction_id": txn_id,
                            "merchant": merchant_name,
                            "suggested_category": m["category"],
                            "confidence": float(m["confidence"]),
                        }
                    )
                    break  # already ordered by confidence DESC

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
    3+ regular payments.  For each qualifying payee the function computes:

    - **predicted_date**  : last_payment_date + median_interval_days
    - **predicted_amount**: median amount across historical charges
    - **is_tracked**      : True when a matching ``finance.bills`` record with
                            ``status IN ('pending', 'overdue')`` or a matching
                            ``finance.subscriptions`` record with
                            ``status = 'active'`` exists.
    - **amount_drift**    : True when the predicted amount differs from the
                            tracked bill/subscription amount by more than 10%.

    Only predictions whose predicted_date falls within ``days_ahead`` days from
    today are included in the response.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    days_ahead:
        Number of days from today to look ahead. Default: 30.

    Returns
    -------
    dict
        Keys: ``as_of``, ``window_days``, ``predictions``, ``status``.

        Each prediction includes: ``payee``, ``predicted_date``,
        ``predicted_amount``, ``currency``, ``median_interval_days``,
        ``occurrences``, ``last_payment_date``, ``is_tracked``,
        ``amount_drift``.
    """
    now = datetime.now(UTC)
    today = now.date()
    horizon = today + timedelta(days=days_ahead)

    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'transactions' AND column_name = 'deleted_at'
        )
        """
    )
    deleted_filter = "AND deleted_at IS NULL" if has_deleted_at else ""

    # Fetch all debit transactions for payees with enough history.
    rows = await pool.fetch(
        f"""
        SELECT merchant,
               array_agg(posted_at::date ORDER BY posted_at) AS dates,
               array_agg(amount ORDER BY posted_at)          AS amounts,
               COUNT(*) AS cnt,
               MAX(currency) AS currency
        FROM transactions
        WHERE direction = 'debit'
          {deleted_filter}
        GROUP BY merchant
        HAVING COUNT(*) >= $1
        ORDER BY merchant
        """,
        _MIN_OCCURRENCES,
    )

    if not rows:
        return {
            "as_of": now.isoformat(),
            "window_days": days_ahead,
            "predictions": [],
            "status": "insufficient_data",
        }

    # Pre-fetch all pending/overdue bills and active subscriptions once to avoid
    # N+1 per-payee queries inside the loop.
    bills_lookup: dict[str, Decimal] = {}
    try:
        bill_rows = await pool.fetch(
            """
            SELECT payee, amount FROM bills
            WHERE status IN ('pending', 'overdue')
            ORDER BY due_date DESC
            """
        )
        for br in bill_rows:
            key = br["payee"].lower()
            if key not in bills_lookup:
                bills_lookup[key] = Decimal(str(br["amount"]))
    except asyncpg.UndefinedTableError:
        pass  # bills table not yet available

    subs_lookup: dict[str, Decimal] = {}
    try:
        sub_rows = await pool.fetch(
            """
            SELECT service, amount FROM subscriptions
            WHERE status = 'active'
            ORDER BY updated_at DESC
            """
        )
        for sr in sub_rows:
            key = sr["service"].lower()
            if key not in subs_lookup:
                subs_lookup[key] = Decimal(str(sr["amount"]))
    except asyncpg.UndefinedTableError:
        pass  # subscriptions table not yet available

    predictions: list[dict[str, Any]] = []

    for row in rows:
        dates: list[date] = list(row["dates"])
        amounts: list[Decimal] = [Decimal(str(a)) for a in row["amounts"]]
        merchant = row["merchant"]
        currency = row["currency"] or "USD"

        # Check amount consistency — skip highly irregular payees.
        variance = _amount_variance_fraction(amounts)
        if variance > _AMOUNT_VARIANCE_THRESHOLD:
            continue

        # Compute median interval.
        median_days = _median_interval_days(dates)
        if median_days is None or median_days < 1:
            continue

        last_date = max(dates)
        predicted_date = last_date + timedelta(days=round(median_days))

        # Only include predictions within the requested horizon.
        if predicted_date < today or predicted_date > horizon:
            continue

        predicted_amount = _median_amount(amounts)

        # is_tracked flag (use pre-fetched lookups, no per-payee queries).
        merchant_lower = merchant.lower()
        tracked_amount: Decimal | None = bills_lookup.get(merchant_lower)
        is_tracked = tracked_amount is not None
        if not is_tracked:
            tracked_amount = subs_lookup.get(merchant_lower)
            is_tracked = tracked_amount is not None

        # amount_drift flag.
        amount_drift = False
        if is_tracked and tracked_amount is not None and tracked_amount > 0:
            drift = abs(predicted_amount - tracked_amount) / tracked_amount
            amount_drift = float(drift) > _BILL_DRIFT_THRESHOLD

        predictions.append(
            {
                "payee": merchant,
                "predicted_date": predicted_date.isoformat(),
                "predicted_amount": str(predicted_amount.quantize(Decimal("0.01"))),
                "currency": currency,
                "median_interval_days": round(median_days, 1),
                "occurrences": len(dates),
                "last_payment_date": last_date.isoformat(),
                "is_tracked": is_tracked,
                "amount_drift": amount_drift,
            }
        )

    # Sort by predicted_date ascending.
    predictions.sort(key=lambda p: p["predicted_date"])

    status = "ok" if predictions else "insufficient_data"
    return {
        "as_of": now.isoformat(),
        "window_days": days_ahead,
        "predictions": predictions,
        "status": status,
    }
