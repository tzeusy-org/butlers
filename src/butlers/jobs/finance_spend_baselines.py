"""Finance category spend: week-bucket personal baselines (bu-q7vx1q.79).

"Unusual spending" in a category is judged against the owner's own history, not a mean times
a factor. Each (category, currency) pair is one ``week`` metric from
:mod:`butlers.core.baselines`: the median and MAD band of up to 26 complete owner-local weeks of
debit spend, with an honest ``n_observed`` / ``n_expected`` and no band until ``min_n`` weeks of
spend exist. Currencies are never mixed or converted; the same category in two currencies is two
independent metrics.

The producer (:func:`refresh_category_baselines`) is run by the daily anomaly insight scan and
stores the bands in the finance schema's ``metric_baselines``. The consumers (the ``anomaly_scan``
tool and the daily briefing) call :func:`score_week_to_date`, which scores the spend of the week
so far against the stored band: spend only grows through the week, so "above the usual range"
already can be said on any day, while "within" is only provisional and is never reported. A week
with no spend is not a sample (a ledger has no instrument to be silent), so a category that is
bought only now and then stays ``insufficient_history`` instead of being called anomalous.

No LLM and no cross-schema read.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

import asyncpg

from butlers.core.baselines import (
    METHOD_VERSION,
    Baseline,
    BaselineMetric,
    Deviation,
    compute_baselines,
    score,
    upsert_baseline,
    window_bounds,
)

logger = logging.getLogger(__name__)

KEY_PREFIX = "spend.category:"
HISTORY_WEEKS = 26
_WINDOW_DAYS = HISTORY_WEEKS * 7
_MIN_WEEKS = 4
# min_n (4 weeks) is the binding floor; 4 of 26 weeks is about 15 percent.
_MIN_COVERAGE = 0.15
# A flat category (identical weekly rent) has a MAD of zero. The band then keeps a floor of one
# currency unit or a tenth of the usual week, whichever is larger, so a cent of drift is not
# "unusual".
_MIN_DISPERSION_FLOOR = 1.0
_MIN_DISPERSION_SHARE = 0.1
_RETIRE_BATCH = 500


def spend_key(category: str, currency: str) -> str:
    return f"{KEY_PREFIX}{category}:{currency}"


def spend_metric(
    category: str, currency: str, *, min_dispersion: float = _MIN_DISPERSION_FLOOR
) -> BaselineMetric:
    """The declaration shared by every (category, currency) spend band."""
    key = spend_key(category, currency)
    return BaselineMetric(
        key=key,
        unit=currency,
        bucket="week",
        window_days=_WINDOW_DAYS,
        min_n=_MIN_WEEKS,
        min_coverage=_MIN_COVERAGE,
        k_mad=2.6,
        k_consecutive=1,
        concern="above",
        min_dispersion=min_dispersion,
        expected_signal_key=f"finance:baseline:{key}",
        refresh_interval=timedelta(days=7),
    )


def week_start(moment: datetime, zone: tzinfo) -> date:
    """The Monday of the owner-local week containing ``moment``."""
    local = moment.astimezone(zone).date()
    return local - timedelta(days=local.weekday())


def _local_week_start_dt(start: date, zone: tzinfo) -> datetime:
    return datetime.combine(start, datetime.min.time(), tzinfo=zone)


async def _deleted_filter(pool: asyncpg.Pool) -> str:
    has_deleted_at = await pool.fetchval(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'transactions'
              AND column_name = 'deleted_at'
        )
        """
    )
    return "AND deleted_at IS NULL" if has_deleted_at else ""


async def fetch_weekly_spend(
    pool: asyncpg.Pool, *, as_of: date, zone: tzinfo
) -> dict[tuple[str, str], dict[date, float]]:
    """Debit spend per complete owner-local week before ``as_of``, by (category, currency).

    Weeks are keyed by their Monday. Only the weeks the band can use are read, and the week
    starting ``as_of`` (still in progress) is not history.
    """
    first, last = window_bounds(spend_metric("x", "XXX"), as_of)
    lower = _local_week_start_dt(first, zone)
    upper = _local_week_start_dt(as_of, zone)
    rows = await pool.fetch(
        f"""
        SELECT category, currency, posted_at, amount
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          AND posted_at < $2
          {await _deleted_filter(pool)}
          AND category IS NOT NULL
          AND currency IS NOT NULL
        """,
        lower,
        upper,
    )
    series: dict[tuple[str, str], dict[date, float]] = defaultdict(dict)
    for row in rows:
        week = week_start(row["posted_at"], zone)
        bucket = series[(row["category"], row["currency"])]
        bucket[week] = bucket.get(week, 0.0) + float(row["amount"])
    return dict(series)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


async def refresh_category_baselines(
    pool: asyncpg.Pool, *, now: datetime, zone: tzinfo
) -> list[Baseline]:
    """Compute and store every category band; returns the bands whose stored row changed.

    A rerun with unchanged inputs is a no-op (the digest matches), so this is safe to call from
    a daily job.
    """
    as_of = week_start(now, zone)
    changed: list[Baseline] = []
    for (category, currency), samples in sorted(
        (await fetch_weekly_spend(pool, as_of=as_of, zone=zone)).items()
    ):
        usual = _median(list(samples.values()))
        metric = spend_metric(
            category,
            currency,
            min_dispersion=max(_MIN_DISPERSION_FLOOR, _MIN_DISPERSION_SHARE * usual),
        )
        (baseline,) = compute_baselines(metric, samples, as_of=as_of, now=now)
        if await upsert_baseline(pool, baseline):
            changed.append(baseline)
    return changed


async def load_baselines(pool: asyncpg.Pool, keys: list[str]) -> dict[str, Baseline]:
    """Stored bands for ``keys`` (current method version); none when the table is absent."""
    if not keys:
        return {}
    try:
        rows = await pool.fetch(
            """
            SELECT metric_key, bucket_value, method_version, status, measurability, unit,
                   center, dispersion, k_mad, n_observed, n_expected, coverage,
                   input_digest, computed_at
            FROM metric_baselines
            WHERE metric_key = ANY($1::text[])
              AND bucket_value = 'all'
              AND method_version = $2
            """,
            keys,
            METHOD_VERSION,
        )
    except asyncpg.UndefinedTableError:
        logger.warning("metric_baselines unavailable; category spend is not judged")
        return {}
    return {
        row["metric_key"]: Baseline(
            metric_key=row["metric_key"],
            bucket_value=row["bucket_value"],
            method_version=row["method_version"],
            status=row["status"],
            measurability=row["measurability"],
            unit=row["unit"],
            center=row["center"],
            dispersion=row["dispersion"],
            n_observed=row["n_observed"],
            n_expected=row["n_expected"],
            coverage=row["coverage"],
            k_mad=row["k_mad"],
            input_digest=row["input_digest"],
            computed_at=row["computed_at"],
        )
        for row in rows
    }


@dataclass(frozen=True, slots=True)
class SpendFinding:
    """One (category, currency) week-to-date spend scored against its stored band."""

    category: str
    currency: str
    week_spend: float
    metric: BaselineMetric
    baseline: Baseline
    deviation: Deviation

    @property
    def is_anomaly(self) -> bool:
        return self.deviation.concerning

    @property
    def usual_range(self) -> tuple[float, float] | None:
        return self.baseline.band


async def score_week_to_date(
    pool: asyncpg.Pool, *, now: datetime, zone: tzinfo, active_since: datetime
) -> list[SpendFinding]:
    """Score this week's spend for every category that had a debit since ``active_since``.

    A category with no stored band is left out (nothing to judge against); one with a thin or
    stale band is returned with that honest state and never counts as an anomaly.
    """
    start = _local_week_start_dt(week_start(now, zone), zone)
    rows = await pool.fetch(
        f"""
        SELECT category, currency, SUM(amount) AS week_spend, MAX(posted_at) AS last_posted
        FROM transactions
        WHERE direction = 'debit'
          AND posted_at >= $1
          AND posted_at <= $2
          {await _deleted_filter(pool)}
          AND category IS NOT NULL
          AND currency IS NOT NULL
        GROUP BY category, currency
        ORDER BY category, currency
        """,
        start,
        now,
    )
    active = [row for row in rows if row["last_posted"] >= active_since]
    bands = await load_baselines(pool, [spend_key(r["category"], r["currency"]) for r in active])
    findings: list[SpendFinding] = []
    for row in active:
        metric = spend_metric(row["category"], row["currency"])
        baseline = bands.get(metric.key)
        if baseline is None:
            continue
        spend = float(row["week_spend"])
        findings.append(
            SpendFinding(
                row["category"],
                row["currency"],
                spend,
                metric,
                baseline,
                score(metric, spend, baseline, now=now),
            )
        )
    return findings


def money(currency: str, value: float) -> str:
    return f"{currency} {value:,.2f}" if abs(value) < 100 else f"{currency} {value:,.0f}"


def usual_range_words(finding: SpendFinding) -> str:
    """'usual CCY 120 to 180, drawn from 10 of 26 weeks' (the honest denominator in words)."""
    band = finding.usual_range
    assert band is not None
    low, high = band
    return (
        f"usual {money(finding.currency, max(low, 0.0))} to {money(finding.currency, high)}, "
        f"drawn from {finding.baseline.n_observed} of {finding.baseline.n_expected} weeks"
    )


def finding_evidence(finding: SpendFinding) -> Mapping[str, Any]:
    return finding.baseline.evidence()


async def retire_spending_baseline_facts(pool: asyncpg.Pool) -> int:
    """Retract the ``spending_baseline`` facts the finance butler wrote; returns how many.

    Only system-authored facts of the finance butler are touched: an owner-authored fact of the
    same predicate is left alone. The retraction is the ordinary ``memory_forget`` one
    (``validity = 'retracted'``, row kept), so it is reversible, and idempotent: retracted rows
    no longer match.
    """
    from butlers.modules.memory.storage import forget_memory

    try:
        rows = await pool.fetch(
            """
            SELECT id FROM facts
            WHERE predicate = 'spending_baseline'
              AND validity IN ('active', 'fading')
              AND content_authority = 'system'
              AND source_butler = 'finance'
            ORDER BY created_at
            LIMIT $1
            """,
            _RETIRE_BATCH,
        )
    except Exception:
        logger.warning("Could not look up spending_baseline facts", exc_info=True)
        return 0
    retired = 0
    for row in rows:
        try:
            if await forget_memory(pool, "fact", row["id"]):
                retired += 1
        except Exception:
            logger.warning("Could not retract a spending_baseline fact", exc_info=True)
    if retired:
        logger.info("Retracted %d spending_baseline fact(s) the finance butler wrote", retired)
    return retired
