"""Finance butler anomaly detection — statistical baselines and anomaly scanning.

Provides:
- ``compute_baselines``: per-merchant (median, stddev) and per-category
  (weekly velocity) figures from a 6-month rolling window.  Nothing is stored.
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

from butlers.jobs.finance_spend_baselines import (
    finding_evidence,
    money,
    score_week_to_date,
    usual_range_words,
)
from butlers.tools.finance.budgets import resolve_budget_zone

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
    """Return True when ``finance.transactions`` has a ``deleted_at`` column.

    Filters by ``current_schema()`` to avoid false positives when another
    schema in the same PostgreSQL database also has a ``transactions`` table.
    """
    return bool(
        await pool.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'transactions'
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
    *,
    now: datetime | None = None,
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

    Nothing is persisted: the ``spending_baseline`` memory facts this used to
    write are gone (bu-q7vx1q.79).  Category spend is judged by the week-bucket
    personal baselines in ``metric_baselines`` (see
    :mod:`butlers.jobs.finance_spend_baselines`); the per-category velocity here
    is kept only as a descriptive figure.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the finance schema.
    memory_pool:
        Deprecated and ignored; kept so existing callers keep working.
    now:
        Optional reference timestamp used as the upper bound of the rolling
        window.  Defaults to ``datetime.now(UTC)``; tests inject a fixed
        value to keep date-bucketed baselines deterministic.

    Returns
    -------
    dict
        ``{merchant_baselines, category_baselines, status, computed_at}``

        ``merchant_baselines``: list of ``{merchant, median, stddev, sample_count}``
        ``category_baselines``: list of ``{category, weekly_velocity, week_count}``
        ``status``: ``"ok"`` | ``"insufficient_data"``
        ``computed_at``: ISO timestamp
    """
    if now is None:
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

    status = "ok" if (merchant_baselines or category_baselines) else "insufficient_data"
    return {
        "merchant_baselines": merchant_baselines,
        "category_baselines": category_baselines,
        "status": status,
        "computed_at": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# 3.2  anomaly_scan
# ---------------------------------------------------------------------------


async def anomaly_scan(
    pool: asyncpg.Pool,
    days_back: int = 7,
    sensitivity: str = "medium",
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scan recent transactions for anomalies against computed baselines.

    Computes baselines inline (no memory pool required) then flags:

    1. **amount_anomaly** — debit amount exceeds ``baseline_median + N * stddev``
       where N is determined by ``sensitivity``.
    2. **new_merchant** — merchant has never appeared in the 6-month baseline
       window (first-time merchant).
    3. **category_velocity_anomaly** — category's spend so far this week is
       above its stored personal baseline band (per currency).

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
    now:
        Optional reference timestamp used as the upper bound of the scan
        window.  Defaults to ``datetime.now(UTC)``; tests inject a fixed
        value to keep date-bucketed scans deterministic.

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

    if now is None:
        now = datetime.now(UTC)
    scan_start = now - timedelta(days=days_back)
    window_start = now - timedelta(days=_BASELINE_WINDOW_DAYS)
    deleted_filter = "AND deleted_at IS NULL" if await _has_deleted_at(pool) else ""

    # Compute baselines (inline, no memory persistence here).
    baseline_result = await compute_baselines(pool, now=now)
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

    # Pre-fetch first-seen date for every merchant in the baseline window to
    # avoid an N+1 query inside the transaction loop (new-merchant detection).
    first_seen_rows = await pool.fetch(
        f"""
        SELECT merchant, MIN(posted_at) AS first_seen
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          {deleted_filter}
          AND merchant IS NOT NULL
        GROUP BY merchant
        """,
        window_start,
    )
    first_seen_by_merchant: dict[str, Any] = {
        r["merchant"]: r["first_seen"] for r in first_seen_rows
    }

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
                        "posted_at": row["posted_at"].isoformat(),
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
        elif b is None:
            # Merchant has too few historical transactions for a baseline.
            # Flag it as new if its first appearance falls within the scan window.
            first_seen = first_seen_by_merchant.get(merchant)
            if first_seen is not None and first_seen >= scan_start:
                anomalies.append(
                    {
                        "transaction_id": row["id"],
                        "merchant": merchant,
                        "amount": str(Decimal(str(row["amount"])).quantize(Decimal("0.01"))),
                        "currency": row["currency"],
                        "posted_at": row["posted_at"].isoformat(),
                        "type": "new_merchant",
                        "severity": "low",
                        "explanation": (
                            f"First transaction from '{merchant}' in the past "
                            f"{_BASELINE_WINDOW_DAYS} days"
                        ),
                    }
                )

    # --- Category velocity anomaly ---
    # This week's spend (owner-local, per currency) against the stored week-bucket personal
    # baseline (bu-q7vx1q.79). Only an ``above`` state is an anomaly: thin, stale or missing
    # history is said nowhere here, and the sensitivity setting does not move the band.
    if scanned > 0:
        zone = await resolve_budget_zone(pool)
        for finding in await score_week_to_date(pool, now=now, zone=zone, active_since=scan_start):
            if not finding.is_anomaly:
                continue
            band = finding.usual_range
            assert band is not None
            mad_units = finding.deviation.mad_units or 0.0
            anomalies.append(
                {
                    "transaction_id": None,
                    "merchant": None,
                    "amount": str(Decimal(str(finding.week_spend)).quantize(Decimal("0.01"))),
                    "currency": finding.currency,
                    "posted_at": None,
                    "type": "category_velocity_anomaly",
                    "severity": "high" if mad_units >= 2 * finding.metric.k_mad else "medium",
                    "category": finding.category,
                    "mad_units": round(mad_units, 2),
                    "band": {"low": band[0], "high": band[1]},
                    "baseline_evidence": dict(finding_evidence(finding)),
                    "explanation": (
                        f"Category '{finding.category}' spend "
                        f"{money(finding.currency, finding.week_spend)} so far this week is above "
                        f"your {usual_range_words(finding)}"
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
                "posted_at": row["posted_at"].isoformat(),
                "posted_date": row["posted_at"].date(),
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
                day_diff = abs((date2 - date1).days)

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
