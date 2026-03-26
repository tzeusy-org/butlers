"""Finance butler anomaly detection — statistical baselines and anomaly scanning.

Provides:
- ``compute_baselines``: per-merchant (median, stddev) and per-category
  (weekly velocity) baselines from a 6-month rolling window.  Results are
  stored as memory facts with ``predicate='spending_baseline'``.
- ``anomaly_scan``: compare recent transactions against baselines; flag
  amount anomalies, new merchants, and category velocity anomalies.
- ``detect_duplicates``: find same-merchant, same-amount transactions on
  the same or adjacent days (excluding known subscription charges).
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

# Rolling window for baseline computation (6 months ≈ 180 days).
_BASELINE_WINDOW_DAYS = 180

# Minimum number of transactions required per merchant to compute a baseline.
_MIN_MERCHANT_TRANSACTIONS = 3

# Minimum number of weekly data points to compute category velocity baseline.
_MIN_CATEGORY_WEEKS = 4

# Sensitivity multipliers for anomaly scoring — the factor by which a
# transaction must exceed the baseline stddev to be flagged.
# high = strictest (flag more), medium = default, low = permissive.
_SENSITIVITY_MULTIPLIERS: dict[str, float] = {
    "high": 1.5,
    "medium": 2.0,
    "low": 3.0,
}

# Duplicate detection window: look for duplicate candidates within this
# many days either side of the transaction date.
_DUPLICATE_DAY_WINDOW = 1

# Severity thresholds for amount anomalies expressed as multiples of stddev.
_SEVERITY_HIGH_MULTIPLIER = 3.0
_SEVERITY_MEDIUM_MULTIPLIER = 2.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_stddev(values: list[float]) -> float:
    """Return sample stddev or 0.0 for lists with fewer than 2 distinct values."""
    if len(values) < 2:
        return 0.0
    try:
        return statistics.stdev(values)
    except statistics.StatisticsError:
        return 0.0


def _safe_median(values: list[float]) -> float:
    """Return median or 0.0 for empty lists."""
    if not values:
        return 0.0
    return float(statistics.median(values))


def _severity_from_zscore(zscore: float) -> str:
    """Map a Z-score to a severity label."""
    if zscore >= _SEVERITY_HIGH_MULTIPLIER:
        return "high"
    if zscore >= _SEVERITY_MEDIUM_MULTIPLIER:
        return "medium"
    return "low"


async def _has_deleted_at(pool: asyncpg.Pool) -> bool:
    """Return True when ``finance.transactions`` has a ``deleted_at`` column."""
    return bool(
        await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'transactions'
                  AND column_name = 'deleted_at'
            )
            """
        )
    )


async def _subscriptions_lookup(pool: asyncpg.Pool) -> set[str]:
    """Return a set of lower-cased merchant names from active subscriptions."""
    try:
        rows = await pool.fetch("SELECT service FROM subscriptions WHERE status = 'active'")
        return {row["service"].lower() for row in rows}
    except asyncpg.UndefinedTableError:
        return set()


# ---------------------------------------------------------------------------
# 3.1  compute_baselines
# ---------------------------------------------------------------------------


async def compute_baselines(
    pool: asyncpg.Pool,
    memory_pool: asyncpg.Pool | None = None,
) -> dict[str, Any]:
    """Compute per-merchant and per-category spending baselines.

    Queries ``finance.transactions WHERE deleted_at IS NULL`` for the last
    6 months (``_BASELINE_WINDOW_DAYS``).  For each merchant with
    ``_MIN_MERCHANT_TRANSACTIONS`` or more transactions, computes:

    - ``median`` — median debit amount
    - ``stddev`` — sample standard deviation of debit amounts

    For each category with ``_MIN_CATEGORY_WEEKS`` or more weeks of data,
    computes:

    - ``weekly_velocity`` — mean of per-week aggregate spend

    Baselines are stored as memory facts (``predicate='spending_baseline'``)
    when ``memory_pool`` is provided.  When no memory pool is available
    (typical in tests), results are returned but not persisted.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the finance schema.
    memory_pool:
        Optional asyncpg connection pool for the memory / facts schema.
        When provided, baselines are upserted as ``public.facts`` rows with
        ``predicate='spending_baseline'``.  When ``None``, persistence is
        skipped.

    Returns
    -------
    dict
        ``{merchant_baselines, category_baselines, status, computed_at}``

        ``merchant_baselines``: list of ``{merchant, median, stddev, sample_count}``
        ``category_baselines``: list of ``{category, weekly_velocity, week_count}``
        ``status``: ``"ok"`` | ``"insufficient_data"``
        ``computed_at``: ISO timestamp
    """
    now = datetime.now(UTC)
    window_start = now - timedelta(days=_BASELINE_WINDOW_DAYS)
    deleted_filter = "AND deleted_at IS NULL" if await _has_deleted_at(pool) else ""

    # --- Per-merchant baselines ---
    merchant_rows = await pool.fetch(
        f"""
        SELECT
            merchant,
            array_agg(amount ORDER BY posted_at) AS amounts,
            COUNT(*) AS cnt
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          {deleted_filter}
          AND merchant IS NOT NULL
        GROUP BY merchant
        HAVING COUNT(*) >= $2
        ORDER BY merchant
        """,
        window_start,
        _MIN_MERCHANT_TRANSACTIONS,
    )

    merchant_baselines: list[dict[str, Any]] = []
    for row in merchant_rows:
        amounts = [float(a) for a in row["amounts"]]
        median = _safe_median(amounts)
        stddev = _safe_stddev(amounts)
        merchant_baselines.append(
            {
                "merchant": row["merchant"],
                "median": round(median, 2),
                "stddev": round(stddev, 2),
                "sample_count": int(row["cnt"]),
            }
        )

    # --- Per-category weekly velocity baselines ---
    # Bucket transactions into ISO weeks (Monday) and compute weekly sums
    # per category, then average those sums to get weekly_velocity.
    category_rows = await pool.fetch(
        f"""
        SELECT
            category,
            DATE_TRUNC('week', posted_at) AS week_start,
            SUM(amount) AS week_spend
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          {deleted_filter}
          AND category IS NOT NULL
        GROUP BY category, DATE_TRUNC('week', posted_at)
        ORDER BY category, week_start
        """,
        window_start,
    )

    # Aggregate weekly spend per category.
    category_weeks: dict[str, list[float]] = {}
    for row in category_rows:
        cat = row["category"]
        category_weeks.setdefault(cat, []).append(float(row["week_spend"]))

    category_baselines: list[dict[str, Any]] = []
    for category, weekly_spends in sorted(category_weeks.items()):
        if len(weekly_spends) < _MIN_CATEGORY_WEEKS:
            continue
        weekly_velocity = sum(weekly_spends) / len(weekly_spends)
        category_baselines.append(
            {
                "category": category,
                "weekly_velocity": round(weekly_velocity, 2),
                "week_count": len(weekly_spends),
            }
        )

    # --- Persist to memory facts if memory_pool provided ---
    if memory_pool is not None and (merchant_baselines or category_baselines):
        await _persist_baselines_to_memory(memory_pool, merchant_baselines, category_baselines, now)

    status = "ok" if (merchant_baselines or category_baselines) else "insufficient_data"
    return {
        "merchant_baselines": merchant_baselines,
        "category_baselines": category_baselines,
        "status": status,
        "computed_at": now.isoformat(),
    }


async def _persist_baselines_to_memory(
    memory_pool: asyncpg.Pool,
    merchant_baselines: list[dict[str, Any]],
    category_baselines: list[dict[str, Any]],
    now: datetime,
) -> None:
    """Upsert baseline facts into ``public.facts``.

    Uses ``ON CONFLICT (subject, predicate)`` to replace stale baselines.
    Silently skips if the facts table does not exist.
    """
    import json

    try:
        for b in merchant_baselines:
            await memory_pool.execute(
                """
                INSERT INTO public.facts (subject, predicate, content, metadata, updated_at)
                VALUES ($1, 'spending_baseline', $2, $3::jsonb, now())
                ON CONFLICT (subject, predicate)
                DO UPDATE SET
                    content    = EXCLUDED.content,
                    metadata   = EXCLUDED.metadata,
                    updated_at = now()
                """,
                b["merchant"],
                f"median={b['median']}, stddev={b['stddev']}, n={b['sample_count']}",
                json.dumps(
                    {
                        "type": "merchant",
                        "median": b["median"],
                        "stddev": b["stddev"],
                        "sample_count": b["sample_count"],
                        "computed_at": now.isoformat(),
                    }
                ),
            )
        for b in category_baselines:
            subject = f"category:{b['category']}"
            await memory_pool.execute(
                """
                INSERT INTO public.facts (subject, predicate, content, metadata, updated_at)
                VALUES ($1, 'spending_baseline', $2, $3::jsonb, now())
                ON CONFLICT (subject, predicate)
                DO UPDATE SET
                    content    = EXCLUDED.content,
                    metadata   = EXCLUDED.metadata,
                    updated_at = now()
                """,
                subject,
                f"weekly_velocity={b['weekly_velocity']}, weeks={b['week_count']}",
                json.dumps(
                    {
                        "type": "category",
                        "weekly_velocity": b["weekly_velocity"],
                        "week_count": b["week_count"],
                        "computed_at": now.isoformat(),
                    }
                ),
            )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        logger.warning("public.facts table unavailable; baselines not persisted to memory")


# ---------------------------------------------------------------------------
# 3.2  anomaly_scan
# ---------------------------------------------------------------------------


async def anomaly_scan(
    pool: asyncpg.Pool,
    days_back: int = 7,
    sensitivity: str = "medium",
) -> dict[str, Any]:
    """Scan recent transactions for anomalies against computed baselines.

    Computes baselines inline (no memory pool required) then flags:

    1. **amount_anomaly** — debit amount exceeds ``baseline_median + N * stddev``
       where N is determined by ``sensitivity``.
    2. **new_merchant** — merchant has never appeared in the 6-month baseline
       window (first-time merchant).
    3. **category_velocity_anomaly** — category's spend in the scan window
       exceeds the expected weekly velocity by more than the sensitivity factor.

    Returns ``status="insufficient_data"`` when no baseline data is available
    (fewer than ``_MIN_MERCHANT_TRANSACTIONS`` transactions across all merchants
    in the 6-month window).

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    days_back:
        Number of days back to scan for anomalies.  Default: 7.
    sensitivity:
        ``"high"`` (flag more), ``"medium"`` (default), or ``"low"`` (flag less).
        Controls the stddev multiplier used for amount and velocity thresholds.

    Returns
    -------
    dict
        ``{anomalies, total_flagged, status, scanned_transactions, as_of}``

        Each anomaly: ``{transaction_id, merchant, amount, currency, posted_at,
        type, severity, explanation}``
    """
    if sensitivity not in _SENSITIVITY_MULTIPLIERS:
        sensitivity = "medium"
    multiplier = _SENSITIVITY_MULTIPLIERS[sensitivity]

    now = datetime.now(UTC)
    scan_start = now - timedelta(days=days_back)
    window_start = now - timedelta(days=_BASELINE_WINDOW_DAYS)
    deleted_filter = "AND deleted_at IS NULL" if await _has_deleted_at(pool) else ""

    # Compute baselines (inline, no memory persistence here).
    baseline_result = await compute_baselines(pool)
    if baseline_result["status"] == "insufficient_data":
        return {
            "anomalies": [],
            "total_flagged": 0,
            "status": "insufficient_data",
            "scanned_transactions": 0,
            "as_of": now.isoformat(),
        }

    # Build quick-lookup dicts from baseline result.
    merchant_baselines: dict[str, dict[str, float]] = {
        b["merchant"]: {"median": b["median"], "stddev": b["stddev"]}
        for b in baseline_result["merchant_baselines"]
    }
    category_velocity: dict[str, float] = {
        b["category"]: b["weekly_velocity"] for b in baseline_result["category_baselines"]
    }

    # Fetch all merchants ever seen in the baseline window (for new-merchant detection).
    known_merchants_rows = await pool.fetch(
        f"""
        SELECT DISTINCT merchant
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          {deleted_filter}
          AND merchant IS NOT NULL
        """,
        window_start,
    )
    known_merchants: set[str] = {row["merchant"] for row in known_merchants_rows}

    # Fetch recent transactions to scan.
    recent_rows = await pool.fetch(
        f"""
        SELECT id::text, merchant, amount, currency, posted_at, category
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          {deleted_filter}
          AND merchant IS NOT NULL
        ORDER BY posted_at DESC
        """,
        scan_start,
    )

    scanned = len(recent_rows)
    anomalies: list[dict[str, Any]] = []

    # --- Amount anomaly per transaction ---
    for row in recent_rows:
        merchant = row["merchant"]
        amount = float(row["amount"])
        b = merchant_baselines.get(merchant)

        if b is not None and b["stddev"] > 0:
            threshold = b["median"] + multiplier * b["stddev"]
            if amount > threshold:
                zscore = (amount - b["median"]) / b["stddev"]
                severity = _severity_from_zscore(zscore)
                anomalies.append(
                    {
                        "transaction_id": row["id"],
                        "merchant": merchant,
                        "amount": str(Decimal(str(row["amount"])).quantize(Decimal("0.01"))),
                        "currency": row["currency"],
                        "posted_at": (
                            row["posted_at"].isoformat()
                            if hasattr(row["posted_at"], "isoformat")
                            else str(row["posted_at"])
                        ),
                        "type": "amount_anomaly",
                        "severity": severity,
                        "explanation": (
                            f"Amount ${amount:.2f} is {zscore:.1f}x stddev above "
                            f"baseline median of ${b['median']:.2f} "
                            f"(stddev=${b['stddev']:.2f})"
                        ),
                    }
                )

        # --- New merchant ---
        elif merchant not in known_merchants or b is None:
            # known_merchants includes this txn's merchant if it appeared in the
            # window; "new" means it first appeared within the scan window itself.
            # Re-check: if the merchant has no baseline entry (too few historical
            # points) AND it appears for the first time in the scan window.
            has_prior = await pool.fetchval(
                f"""
                SELECT EXISTS (
                    SELECT 1
                    FROM transactions
                    WHERE merchant = $1
                      AND direction = 'debit'
                      AND posted_at >= $2
                      AND posted_at < $3
                      {deleted_filter}
                )
                """,
                merchant,
                window_start,
                scan_start,
            )
            if not has_prior:
                anomalies.append(
                    {
                        "transaction_id": row["id"],
                        "merchant": merchant,
                        "amount": str(Decimal(str(row["amount"])).quantize(Decimal("0.01"))),
                        "currency": row["currency"],
                        "posted_at": (
                            row["posted_at"].isoformat()
                            if hasattr(row["posted_at"], "isoformat")
                            else str(row["posted_at"])
                        ),
                        "type": "new_merchant",
                        "severity": "low",
                        "explanation": (
                            f"First transaction from '{merchant}' in the past "
                            f"{_BASELINE_WINDOW_DAYS} days"
                        ),
                    }
                )

    # --- Category velocity anomaly ---
    # Compute actual per-category spend over the scan window and compare to
    # the baseline weekly velocity scaled to the scan window length.
    if scanned > 0 and category_velocity:
        cat_spend_rows = await pool.fetch(
            f"""
            SELECT category, SUM(amount) AS total_spend
            FROM transactions
            WHERE direction = 'debit'
              AND posted_at >= $1
              {deleted_filter}
              AND category IS NOT NULL
            GROUP BY category
            """,
            scan_start,
        )
        weeks_in_window = max(days_back / 7.0, 1.0)
        for row in cat_spend_rows:
            cat = row["category"]
            actual_spend = float(row["total_spend"])
            weekly_vel = category_velocity.get(cat)
            if weekly_vel is None or weekly_vel <= 0:
                continue
            expected_spend = weekly_vel * weeks_in_window
            if actual_spend > expected_spend * multiplier:
                ratio = actual_spend / expected_spend
                anomalies.append(
                    {
                        "transaction_id": None,
                        "merchant": None,
                        "amount": str(Decimal(str(actual_spend)).quantize(Decimal("0.01"))),
                        "currency": None,
                        "posted_at": None,
                        "type": "category_velocity_anomaly",
                        "severity": "high" if ratio > 3.0 else "medium",
                        "category": cat,
                        "explanation": (
                            f"Category '{cat}' spend ${actual_spend:.2f} is "
                            f"{ratio:.1f}x the expected ${expected_spend:.2f} "
                            f"for a {days_back}-day window "
                            f"(baseline weekly velocity: ${weekly_vel:.2f})"
                        ),
                    }
                )

    status = "ok" if scanned > 0 else "insufficient_data"
    return {
        "anomalies": anomalies,
        "total_flagged": len(anomalies),
        "status": status,
        "scanned_transactions": scanned,
        "as_of": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# 3.3  detect_duplicates
# ---------------------------------------------------------------------------


async def detect_duplicates(
    pool: asyncpg.Pool,
    days_back: int = 30,
) -> dict[str, Any]:
    """Find potential duplicate transactions within a recent window.

    A duplicate candidate pair consists of two debit transactions where:
    - Same merchant (case-insensitive)
    - Same amount
    - Posted within ``_DUPLICATE_DAY_WINDOW`` days of each other

    Transactions whose merchant matches an active subscription service
    (case-insensitive) are excluded from duplicate detection — a recurring
    monthly charge is expected, not a duplicate.

    Confidence is ``"high"`` when both transactions have the same day, and
    ``"medium"`` when they are on adjacent days.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    days_back:
        Number of days back to scan.  Default: 30.

    Returns
    -------
    dict
        ``{duplicates, total_found, status, as_of}``

        Each duplicate group: ``{merchant, amount, currency, transactions,
        confidence}``

        Each transaction in the group: ``{id, posted_at}``
    """
    now = datetime.now(UTC)
    scan_start = now - timedelta(days=days_back)
    deleted_filter = "AND deleted_at IS NULL" if await _has_deleted_at(pool) else ""

    subscription_merchants = await _subscriptions_lookup(pool)

    # Fetch all debit transactions in the window.
    rows = await pool.fetch(
        f"""
        SELECT id::text, merchant, amount, currency, posted_at
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          {deleted_filter}
          AND merchant IS NOT NULL
        ORDER BY merchant ASC, amount ASC, posted_at ASC
        """,
        scan_start,
    )

    if not rows:
        return {
            "duplicates": [],
            "total_found": 0,
            "status": "ok",
            "as_of": now.isoformat(),
        }

    # Group by (lower(merchant), amount).
    groups: dict[tuple[str, Decimal], list[dict[str, Any]]] = {}
    for row in rows:
        merchant_lower = row["merchant"].lower()
        if merchant_lower in subscription_merchants:
            continue  # Skip known subscription merchants
        key = (merchant_lower, Decimal(str(row["amount"])))
        groups.setdefault(key, []).append(
            {
                "id": row["id"],
                "merchant": row["merchant"],
                "amount": str(Decimal(str(row["amount"])).quantize(Decimal("0.01"))),
                "currency": row["currency"],
                "posted_at": (
                    row["posted_at"].isoformat()
                    if hasattr(row["posted_at"], "isoformat")
                    else str(row["posted_at"])
                ),
                "posted_date": (
                    row["posted_at"].date()
                    if hasattr(row["posted_at"], "date")
                    else row["posted_at"]
                ),
            }
        )

    duplicates: list[dict[str, Any]] = []

    for (merchant_lower, amount), txns in groups.items():
        if len(txns) < 2:
            continue

        # Find pairs within the day window.
        seen_pairs: set[frozenset[str]] = set()
        for i in range(len(txns)):
            for j in range(i + 1, len(txns)):
                t1, t2 = txns[i], txns[j]
                pair_key = frozenset([t1["id"], t2["id"]])
                if pair_key in seen_pairs:
                    continue

                date1 = t1["posted_date"]
                date2 = t2["posted_date"]
                day_diff = (
                    abs((date2 - date1).days)
                    if hasattr(date2 - date1, "days")
                    else abs(int((date2 - date1).total_seconds() / 86400))
                )

                if day_diff <= _DUPLICATE_DAY_WINDOW:
                    seen_pairs.add(pair_key)
                    confidence = "high" if day_diff == 0 else "medium"
                    # Check if already represented in a group.
                    # Merge into an existing group if ids overlap.
                    merged = False
                    for existing in duplicates:
                        existing_ids = {t["id"] for t in existing["transactions"]}
                        if t1["id"] in existing_ids or t2["id"] in existing_ids:
                            for t in [t1, t2]:
                                if t["id"] not in existing_ids:
                                    existing["transactions"].append(
                                        {"id": t["id"], "posted_at": t["posted_at"]}
                                    )
                            # Upgrade confidence if a same-day pair was found.
                            if confidence == "high":
                                existing["confidence"] = "high"
                            merged = True
                            break

                    if not merged:
                        duplicates.append(
                            {
                                "merchant": t1["merchant"],
                                "amount": t1["amount"],
                                "currency": t1["currency"],
                                "transactions": [
                                    {"id": t1["id"], "posted_at": t1["posted_at"]},
                                    {"id": t2["id"], "posted_at": t2["posted_at"]},
                                ],
                                "confidence": confidence,
                            }
                        )

    return {
        "duplicates": duplicates,
        "total_found": len(duplicates),
        "status": "ok",
        "as_of": now.isoformat(),
    }
