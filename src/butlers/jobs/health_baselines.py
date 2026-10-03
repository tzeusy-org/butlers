"""Health ``baseline_watch``: personal baselines for resting HR, HRV and sleep.

A deterministic daily job (no LLM, no cross-schema read). For each metric it
reads the owner's own per-day values from the health butler's ``facts``,
computes the robust band with its honest denominator
(:mod:`butlers.core.baselines`), judges instrument measurability through the
shared expected-signal tri-state, and opens or closes a deviation episode.

An opened episode is reconciled into the owner-condition ledger and then
proposed as one insight candidate carrying ``baseline_evidence`` and an
``owner_condition`` premise, so the candidate is withdrawn unsent (or amended in
place) the moment the metric returns to its band. The wording reports the
deviation from the owner's own range and the days it rests on; it never
interprets or advises (``roster/health/MANIFESTO.md``: no judgment, only
honesty). No health values reach a log line.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg

from butlers.core.baselines import (
    Baseline,
    BaselineMetric,
    BaselineState,
    Deviation,
    EpisodeAction,
    OpenEpisode,
    apply_episode_decision,
    bucket_value,
    compute_baselines,
    episode_decision,
    episode_excluded_days,
    episode_spans,
    get_open_episode,
    mark_insight_proposed,
    score,
    upsert_baseline,
    window_bounds,
)
from butlers.core.condition_ledger import Observation, compute_fingerprint
from butlers.core.expected_signals import (
    ExpectedSignalState,
    measurement_producer_identity,
    upsert_expected_signal,
)
from butlers.core.general_settings import resolve_general_timezone
from butlers.core.insight_premise import owner_condition_premise
from butlers.core.owner_conditions import reconcile_snapshot

logger = logging.getLogger(__name__)

CONDITION_SOURCE = "health:baseline-deviation"
INSIGHT_CATEGORY = "baseline-deviation"
_INSIGHT_PRIORITY = 50
_INSIGHT_EXPIRES_DAYS = 3
_PRODUCER_SAMPLE_ROWS = 10
# A daily series refreshes at most once a day; two days of silence is the point
# at which an absent reading stops being ordinary.
_EXPECTED_CADENCE = timedelta(days=2)
_MS_PER_HOUR = 3_600_000.0


def _metric(
    key: str,
    unit: str,
    concern: str,
    min_dispersion: float,
) -> BaselineMetric:
    return BaselineMetric(
        key=key,
        unit=unit,
        bucket="day",
        window_days=60,
        min_n=14,
        min_coverage=0.5,
        k_mad=2.6,
        k_consecutive=3,
        concern=concern,  # type: ignore[arg-type]
        min_dispersion=min_dispersion,
        expected_signal_key=f"health:baseline:{key}",
    )


@dataclass(frozen=True, slots=True)
class _Source:
    metric: BaselineMetric
    predicate: str
    label: str
    decimals: int
    # The measurement type a chart door can open; None where no chart exists.
    door_type: str | None


_SOURCES: tuple[_Source, ...] = (
    _Source(
        _metric("resting_hr", "bpm", "above", 1.0),
        "measurement_resting_hr",
        "Resting heart rate",
        0,
        "resting_hr",
    ),
    _Source(
        _metric("hrv", "ms", "below", 3.0), "measurement_hrv", "Heart rate variability", 0, "hrv"
    ),
    _Source(
        _metric("sleep_duration", "h", "both", 0.25),
        "sleep_session",
        "Sleep duration",
        1,
        None,
    ),
)

METRICS: tuple[BaselineMetric, ...] = tuple(source.metric for source in _SOURCES)


@dataclass(frozen=True, slots=True)
class _Series:
    samples: dict[date, float]
    producer_rows: list[tuple[str | None, str | None]]
    last_observed_at: datetime | None


def _parse_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _load_series(
    pool: asyncpg.Pool, source: _Source, *, first: date, last: date, tz: ZoneInfo
) -> _Series:
    """Per owner-local day values for ``source`` between ``first`` and ``last``.

    Resting HR and HRV are daily summaries: the latest reading of the day wins.
    Sleep is the total of the sessions that ended on the day (the wake-up date).
    """
    lower = datetime.combine(first - timedelta(days=1), datetime.min.time(), tzinfo=tz)
    upper = datetime.combine(last + timedelta(days=2), datetime.min.time(), tzinfo=tz)
    rows = await pool.fetch(
        """
        SELECT valid_at,
               metadata->>'value' AS value,
               metadata->>'duration_ms' AS duration_ms,
               COALESCE(metadata->>'source', metadata->>'provider') AS source,
               metadata->>'source_endpoint_identity' AS source_endpoint_identity
        FROM facts
        WHERE predicate = $1
          AND scope = 'health'
          AND validity = 'active'
          AND valid_at >= $2
          AND valid_at < $3
        ORDER BY valid_at
        """,
        source.predicate,
        lower,
        upper,
    )
    samples: dict[date, float] = {}
    for row in rows:
        valid_at = row["valid_at"]
        if source.metric.key == "sleep_duration":
            duration_ms = _parse_float(row["duration_ms"])
            if duration_ms is None or duration_ms <= 0:
                continue
            day = (valid_at + timedelta(milliseconds=duration_ms)).astimezone(tz).date()
            samples[day] = samples.get(day, 0.0) + duration_ms / _MS_PER_HOUR
        else:
            value = _parse_float(row["value"])
            if value is None:
                continue
            samples[valid_at.astimezone(tz).date()] = value
    producer_rows = [(row["source"], row["source_endpoint_identity"]) for row in rows]
    last_observed = rows[-1]["valid_at"] if rows else None
    return _Series(
        samples={day: v for day, v in samples.items() if first <= day <= last},
        producer_rows=producer_rows[-_PRODUCER_SAMPLE_ROWS:],
        last_observed_at=last_observed,
    )


async def _measurable(
    pool: asyncpg.Pool, metric: BaselineMetric, series: _Series, *, now: datetime
) -> bool:
    """Whether the instrument behind ``metric`` is honestly able to report."""
    if not series.producer_rows:
        # Nothing was ever reported: thin history is the honest word, not a dead instrument.
        return True
    producer, endpoint = measurement_producer_identity(series.producer_rows)
    evaluation = await upsert_expected_signal(
        pool,
        signal_key=metric.expected_signal_key,
        producer=producer,
        producer_endpoint_identity=endpoint,
        expected_cadence=_EXPECTED_CADENCE,
        last_observed_at=series.last_observed_at,
        now=now,
    )
    if evaluation.state is ExpectedSignalState.UNMEASURABLE:
        logger.warning(
            "Baseline unmeasurable (metric=%s producer=%s reason=%s)",
            metric.key,
            evaluation.producer,
            evaluation.unmeasurable_reason,
        )
        return False
    return True


def format_deviation_message(
    source: _Source, baseline: Baseline, *, direction: str, days: int
) -> str:
    """Neutral report of a deviation from the owner's own range; no advice."""
    band = baseline.band
    assert band is not None
    low, high = band
    fmt = f"{{:.{source.decimals}f}}"
    unit = {"h": "hours"}.get(source.metric.unit, source.metric.unit)
    return (
        f"{source.label} has been {direction} your usual range of "
        f"{fmt.format(low)} to {fmt.format(high)} {unit} for {days} "
        f"{'day' if days == 1 else 'days'}. "
        f"Your usual range is drawn from {baseline.n_observed} of {baseline.n_expected} days."
    )


def _event_window(opened_on: date, as_of: date, tz: ZoneInfo) -> dict[str, str]:
    start = datetime.combine(opened_on, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(as_of + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _fingerprint(metric_key: str, opened_on: date) -> str:
    return compute_fingerprint(
        CONDITION_SOURCE, 1, {"metric": metric_key, "opened_on": opened_on.isoformat()}
    )


async def _reconcile_conditions(pool: asyncpg.Pool, *, labels: Mapping[str, str]) -> bool:
    """Mirror every open episode into the owner-condition ledger (complete snapshot)."""
    rows = await pool.fetch(
        "SELECT metric_key, opened_on, direction FROM metric_deviation_episodes "
        "WHERE status = 'open'"
    )
    observations = [
        Observation(
            fingerprint=_fingerprint(row["metric_key"], row["opened_on"]),
            summary=(
                f"{labels.get(row['metric_key'], row['metric_key'])} outside the owner's "
                f"usual range since {row['opened_on'].isoformat()}"
            ),
            metadata={
                "metric_key": row["metric_key"],
                "opened_on": row["opened_on"].isoformat(),
                "direction": row["direction"],
            },
        )
        for row in rows
    ]
    try:
        await reconcile_snapshot(
            pool,
            source=CONDITION_SOURCE,
            observations=observations,
            snapshot_complete=True,
            initial_grace_seconds=3600,
        )
    except Exception:
        logger.warning("Baseline owner-condition reconcile failed", exc_info=True)
        return False
    return True


async def _propose_insight(
    pool: asyncpg.Pool,
    source: _Source,
    episode: OpenEpisode,
    baseline: Baseline,
    *,
    as_of: date,
    now: datetime,
    tz: ZoneInfo,
) -> str:
    """Hand one candidate to the broker; returns ``accepted``, ``filtered`` or ``error``."""
    from butlers.tools.switchboard.insight.broker import propose_insight_candidate

    days = (as_of - episode.opened_on).days + 1
    metadata: dict[str, Any] = {
        "baseline_evidence": baseline.evidence(),
        "event_window": _event_window(episode.opened_on, as_of, tz),
    }
    if source.door_type is not None:
        metadata["measurement_door"] = {
            "type": source.door_type,
            "since": episode.opened_on.isoformat(),
            "until": as_of.isoformat(),
        }
    result = await propose_insight_candidate(
        pool,
        origin_butler="health",
        priority=_INSIGHT_PRIORITY,
        category=INSIGHT_CATEGORY,
        dedup_key=f"health:baseline:{source.metric.key}:{episode.id}",
        message=format_deviation_message(source, baseline, direction=episode.direction, days=days),
        expires_at=now + timedelta(days=_INSIGHT_EXPIRES_DAYS),
        metadata=metadata,
        premise=owner_condition_premise(
            CONDITION_SOURCE, _fingerprint(source.metric.key, episode.opened_on)
        ),
        now=now,
    )
    status = result.get("status", "error")
    if status == "error":
        logger.warning(
            "Baseline insight rejected (metric=%s reason=%s)",
            source.metric.key,
            result.get("reason", "unknown"),
        )
    return status


def _score_run(
    metric: BaselineMetric,
    samples: Mapping[date, float],
    baselines: Sequence[Baseline],
    *,
    as_of: date,
    now: datetime,
) -> dict[date, Deviation | None]:
    by_bucket = {baseline.bucket_value: baseline for baseline in baselines}
    scored: dict[date, Deviation | None] = {}
    for offset in range(metric.k_consecutive):
        day = as_of - timedelta(days=offset)
        baseline = by_bucket.get(bucket_value(metric, day))
        value = samples.get(day)
        scored[day] = (
            None if value is None or baseline is None else score(metric, value, baseline, now=now)
        )
    return scored


async def run_baseline_watch(
    pool: asyncpg.Pool,
    job_args: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recompute the health baselines and advance their deviation episodes."""
    del job_args
    now = now or datetime.now(UTC)
    tz = ZoneInfo(await resolve_general_timezone(pool))
    as_of = now.astimezone(tz).date() - timedelta(days=1)

    stats: dict[str, Any] = {
        "metrics": len(_SOURCES),
        "baselines_written": 0,
        "episodes_opened": 0,
        "episodes_closed": 0,
        "insights_accepted": 0,
        "insights_filtered": 0,
        "insights_errored": 0,
        "states": {},
    }
    labels = {source.metric.key: source.label for source in _SOURCES}
    pending: list[tuple[_Source, Baseline]] = []

    for source in _SOURCES:
        metric = source.metric
        first, _ = window_bounds(metric, as_of)
        series = await _load_series(pool, source, first=first, last=as_of, tz=tz)
        spans = await episode_spans(pool, metric.key, since=first)
        open_episode = await get_open_episode(pool, metric.key)
        excluded = episode_excluded_days(spans, as_of=as_of)
        measurable = await _measurable(pool, metric, series, now=now)

        baselines = compute_baselines(
            metric,
            series.samples,
            as_of=as_of,
            now=now,
            excluded_days=excluded,
            measurable=measurable,
        )
        for baseline in baselines:
            if await upsert_baseline(pool, baseline):
                stats["baselines_written"] += 1

        scored = _score_run(metric, series.samples, baselines, as_of=as_of, now=now)
        latest = scored.get(as_of)
        stats["states"][metric.key] = (
            latest.state.value
            if latest is not None
            else ("no_reading" if measurable else BaselineState.UNMEASURABLE.value)
        )
        decision = episode_decision(metric, scored, as_of=as_of, open_episode=open_episode)
        transition = await apply_episode_decision(pool, metric.key, decision, as_of=as_of)
        if transition.action is EpisodeAction.OPEN:
            stats["episodes_opened"] += 1
        elif transition.action is EpisodeAction.CLOSE:
            stats["episodes_closed"] += 1

        episode = transition.episode
        if (
            episode is not None
            and transition.action in (EpisodeAction.OPEN, EpisodeAction.CONTINUE)
            and not episode.insight_proposed
        ):
            baseline = next(b for b in baselines if b.bucket_value == bucket_value(metric, as_of))
            pending.append((source, baseline))

    reconciled = await _reconcile_conditions(pool, labels=labels)
    if not reconciled:
        # Without the ledger row the premise would be unbound; try again next run.
        return stats

    for source, baseline in pending:
        episode = await get_open_episode(pool, source.metric.key)
        if episode is None or episode.insight_proposed:
            continue
        status = await _propose_insight(
            pool, source, episode, baseline, as_of=as_of, now=now, tz=tz
        )
        if status == "accepted":
            stats["insights_accepted"] += 1
        elif status == "filtered":
            stats["insights_filtered"] += 1
        else:
            stats["insights_errored"] += 1
            continue
        await mark_insight_proposed(pool, episode.id)

    logger.info(
        "Health baseline_watch complete (opened=%d closed=%d accepted=%d)",
        stats["episodes_opened"],
        stats["episodes_closed"],
        stats["insights_accepted"],
    )
    return stats
