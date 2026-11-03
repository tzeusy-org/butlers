"""Chronicler day-close memory write-back loop (tasks.md §8, bu-93y4rt).

Doctrine-amended, narrow write-back. After the once-daily ``chronicler_day_close``
prompt produces its retrospective summary, this deterministic completion-hook
step distills *derived* insights from the chronicler's OWN already-materialized
data (``daily_rollups``, ``daily_rollup_flags``, ``episode_entities``) and:

1. writes synthesized insight facts into the chronicler's OWN memory schema
   (``store_fact``, ``source_butler="chronicler"``) with provenance, an
   evidence-derived confidence (see :func:`evidence_confidence`, written to the
   facts ``confidence`` column and mirrored in metadata), and decay (via
   ``permanence``);
2. writes self-reminder facts marking low-confidence day blocks for
   re-reconciliation once more evidence lands.

Invariants (the §8.6 acceptance contract):

- Every fact write targets the chronicler's OWN schema only. This module is
  handed a ``store_fact_fn`` already bound to the chronicler pool; it never
  receives, and cannot construct, a foreign-schema pool.
- It never touches ``relationship.entity_facts`` or any other butler's schema.
- The write-back adds NO owner-facing message. This module has no ``notify``
  collaborator; the once-daily day-close summary (sent by the prompt) remains
  the single sanctioned owner-facing message.

Everything here is deterministic: pure synthesis functions over plain data plus
an orchestrator that dispatches to injected async collaborators. No LLM, no
randomness, no ``datetime.now`` inside the synthesis core.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.chronicler.aggregations import LANES, sources_for_lane
from butlers.chronicler.balance import (
    DEFAULT_BASELINE_LOOKBACK_DAYS,
    LaneBalance,
    compute_daily_balance,
    is_lane_anomalous,
)

logger = logging.getLogger(__name__)

SOURCE_BUTLER = "chronicler"

# Sleep debt: cumulative deficit (baseline − actual) over the trailing window,
# counting only days that fell short. Fire an insight once the running debt
# clears this many seconds across at least this many short days — a single
# short night is noise; a sustained pattern is the signal worth remembering.
SLEEP_DEBT_THRESHOLD_SECONDS = 3 * 3600
SLEEP_DEBT_MIN_SHORTFALL_DAYS = 2

# Insight confidence (see evidence_confidence). A derived insight is never
# certain: a full baseline window earns the ceiling, a thin one sits near the
# floor. A self-reminder exists *because* a block is uncertain, so it carries
# the floor.
INSIGHT_CONFIDENCE_FLOOR = 0.4
INSIGHT_CONFIDENCE_CEILING = 0.9

# Predicate strings for chronicler-owned insight facts.
PREDICATE_LANE_SKEW = "lane-skew"
PREDICATE_SLEEP_DEBT = "sleep-debt"
PREDICATE_SELF_REMINDER = "revisit-low-confidence-block"

# daily_rollup_flag types that mean "this day may be revised once more evidence
# lands" — the exact signal a self-reminder is for. Other flag types
# (routine_break, lane_share_outlier) are stable verdicts, not pending-backfill.
_REVISIT_FLAG_TYPES = frozenset({"feeder_dark", "sleep_missing"})


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InsightFact:
    """A derived fact the chronicler writes into its OWN memory schema.

    ``confidence`` is stored on the fact row (and mirrored in metadata);
    ``permanence`` sets the decay tier so a low-confidence, fast-fading insight
    can be distinguished from a durable one.
    """

    subject: str
    predicate: str
    content: str
    permanence: str
    importance: float
    confidence: float
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriteBackResult:
    """Outcome counts for one day-close write-back run."""

    insights_written: int = 0
    self_reminders_written: int = 0
    errors: int = 0


StoreFactFn = Callable[..., Awaitable[Any]]


# ---------------------------------------------------------------------------
# Pure synthesis
# ---------------------------------------------------------------------------


def _mean(values: Sequence[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f}h"


def evidence_confidence(sample_days: int, window_days: int) -> float:
    """Confidence of a derived insight from how much of its window had data.

    Linear in baseline coverage (``sample_days / window_days``, clamped to
    ``[0, 1]``) between :data:`INSIGHT_CONFIDENCE_FLOOR` and
    :data:`INSIGHT_CONFIDENCE_CEILING`, rounded to two decimals. Days whose
    sources did not report never count as samples, so a sparse history yields
    a visibly weaker insight instead of a confident one.
    """
    if window_days <= 0:
        return INSIGHT_CONFIDENCE_FLOOR
    coverage = min(1.0, max(0, sample_days) / window_days)
    span = INSIGHT_CONFIDENCE_CEILING - INSIGHT_CONFIDENCE_FLOOR
    return round(INSIGHT_CONFIDENCE_FLOOR + span * coverage, 2)


def synthesize_lane_skew_insights(
    day: date,
    balances: Iterable[LaneBalance],
    *,
    window_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
) -> list[InsightFact]:
    """One insight per lane whose day-vs-usual delta clears the anomaly bar.

    Reuses :func:`butlers.chronicler.balance.is_lane_anomalous` so the skew
    signal never diverges from the balance/trends API surface. Confidence
    follows :func:`evidence_confidence` over the lane's baseline sample days.
    """
    out: list[InsightFact] = []
    for b in sorted(balances, key=lambda x: x.lane):
        if not is_lane_anomalous(b):
            continue
        delta = b.delta_seconds or 0.0
        direction = "up" if delta > 0 else "down"
        phrase = "more" if delta > 0 else "less"
        content = (
            f"{b.lane.capitalize()} skewed {phrase} than usual on "
            f"{day.isoformat()}: {_fmt_hours(b.seconds)} vs a usual "
            f"{_fmt_hours(b.baseline_seconds or 0.0)}."
        )
        out.append(
            InsightFact(
                subject=f"{b.lane} lane",
                predicate=PREDICATE_LANE_SKEW,
                content=content,
                # A single day's skew is a fast-fading observation.
                permanence="volatile",
                importance=5.0,
                confidence=evidence_confidence(b.baseline_sample_days, window_days),
                tags=("chronicler-insight", PREDICATE_LANE_SKEW, b.lane),
                metadata={
                    "local_date": day.isoformat(),
                    "lane": b.lane,
                    "direction": direction,
                    "seconds": b.seconds,
                    "baseline_seconds": b.baseline_seconds,
                    "delta_seconds": b.delta_seconds,
                    "baseline_sample_days": b.baseline_sample_days,
                },
            )
        )
    return out


def synthesize_sleep_debt_insight(
    day: date,
    *,
    sleep_daily_seconds: Sequence[int],
    sleep_baseline_seconds: float | None,
) -> InsightFact | None:
    """A cumulative sleep-debt insight, or ``None`` when the pattern is noise.

    ``sleep_daily_seconds`` is the trailing window of the sleep lane's per-day
    seconds (baseline days + the closed day, last). Debt accrues only on days
    that fell short of ``sleep_baseline_seconds``; both a total-debt floor and
    a minimum number of short days must be cleared before an insight is
    emitted. Debt is only "building" when the closed day itself fell short: a
    closed day at or above the usual adds nothing, and claiming growing debt
    beside a "slept more than usual" skew would contradict it.

    Callers pass only complete windows, so confidence is the full-coverage
    :func:`evidence_confidence`.
    """
    if sleep_baseline_seconds is None or sleep_baseline_seconds <= 0:
        return None
    if not sleep_daily_seconds or sleep_daily_seconds[-1] >= sleep_baseline_seconds:
        return None
    debt = 0.0
    shortfall_days = 0
    for seconds in sleep_daily_seconds:
        deficit = sleep_baseline_seconds - seconds
        if deficit > 0:
            debt += deficit
            shortfall_days += 1
    if debt < SLEEP_DEBT_THRESHOLD_SECONDS or shortfall_days < SLEEP_DEBT_MIN_SHORTFALL_DAYS:
        return None
    content = (
        f"Sleep debt building: about {_fmt_hours(debt)} below your usual across "
        f"{shortfall_days} of the last {len(sleep_daily_seconds)} days."
    )
    return InsightFact(
        subject="sleep",
        predicate=PREDICATE_SLEEP_DEBT,
        content=content,
        permanence="volatile",
        importance=6.0,
        confidence=evidence_confidence(len(sleep_daily_seconds), len(sleep_daily_seconds)),
        tags=("chronicler-insight", PREDICATE_SLEEP_DEBT),
        metadata={
            "as_of": day.isoformat(),
            "debt_seconds": round(debt),
            "shortfall_days": shortfall_days,
            "window_days": len(sleep_daily_seconds),
            "baseline_seconds": sleep_baseline_seconds,
        },
    )


def synthesize_self_reminders(day: date, flags: Iterable[Any]) -> list[InsightFact]:
    """Self-reminder facts for low-confidence day blocks pending backfill.

    ``flags`` are :class:`~butlers.chronicler.models.DailyRollupFlag` rows for
    the closed day. Only the pending-backfill flag types (see
    ``_REVISIT_FLAG_TYPES``) produce a reminder — a stable verdict is not a
    "revisit me" marker.
    """
    out: list[InsightFact] = []
    for f in sorted(flags, key=lambda x: str(getattr(x, "flag_type", ""))):
        flag_type = getattr(f, "flag_type", None)
        if flag_type not in _REVISIT_FLAG_TYPES:
            continue
        content = (
            f"Day {day.isoformat()} closed with '{flag_type}' — a low-confidence "
            "block to revisit once more evidence lands."
        )
        out.append(
            InsightFact(
                subject=f"{day.isoformat()} {flag_type}",
                predicate=PREDICATE_SELF_REMINDER,
                content=content,
                # A reminder should fade if it is never reconciled.
                permanence="ephemeral",
                importance=4.0,
                confidence=INSIGHT_CONFIDENCE_FLOOR,
                tags=("chronicler-self-reminder", str(flag_type)),
                metadata={
                    "local_date": day.isoformat(),
                    "flag_type": flag_type,
                    "severity": getattr(f, "severity", None),
                    "detail": getattr(f, "detail", None) or {},
                },
            )
        )
    return out


# ---------------------------------------------------------------------------
# Orchestrator (injected collaborators)
# ---------------------------------------------------------------------------


async def execute_writeback(
    *,
    insights: Sequence[InsightFact],
    self_reminders: Sequence[InsightFact],
    store_fact_fn: StoreFactFn,
) -> WriteBackResult:
    """Dispatch synthesized write-backs to their injected collaborators.

    Best-effort per item: a single failure is logged and counted, never raised,
    so one bad fact cannot strand the rest of the day-close write-back. This
    function never notifies the owner — it has no channel to.
    """
    result = WriteBackResult()

    async def _store(fact: InsightFact) -> bool:
        try:
            await store_fact_fn(
                subject=fact.subject,
                predicate=fact.predicate,
                content=fact.content,
                permanence=fact.permanence,
                importance=fact.importance,
                confidence=fact.confidence,
                tags=list(fact.tags),
                metadata={
                    **fact.metadata,
                    "confidence": fact.confidence,
                    "source": SOURCE_BUTLER,
                },
            )
            return True
        except Exception:
            logger.exception(
                "chronicler writeback: failed to store fact %s/%s",
                fact.subject,
                fact.predicate,
            )
            result.errors += 1
            return False

    for fact in insights:
        if await _store(fact):
            result.insights_written += 1
    for fact in self_reminders:
        if await _store(fact):
            result.self_reminders_written += 1

    return result


# ---------------------------------------------------------------------------
# Own-schema data fetch
# ---------------------------------------------------------------------------


def _coerce_zone(tz: str | ZoneInfo | None) -> ZoneInfo:
    if isinstance(tz, ZoneInfo):
        return tz
    candidate = (tz or "").strip()
    if not candidate or candidate.upper() == "UTC":
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("chronicler writeback: unknown timezone %r; using UTC", tz)
        return ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Top-level day-close write-back
# ---------------------------------------------------------------------------


async def run_day_close_writeback(
    pool: Any,
    *,
    day_date: date,
    timezone: str,
    store_fact_fn: StoreFactFn,
    baseline_lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
) -> WriteBackResult:
    """Synthesize and persist the day-close write-back for ``day_date``.

    Reads the chronicler's OWN ``daily_rollups`` / ``daily_rollup_flags`` for
    the closed day plus a trailing baseline window, synthesizes insights +
    self-reminders, and dispatches them via the injected collaborator. Returns outcome counts;
    never raises for data/collaborator failures (best-effort, like the tier2
    cache writer it runs beside).
    """
    from butlers.chronicler import storage

    baseline_start = day_date - timedelta(days=baseline_lookback_days)
    baseline_end = day_date - timedelta(days=1)

    try:
        day_rollups = await storage.list_daily_rollups(pool, local_date=day_date)
        baseline_rollups = await storage.list_daily_rollups_range(
            pool, start_date=baseline_start, end_date=baseline_end
        )
        flags = await storage.list_daily_rollup_flags(pool, local_date=day_date)
        baseline_flags = await storage.list_daily_rollup_flags_range(
            pool, start_date=baseline_start, end_date=baseline_end
        )
    except Exception:
        logger.exception(
            "chronicler writeback: rollup fetch failed for %s; skipping write-back", day_date
        )
        return WriteBackResult()

    # Rollups deliberately zero-fill every lane for the balance API. That is
    # not evidence of a measured zero for durable behavioral claims. Preserve
    # numeric zero when it has a real sample, rather than testing truthiness.
    dark_by_date: dict[date, set[str]] = {day_date: set()}
    for flag_date, flag in [
        *((day_date, flag) for flag in flags),
        *((flag.local_date, flag) for flag in baseline_flags),
    ]:
        if flag.flag_type == "feeder_dark":
            dark_by_date.setdefault(flag_date, set()).update(flag.detail.get("dark_sources", []))

    def supported(row: Any) -> bool:
        return (
            row.timezone == timezone
            and row.episode_count > 0
            and not (sources_for_lane(row.lane) & dark_by_date.get(row.local_date, set()))
        )

    day_seconds_by_lane = {
        r.lane: r.seconds for r in day_rollups if r.local_date == day_date and supported(r)
    }
    baseline_by_lane: dict[str, list[int]] = {lane: [] for lane in LANES}
    for r in baseline_rollups:
        if (
            r.lane in baseline_by_lane
            and baseline_start <= r.local_date <= baseline_end
            and supported(r)
        ):
            baseline_by_lane[r.lane].append(r.seconds)

    balances = [
        b
        for b in compute_daily_balance(day_seconds_by_lane, baseline_by_lane)
        if b.lane in day_seconds_by_lane
    ]

    insights = synthesize_lane_skew_insights(day_date, balances, window_days=baseline_lookback_days)
    # The pure synthesis text describes consecutive calendar days. Missing
    # dates cannot be compressed into a confident shorter debt window.
    sleep_by_day = {
        row.local_date: row.seconds
        for row in baseline_rollups
        if row.lane == "sleep"
        and baseline_start <= row.local_date <= baseline_end
        and supported(row)
    }
    expected_days = {baseline_start + timedelta(days=i) for i in range(baseline_lookback_days)}
    sleep_window = (
        [*(sleep_by_day[day] for day in sorted(expected_days)), day_seconds_by_lane["sleep"]]
        if "sleep" in day_seconds_by_lane and set(sleep_by_day) == expected_days
        else []
    )
    sleep_debt = synthesize_sleep_debt_insight(
        day_date,
        sleep_daily_seconds=sleep_window,
        sleep_baseline_seconds=_mean(baseline_by_lane.get("sleep", [])),
    )
    if sleep_debt is not None and "sleep" in day_seconds_by_lane:
        insights.append(sleep_debt)

    self_reminders = synthesize_self_reminders(day_date, flags)

    return await execute_writeback(
        insights=insights,
        self_reminders=self_reminders,
        store_fact_fn=store_fact_fn,
    )


# ---------------------------------------------------------------------------
# Production collaborator factories
# ---------------------------------------------------------------------------


def build_chronicler_fact_writer(pool: Any, embedding_engine: Any) -> StoreFactFn:
    """Bind ``memory.storage.store_fact`` to the chronicler's OWN schema.

    The returned callable only ever writes through ``pool`` (chronicler schema)
    with ``source_butler="chronicler"`` — it structurally cannot write another
    butler's schema.
    """
    from butlers.modules.memory import storage as memory_storage

    async def _write(
        *,
        subject: str,
        predicate: str,
        content: str,
        permanence: str,
        importance: float,
        confidence: float,
        tags: list[str],
        metadata: dict[str, Any],
    ) -> Any:
        return await memory_storage.store_fact(
            pool,
            subject,
            predicate,
            content,
            embedding_engine,
            importance=importance,
            confidence=confidence,
            permanence=permanence,
            tags=tags,
            metadata=metadata,
            source_butler=SOURCE_BUTLER,
            source_schema=SOURCE_BUTLER,
        )

    return _write


__all__ = [
    "INSIGHT_CONFIDENCE_CEILING",
    "INSIGHT_CONFIDENCE_FLOOR",
    "PREDICATE_LANE_SKEW",
    "PREDICATE_SELF_REMINDER",
    "PREDICATE_SLEEP_DEBT",
    "SLEEP_DEBT_THRESHOLD_SECONDS",
    "SOURCE_BUTLER",
    "InsightFact",
    "WriteBackResult",
    "build_chronicler_fact_writer",
    "evidence_confidence",
    "execute_writeback",
    "run_day_close_writeback",
    "synthesize_lane_skew_insights",
    "synthesize_self_reminders",
    "synthesize_sleep_debt_insight",
]
