"""Personal baselines: one robust "usual for you" scorer (bu-q7vx1q.15).

A baseline is the owner's own recent band for one metric: a median and a
MAD-derived dispersion over a trailing window of per-day values, carried with
the honest denominator it was computed from (``n_observed`` of ``n_expected``
days) and the instrument's measurability. A claim of the form "unusual for you"
is only ever made from a baseline that is ready, fresh and measurable; every
other case is a named state, never a quiet "within".

Everything up to :func:`episode_decision` is pure and deterministic -- no
clock, no database, no LLM. The persistence helpers at the bottom are the only
code that touches Postgres, always against the caller's own schema (the tables
are unqualified per-butler core tables, see ``core_258``).

Contamination rules:

- The window ends ``k_consecutive`` days before the last scored day, so the run
  being judged never contributes to the band it is judged against.
- Days inside a deviation episode (open or closed) are excluded from both
  ``n_observed`` and ``n_expected``, so the band does not chase an anomaly.
- A dispersion of zero (a perfectly flat series) is floored at the metric's
  declared ``min_dispersion``.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

import asyncpg

METHOD_VERSION = "mad-v1"

# Consistency constant that makes the MAD comparable to a standard deviation
# for normally distributed data.
_MAD_SCALE = 1.4826

#: Insight categories that assert "unusual for you". The Switchboard broker
#: rejects a candidate in one of these categories unless it carries
#: ``metadata.baseline_evidence`` (see :func:`validate_baseline_evidence`).
DEVIATION_CATEGORIES = frozenset({"baseline-deviation"})

_EVIDENCE_NUMERIC_KEYS = ("n_observed", "n_expected", "coverage", "center", "dispersion")

Bucket = Literal["day", "dow"]
Concern = Literal["above", "below", "both"]


class BaselineState(StrEnum):
    """What a value scored against a baseline can honestly be called."""

    WITHIN = "within"
    ABOVE = "above"
    BELOW = "below"
    INSUFFICIENT_HISTORY = "insufficient_history"
    UNMEASURABLE = "unmeasurable"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class BaselineMetric:
    """Declaration of one baselined metric. All thresholds are explicit."""

    key: str
    unit: str
    bucket: Bucket
    window_days: int
    min_n: int
    min_coverage: float
    k_mad: float
    k_consecutive: int
    concern: Concern
    min_dispersion: float
    expected_signal_key: str
    refresh_interval: timedelta = timedelta(days=1)

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("metric key must be non-empty")
        if self.window_days < 1 or self.k_consecutive < 1:
            raise ValueError("window_days and k_consecutive must be positive")
        if self.min_n < 1 or not 0 < self.min_coverage <= 1:
            raise ValueError("min_n must be positive and min_coverage in (0, 1]")
        if self.k_mad <= 0 or self.min_dispersion <= 0:
            raise ValueError("k_mad and min_dispersion must be positive")


@dataclass(frozen=True, slots=True)
class Baseline:
    """One computed band, or the honest reason there is none."""

    metric_key: str
    bucket_value: str
    method_version: str
    status: Literal["ready", "insufficient_history", "unmeasurable"]
    measurability: Literal["measurable", "unmeasurable"]
    unit: str
    center: float | None
    dispersion: float | None
    n_observed: int
    n_expected: int
    coverage: float
    k_mad: float
    input_digest: str
    computed_at: datetime

    @property
    def band(self) -> tuple[float, float] | None:
        """The usual range: the values that would not be called a deviation."""
        if self.status != "ready" or self.center is None or self.dispersion is None:
            return None
        spread = self.k_mad * self.dispersion
        return self.center - spread, self.center + spread

    def evidence(self) -> dict[str, Any]:
        """The ``baseline_evidence`` an insight claiming a deviation must carry."""
        return {
            "n_observed": self.n_observed,
            "n_expected": self.n_expected,
            "coverage": round(self.coverage, 4),
            "center": self.center,
            "dispersion": self.dispersion,
            "method_version": self.method_version,
        }


@dataclass(frozen=True, slots=True)
class Deviation:
    """One value scored against a baseline."""

    state: BaselineState
    mad_units: float | None
    n_observed: int
    n_expected: int
    coverage: float
    concerning: bool

    @property
    def deviates(self) -> bool:
        return self.state in (BaselineState.ABOVE, BaselineState.BELOW)


def bucket_value(metric: BaselineMetric, day: date) -> str:
    """Bucket a day falls in: ``all`` for a daily band, the weekday for ``dow``.

    ``day`` is the owner-local calendar date; the caller resolves the owner's
    timezone, so a reading just after local midnight lands on the right weekday.
    """
    return str(day.weekday()) if metric.bucket == "dow" else "all"


def window_bounds(metric: BaselineMetric, as_of: date) -> tuple[date, date]:
    """Inclusive ``(first, last)`` window days for scoring the run ending ``as_of``."""
    last = as_of - timedelta(days=metric.k_consecutive)
    first = last - timedelta(days=metric.window_days - 1)
    return first, last


def compute_baselines(
    metric: BaselineMetric,
    samples: Mapping[date, float],
    *,
    as_of: date,
    now: datetime,
    excluded_days: Collection[date] = (),
    measurable: bool = True,
) -> list[Baseline]:
    """Compute one baseline per bucket from per-day ``samples``.

    ``measurable`` is the instrument verdict (an unmeasurable producer yields
    ``status='unmeasurable'`` bands with no center, whatever the history says).
    """
    first, last = window_bounds(metric, as_of)
    excluded = set(excluded_days)
    expected: dict[str, int] = {}
    observed: dict[str, list[tuple[date, float]]] = {}
    day = first
    while day <= last:
        if day not in excluded:
            bucket = bucket_value(metric, day)
            expected[bucket] = expected.get(bucket, 0) + 1
            observed.setdefault(bucket, [])
            if day in samples:
                observed[bucket].append((day, float(samples[day])))
        day += timedelta(days=1)

    out: list[Baseline] = []
    for bucket in sorted(expected):
        points = observed[bucket]
        n_observed, n_expected = len(points), expected[bucket]
        coverage = n_observed / n_expected if n_expected else 0.0
        center = dispersion = None
        if not measurable:
            status = "unmeasurable"
        elif n_observed < metric.min_n or coverage < metric.min_coverage:
            status = "insufficient_history"
        else:
            status = "ready"
            values = [value for _, value in points]
            center = statistics.median(values)
            mad = statistics.median(abs(value - center) for value in values)
            dispersion = max(_MAD_SCALE * mad, metric.min_dispersion)
        out.append(
            Baseline(
                metric_key=metric.key,
                bucket_value=bucket,
                method_version=METHOD_VERSION,
                status=status,
                measurability="measurable" if measurable else "unmeasurable",
                unit=metric.unit,
                center=center,
                dispersion=dispersion,
                n_observed=n_observed,
                n_expected=n_expected,
                coverage=coverage,
                k_mad=metric.k_mad,
                input_digest=_digest(metric, bucket, as_of, points, measurable),
                computed_at=now,
            )
        )
    return out


def _digest(
    metric: BaselineMetric,
    bucket: str,
    as_of: date,
    points: Sequence[tuple[date, float]],
    measurable: bool,
) -> str:
    payload = json.dumps(
        [
            METHOD_VERSION,
            metric.key,
            bucket,
            as_of.isoformat(),
            measurable,
            [[day.isoformat(), value] for day, value in points],
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def score(metric: BaselineMetric, value: float, baseline: Baseline, *, now: datetime) -> Deviation:
    """Score ``value`` against ``baseline``.

    Precedence: an unmeasurable instrument, then a stale band, then thin
    history, then the value itself. Nothing but a ready, fresh, measurable band
    can answer ``within`` / ``above`` / ``below``.
    """

    def _unscored(state: BaselineState) -> Deviation:
        return Deviation(
            state, None, baseline.n_observed, baseline.n_expected, baseline.coverage, False
        )

    if baseline.measurability != "measurable":
        return _unscored(BaselineState.UNMEASURABLE)
    if now - baseline.computed_at > 2 * metric.refresh_interval:
        return _unscored(BaselineState.STALE)
    if baseline.status != "ready" or baseline.center is None or baseline.dispersion is None:
        return _unscored(BaselineState.INSUFFICIENT_HISTORY)

    mad_units = (value - baseline.center) / baseline.dispersion
    if mad_units >= metric.k_mad:
        state = BaselineState.ABOVE
    elif mad_units <= -metric.k_mad:
        state = BaselineState.BELOW
    else:
        state = BaselineState.WITHIN
    concerning = state is not BaselineState.WITHIN and metric.concern in ("both", state.value)
    return Deviation(
        state, mad_units, baseline.n_observed, baseline.n_expected, baseline.coverage, concerning
    )


# ---------------------------------------------------------------------------
# Episodes (pure decision)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenEpisode:
    id: str
    opened_on: date
    direction: Literal["above", "below"]
    peak_mad: float
    insight_proposed: bool = False


class EpisodeAction(StrEnum):
    NONE = "none"
    OPEN = "open"
    CONTINUE = "continue"
    CLOSE = "close"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class EpisodeDecision:
    action: EpisodeAction
    opened_on: date | None = None
    direction: Literal["above", "below"] | None = None
    peak_mad: float | None = None


def episode_decision(
    metric: BaselineMetric,
    scored: Mapping[date, Deviation | None],
    *,
    as_of: date,
    open_episode: OpenEpisode | None,
) -> EpisodeDecision:
    """Decide what the latest ``k_consecutive`` scored days do to the episode.

    ``scored`` maps a day to its :class:`Deviation`, or ``None`` when the day
    has no reading. An episode opens only when every one of the last
    ``k_consecutive`` days has a concerning deviation in the same direction. An
    open episode closes only on positive evidence of a return (the latest day is
    scored and not a concerning deviation); a day that cannot be scored
    (missing, unmeasurable, stale, thin history) holds it open and never closes
    it by silence.
    """
    latest = scored.get(as_of)
    if open_episode is not None:
        if latest is None or latest.mad_units is None:
            return EpisodeDecision(EpisodeAction.HOLD)
        if latest.concerning and latest.state.value == open_episode.direction:
            return EpisodeDecision(
                EpisodeAction.CONTINUE,
                opened_on=open_episode.opened_on,
                direction=open_episode.direction,
                peak_mad=max(open_episode.peak_mad, abs(latest.mad_units)),
            )
        return EpisodeDecision(EpisodeAction.CLOSE, opened_on=open_episode.opened_on)

    days = [as_of - timedelta(days=offset) for offset in range(metric.k_consecutive - 1, -1, -1)]
    run = [scored.get(day) for day in days]
    if any(item is None or not item.concerning for item in run):
        return EpisodeDecision(EpisodeAction.NONE)
    states = {item.state for item in run if item is not None}
    if len(states) != 1:
        return EpisodeDecision(EpisodeAction.NONE)
    direction = next(iter(states)).value
    peak = max(abs(item.mad_units) for item in run if item is not None and item.mad_units)
    return EpisodeDecision(
        EpisodeAction.OPEN,
        opened_on=days[0],
        direction=direction,  # type: ignore[arg-type]
        peak_mad=peak,
    )


def episode_excluded_days(
    episodes: Sequence[tuple[date, date | None]], *, as_of: date
) -> set[date]:
    """Days to keep out of a window: ``[opened_on, closed_on)`` per episode.

    ``closed_on`` is the first day back in the band, so it is a normal day; an
    open episode (``None``) excludes everything through ``as_of``.
    """
    excluded: set[date] = set()
    for opened_on, closed_on in episodes:
        end = as_of if closed_on is None else closed_on - timedelta(days=1)
        day = opened_on
        while day <= end:
            excluded.add(day)
            day += timedelta(days=1)
    return excluded


def validate_baseline_evidence(evidence: Any) -> str | None:
    """Return why ``evidence`` is not a usable ``baseline_evidence``, else ``None``."""
    if not isinstance(evidence, Mapping):
        return "baseline_evidence must be an object"
    for key in _EVIDENCE_NUMERIC_KEYS:
        value = evidence.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            return f"baseline_evidence.{key} must be a number"
    if not isinstance(evidence.get("method_version"), str) or not evidence["method_version"]:
        return "baseline_evidence.method_version must be a non-empty string"
    if evidence["n_expected"] < 1 or evidence["n_observed"] > evidence["n_expected"]:
        return "baseline_evidence n_observed/n_expected are inconsistent"
    return None


# ---------------------------------------------------------------------------
# Persistence (caller's own schema; unqualified tables from core_258)
# ---------------------------------------------------------------------------


async def upsert_baseline(pool: asyncpg.Pool, baseline: Baseline) -> bool:
    """Persist ``baseline``; an unchanged ``input_digest`` is a no-op (returns False)."""
    row = await pool.fetchrow(
        """
        INSERT INTO metric_baselines (
            metric_key, bucket_value, method_version, status, measurability, unit,
            center, dispersion, k_mad, n_observed, n_expected, coverage,
            input_digest, computed_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
        ON CONFLICT (metric_key, bucket_value, method_version) DO UPDATE
        SET status = EXCLUDED.status,
            measurability = EXCLUDED.measurability,
            unit = EXCLUDED.unit,
            center = EXCLUDED.center,
            dispersion = EXCLUDED.dispersion,
            k_mad = EXCLUDED.k_mad,
            n_observed = EXCLUDED.n_observed,
            n_expected = EXCLUDED.n_expected,
            coverage = EXCLUDED.coverage,
            input_digest = EXCLUDED.input_digest,
            computed_at = EXCLUDED.computed_at
        WHERE metric_baselines.input_digest IS DISTINCT FROM EXCLUDED.input_digest
        RETURNING 1
        """,
        baseline.metric_key,
        baseline.bucket_value,
        baseline.method_version,
        baseline.status,
        baseline.measurability,
        baseline.unit,
        baseline.center,
        baseline.dispersion,
        baseline.k_mad,
        baseline.n_observed,
        baseline.n_expected,
        baseline.coverage,
        baseline.input_digest,
        baseline.computed_at,
    )
    return row is not None


async def episode_spans(
    pool: asyncpg.Pool, metric_key: str, *, since: date
) -> list[tuple[date, date | None]]:
    """``(opened_on, closed_on)`` of every episode that can touch a window from ``since``."""
    rows = await pool.fetch(
        """
        SELECT opened_on, closed_on FROM metric_deviation_episodes
        WHERE metric_key = $1 AND (closed_on IS NULL OR closed_on >= $2)
        """,
        metric_key,
        since,
    )
    return [(row["opened_on"], row["closed_on"]) for row in rows]


async def get_open_episode(pool: asyncpg.Pool, metric_key: str) -> OpenEpisode | None:
    row = await pool.fetchrow(
        """
        SELECT id::text AS id, opened_on, direction, peak_mad,
               insight_proposed_at IS NOT NULL AS insight_proposed
        FROM metric_deviation_episodes
        WHERE metric_key = $1 AND status = 'open'
        """,
        metric_key,
    )
    if row is None:
        return None
    return OpenEpisode(
        row["id"],
        row["opened_on"],
        row["direction"],
        float(row["peak_mad"]),
        row["insight_proposed"],
    )


async def mark_insight_proposed(pool: asyncpg.Pool, episode_id: str) -> None:
    """Record that the episode's insight candidate was handed to the broker."""
    await pool.execute(
        "UPDATE metric_deviation_episodes SET insight_proposed_at = now() "
        "WHERE id = $1::uuid AND insight_proposed_at IS NULL",
        episode_id,
    )


@dataclass(frozen=True, slots=True)
class EpisodeTransition:
    action: EpisodeAction
    episode: OpenEpisode | None = None


async def apply_episode_decision(
    pool: asyncpg.Pool,
    metric_key: str,
    decision: EpisodeDecision,
    *,
    as_of: date,
) -> EpisodeTransition:
    """Apply ``decision`` under a per-metric advisory lock.

    The decision was computed from a read taken before the lock, so the open
    episode is re-read inside it: a catch-up run racing the scheduled run finds
    the episode already open (or closed) and changes nothing, and
    ``UNIQUE(metric_key, opened_on)`` backstops the insert.
    """
    if decision.action in (EpisodeAction.NONE, EpisodeAction.HOLD):
        return EpisodeTransition(decision.action)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"baseline:{metric_key}"
        )
        current = await conn.fetchrow(
            """
            SELECT id::text AS id, opened_on, direction, peak_mad,
                   insight_proposed_at IS NOT NULL AS insight_proposed
            FROM metric_deviation_episodes
            WHERE metric_key = $1 AND status = 'open'
            """,
            metric_key,
        )
        if decision.action is EpisodeAction.OPEN:
            if current is not None:
                return EpisodeTransition(EpisodeAction.NONE)
            row = await conn.fetchrow(
                """
                INSERT INTO metric_deviation_episodes
                    (metric_key, opened_on, direction, peak_mad, status)
                VALUES ($1, $2, $3, $4, 'open')
                ON CONFLICT (metric_key, opened_on) DO NOTHING
                RETURNING id::text AS id
                """,
                metric_key,
                decision.opened_on,
                decision.direction,
                decision.peak_mad,
            )
            if row is None:
                return EpisodeTransition(EpisodeAction.NONE)
            assert decision.opened_on is not None and decision.direction is not None
            return EpisodeTransition(
                EpisodeAction.OPEN,
                OpenEpisode(
                    row["id"], decision.opened_on, decision.direction, float(decision.peak_mad or 0)
                ),
            )
        if current is None:
            return EpisodeTransition(EpisodeAction.NONE)
        episode = OpenEpisode(
            current["id"],
            current["opened_on"],
            current["direction"],
            float(current["peak_mad"]),
            current["insight_proposed"],
        )
        if decision.action is EpisodeAction.CONTINUE:
            await conn.execute(
                "UPDATE metric_deviation_episodes SET peak_mad = $2, updated_at = now() "
                "WHERE id = $1::uuid",
                episode.id,
                decision.peak_mad,
            )
            return EpisodeTransition(EpisodeAction.CONTINUE, episode)
        await conn.execute(
            "UPDATE metric_deviation_episodes "
            "SET status = 'closed', closed_on = $2, updated_at = now() WHERE id = $1::uuid",
            episode.id,
            as_of,
        )
        return EpisodeTransition(EpisodeAction.CLOSE, episode)
