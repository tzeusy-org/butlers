"""Home weekly energy digest: week-bucket personal baselines (bu-q7vx1q.78).

The digest judges "unusual" against the owner's own history instead of last week's prose fact.
Each metric (the house total, and one per device) is a ``week`` baseline from
:mod:`butlers.core.baselines`: the median and MAD band of the preceding complete weeks, with an
honest ``n_observed`` / ``n_expected`` and no band until ``min_n`` weeks exist. History comes
from Home Assistant's hourly long-term statistics, summed over trailing 7-day windows that end
where the scored week starts, so nothing is stored per week and no migration is needed.

A week counts only when its hourly buckets are nearly complete: a gap in Home Assistant's
recorder understates the total and would pull the band down, so the week is left out and the
denominator keeps it. No LLM and no cross-schema read.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg

from butlers.connectors.home_assistant_statistics import HAStatisticsClient, HAStatisticsError
from butlers.core.baselines import (
    Baseline,
    BaselineMetric,
    BaselineState,
    Deviation,
    apply_episode_decision,
    compute_baselines,
    episode_decision,
    episode_excluded_days,
    episode_spans,
    get_open_episode,
    score,
    upsert_baseline,
    window_bounds,
)

logger = logging.getLogger(__name__)

TOTAL_KEY = "energy.total"
DEVICE_KEY_PREFIX = "energy.device:"

HISTORY_WEEKS = 8
_WINDOW_DAYS = HISTORY_WEEKS * 7
_HOURS_PER_WEEK = 7 * 24
# A week whose hourly buckets fall below this share is a recorder gap, not a quiet week.
_MIN_WEEK_BUCKET_SHARE = 0.9

# Provenance of the prose facts this job used to write: scheduled jobs carry no routing
# context, so their writes stamp the ``system`` authority. Owner-authored facts never match.
_PROSE_FACT_TAGS = ["energy", "baseline", "weekly"]
_RETIRE_BATCH = 500


def device_key(entity_id: str) -> str:
    return f"{DEVICE_KEY_PREFIX}{entity_id}"


def energy_metric(key: str) -> BaselineMetric:
    """The declaration shared by the house total and every device."""
    return BaselineMetric(
        key=key,
        unit="kWh",
        bucket="week",
        window_days=_WINDOW_DAYS,
        min_n=4,
        min_coverage=0.5,
        k_mad=2.6,
        k_consecutive=1,
        concern="above",
        min_dispersion=0.5,
        expected_signal_key=f"home:baseline:{key}",
        refresh_interval=timedelta(days=7),
    )


def scored_week_anchor(end: datetime) -> date:
    """The key (start date) of the trailing week ending at ``end``."""
    return (end - timedelta(days=7)).date()


def _bucket_start(bucket: Mapping[str, Any]) -> datetime | None:
    raw = bucket.get("start")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        seconds = raw / 1000 if raw > 1e11 else raw
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def weekly_totals(series: object, *, end: datetime) -> dict[date, float]:
    """kWh per complete trailing week before the scored one, keyed by week-start date.

    ``series`` is one entity's hourly statistics. Week ``i`` (1..HISTORY_WEEKS) is the window
    ``[end - 7(i+1) days, end - 7i days)``; the scored week (``i = 0``) is not history.
    """
    if not isinstance(series, list):
        return {}
    sums: dict[int, float] = {}
    counts: dict[int, int] = {}
    for bucket in series:
        if not isinstance(bucket, dict):
            continue
        start = _bucket_start(bucket)
        change = bucket.get("change")
        if (
            start is None
            or isinstance(change, bool)
            or not isinstance(change, int | float)
            or not math.isfinite(change)
        ):
            continue
        if start >= end:
            continue
        # Window i is [end - 7(i+1) days, end - 7i days), so a bucket at the boundary is week i-1.
        weeks_back = math.ceil((end - start) / timedelta(days=7)) - 1
        if not 1 <= weeks_back <= HISTORY_WEEKS:
            continue
        sums[weeks_back] = sums.get(weeks_back, 0.0) + float(change)
        counts[weeks_back] = counts.get(weeks_back, 0) + 1
    needed = math.ceil(_MIN_WEEK_BUCKET_SHARE * _HOURS_PER_WEEK)
    return {
        scored_week_anchor(end) - timedelta(days=7 * weeks_back): total
        for weeks_back, total in sums.items()
        if counts[weeks_back] >= needed
    }


async def fetch_weekly_history(
    entity_ids: list[str], *, ha_url: str, ha_token: str, end: datetime
) -> dict[str, dict[date, float]]:
    """Weekly kWh history per entity from HA hourly long-term statistics.

    Returns an empty history when Home Assistant cannot answer: every band then reports
    ``insufficient_history`` rather than a guess. Not verified against a live Home Assistant.
    """
    client = HAStatisticsClient(ha_url=ha_url, ha_token=ha_token, verify_ssl=False)
    try:
        data = await client.get_statistics(
            statistic_ids=entity_ids,
            start=(end - timedelta(days=7 * (HISTORY_WEEKS + 1))).isoformat(),
            end=end.isoformat(),
            period="hour",
            types=("change",),
        )
    except HAStatisticsError as exc:
        logger.warning("Home energy history unavailable: code=%s", exc.code)
        return {}
    return {entity_id: weekly_totals(data.get(entity_id), end=end) for entity_id in entity_ids}


@dataclass(frozen=True, slots=True)
class EnergyFinding:
    """One metric's scored week."""

    key: str
    entity_id: str | None
    name: str
    weekly_kwh: float
    deviation: Deviation
    baseline: Baseline

    @property
    def state(self) -> BaselineState:
        return self.deviation.state

    @property
    def is_anomaly(self) -> bool:
        return self.deviation.concerning

    @property
    def usual_range(self) -> tuple[float, float] | None:
        return self.baseline.band


def _total_history(
    history: Mapping[str, Mapping[date, float]], entity_ids: list[str]
) -> dict[date, float]:
    """House-total history: only weeks every currently-reporting device has a complete value."""
    if not entity_ids:
        return {}
    weeks = set.intersection(*(set(history.get(entity_id, {})) for entity_id in entity_ids))
    return {week: sum(history[entity_id][week] for entity_id in entity_ids) for week in weeks}


async def _score_metric(
    pool: asyncpg.Pool,
    key: str,
    kwh: float,
    samples: Mapping[date, float],
    *,
    as_of: date,
    now: datetime,
) -> tuple[Baseline, Deviation, bool]:
    metric = energy_metric(key)
    first, _ = window_bounds(metric, as_of)
    spans = await episode_spans(pool, key, since=first)
    baselines = compute_baselines(
        metric,
        samples,
        as_of=as_of,
        now=now,
        excluded_days=episode_excluded_days(spans, as_of=as_of),
    )
    (baseline,) = baselines
    written = await upsert_baseline(pool, baseline)
    deviation = score(metric, kwh, baseline, now=now)
    decision = episode_decision(
        metric,
        {as_of: deviation},
        as_of=as_of,
        open_episode=await get_open_episode(pool, key),
    )
    await apply_episode_decision(pool, key, decision, as_of=as_of)
    return baseline, deviation, written


async def score_energy_week(
    pool: asyncpg.Pool,
    *,
    devices: list[dict[str, Any]],
    total_kwh: float,
    history: Mapping[str, Mapping[date, float]],
    as_of: date,
    now: datetime,
) -> tuple[list[EnergyFinding], bool]:
    """Score the house total and every device for the week starting ``as_of``.

    Returns the findings and whether any stored band changed. A device seen for the first time
    has no history, so its band starts ``insufficient_history`` and fills over ``min_n`` weeks.
    """
    findings: list[EnergyFinding] = []
    changed = False
    entity_ids = [device["entity_id"] for device in devices]
    plan: list[tuple[str, str | None, str, float, Mapping[date, float]]] = [
        (TOTAL_KEY, None, "House total", total_kwh, _total_history(history, entity_ids))
    ]
    for device in devices:
        entity_id = device["entity_id"]
        plan.append(
            (
                device_key(entity_id),
                entity_id,
                device["friendly_name"],
                float(device["weekly_kwh"]),
                history.get(entity_id, {}),
            )
        )
    for key, entity_id, name, kwh, samples in plan:
        baseline, deviation, written = await _score_metric(
            pool, key, kwh, samples, as_of=as_of, now=now
        )
        changed = changed or written
        findings.append(EnergyFinding(key, entity_id, name, kwh, deviation, baseline))
    return findings, changed


def anomalies_from(findings: list[EnergyFinding]) -> list[dict[str, Any]]:
    """Device anomalies for the digest: above the owner's own usual range, with the range."""
    out: list[dict[str, Any]] = []
    for finding in findings:
        band = finding.usual_range
        center = finding.baseline.center
        if finding.entity_id is None or not finding.is_anomaly or band is None or not center:
            continue
        pct_above = (finding.weekly_kwh - center) / center * 100
        out.append(
            {
                "entity_id": finding.entity_id,
                "friendly_name": finding.name,
                "weekly_kwh": finding.weekly_kwh,
                "baseline_kwh": center,
                "pct_above": round(pct_above, 1),
                "severity": "high" if finding.weekly_kwh >= 2 * center else "anomaly",
                "usual_low": band[0],
                "usual_high": band[1],
                "n_observed": finding.baseline.n_observed,
                "n_expected": finding.baseline.n_expected,
            }
        )
    return out


def unjudged_names(findings: list[EnergyFinding]) -> list[str]:
    """Metrics (the house total included) whose band is not ready: 'not enough weeks yet'."""
    return [
        finding.name for finding in findings if finding.state is BaselineState.INSUFFICIENT_HISTORY
    ]


def total_is_within(findings: list[EnergyFinding]) -> bool:
    """True only when the house total was scored ``within`` a ready band.

    The digest may call the week normal on this alone: a total with no ready band, or one above
    its band, has no honest "normal" to report even when no single device is anomalous.
    """
    return any(
        finding.key == TOTAL_KEY
        and finding.baseline.status == "ready"
        and finding.state is BaselineState.WITHIN
        for finding in findings
    )


def usual_total_kwh(findings: list[EnergyFinding]) -> float | None:
    for finding in findings:
        if finding.key == TOTAL_KEY and finding.baseline.status == "ready":
            return finding.baseline.center
    return None


async def retire_prose_energy_baselines(pool: asyncpg.Pool) -> int:
    """Retract the ``energy_baseline`` facts this job used to write; returns how many.

    Only facts the job itself wrote are touched: ``system`` authority, ``home`` source and the
    job's tags. An owner-authored ``energy_baseline`` fact is left alone. The retraction is the
    ordinary ``memory_forget`` one (``validity = 'retracted'``, row kept), so it is reversible,
    and it is idempotent: retracted rows no longer match.
    """
    from butlers.modules.memory.storage import forget_memory

    try:
        rows = await pool.fetch(
            """
            SELECT id FROM facts
            WHERE predicate = 'energy_baseline'
              AND validity IN ('active', 'fading')
              AND content_authority = 'system'
              AND source_butler = 'home'
              AND tags @> $1::jsonb
            ORDER BY created_at
            LIMIT $2
            """,
            json.dumps(_PROSE_FACT_TAGS),
            _RETIRE_BATCH,
        )
    except Exception:
        logger.warning("Could not look up prose energy_baseline facts", exc_info=True)
        return 0
    retired = 0
    for row in rows:
        try:
            if await forget_memory(pool, "fact", row["id"]):
                retired += 1
        except Exception:
            logger.warning("Could not retract a prose energy_baseline fact", exc_info=True)
    if retired:
        logger.info("Retracted %d prose energy_baseline fact(s) the digest wrote", retired)
    return retired
