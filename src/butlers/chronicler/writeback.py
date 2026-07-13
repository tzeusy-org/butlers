"""Chronicler day-close memory write-back loop (tasks.md §8, bu-93y4rt).

Doctrine-amended, narrow write-back. After the once-daily ``chronicler_day_close``
prompt produces its retrospective summary, this deterministic completion-hook
step distills *derived* insights from the chronicler's OWN already-materialized
data (``daily_rollups``, ``daily_rollup_flags``, ``episode_entities``) and:

1. writes synthesized insight facts into the chronicler's OWN memory schema
   (``store_fact``, ``source_butler="chronicler"``) with provenance, confidence
   (carried in metadata — the facts table's own ``confidence`` column is fixed
   at 1.0 by ``store_fact``), and decay (via ``permanence``);
2. writes self-reminder facts marking low-confidence day blocks for
   re-reconciliation once more evidence lands;
3. PROPOSES recurring-companion enrichment to the ``relationship`` butler over
   MCP (switchboard-routed ``post_mail``) — never a direct cross-schema write.

Invariants (the §8.6 acceptance contract):

- Every fact write targets the chronicler's OWN schema only. This module is
  handed a ``store_fact_fn`` already bound to the chronicler pool; it never
  receives, and cannot construct, a foreign-schema pool.
- Enrichment leaves the chronicler ONLY as an MCP proposal via the injected
  ``propose_enrichment_fn`` — it never touches ``relationship.entity_facts``.
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
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from butlers.chronicler.aggregations import LANES, lane_for_activity
from butlers.chronicler.balance import (
    DEFAULT_BASELINE_LOOKBACK_DAYS,
    LaneBalance,
    compute_daily_balance,
    is_lane_anomalous,
)

logger = logging.getLogger(__name__)

SOURCE_BUTLER = "chronicler"
RELATIONSHIP_BUTLER = "relationship"

# Sleep debt: cumulative deficit (baseline − actual) over the trailing window,
# counting only days that fell short. Fire an insight once the running debt
# clears this many seconds across at least this many short days — a single
# short night is noise; a sustained pattern is the signal worth remembering.
SLEEP_DEBT_THRESHOLD_SECONDS = 3 * 3600
SLEEP_DEBT_MIN_SHORTFALL_DAYS = 2

# Recurring companion: co-present on at least this many distinct local days
# across the trailing window before it is worth proposing to relationship.
COMPANION_MIN_DISTINCT_DAYS = 3

# Predicate strings for chronicler-owned insight facts.
PREDICATE_LANE_SKEW = "lane-skew"
PREDICATE_SLEEP_DEBT = "sleep-debt"
PREDICATE_SELF_REMINDER = "revisit-low-confidence-block"
PREDICATE_RECURRING_COMPANION = "recurring-companion"

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

    ``confidence`` is carried into fact metadata (the facts table column is
    fixed at 1.0 by ``store_fact``); ``permanence`` sets the decay tier so a
    low-confidence, fast-fading insight can be distinguished from a durable one.
    """

    subject: str
    predicate: str
    content: str
    permanence: str
    importance: float
    confidence: float
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompanionCopresence:
    """Chronicler-owned co-presence observation for one companion entity.

    Derived from ``episode_entities`` on the chronicler's own social-lane
    episodes over a trailing window — no cross-schema read.
    """

    entity_id: str
    distinct_days: int
    episode_count: int
    last_seen_at: datetime | None = None


@dataclass(frozen=True)
class EnrichmentProposal:
    """A recurring-companion enrichment PROPOSED to relationship over MCP.

    Carries the companion's resolved ``entity_id`` (owned by chronicler via
    ``episode_entities``) and the observation that justifies the proposal. The
    chronicler never writes ``relationship.entity_facts``; the proposer merely
    hands this to the relationship butler, which decides on its own terms.
    """

    entity_id: str
    predicate: str
    distinct_days: int
    episode_count: int
    window_start: date
    window_end: date
    dedup_key: str
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WriteBackResult:
    """Outcome counts for one day-close write-back run."""

    insights_written: int = 0
    self_reminders_written: int = 0
    proposals_sent: int = 0
    errors: int = 0


StoreFactFn = Callable[..., Awaitable[Any]]
ProposeEnrichmentFn = Callable[[EnrichmentProposal], Awaitable[Any]]


# ---------------------------------------------------------------------------
# Pure synthesis
# ---------------------------------------------------------------------------


def _mean(values: Sequence[int]) -> float | None:
    return sum(values) / len(values) if values else None


def _fmt_hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f}h"


def synthesize_lane_skew_insights(day: date, balances: Iterable[LaneBalance]) -> list[InsightFact]:
    """One insight per lane whose day-vs-usual delta clears the anomaly bar.

    Reuses :func:`butlers.chronicler.balance.is_lane_anomalous` so the skew
    signal never diverges from the balance/trends API surface.
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
                confidence=0.7,
                tags=("chronicler-insight", PREDICATE_LANE_SKEW, b.lane),
                metadata={
                    "local_date": day.isoformat(),
                    "lane": b.lane,
                    "direction": direction,
                    "seconds": b.seconds,
                    "baseline_seconds": b.baseline_seconds,
                    "delta_seconds": b.delta_seconds,
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
    seconds (baseline days + the closed day). Debt accrues only on days that
    fell short of ``sleep_baseline_seconds``; both a total-debt floor and a
    minimum number of short days must be cleared before an insight is emitted.
    """
    if sleep_baseline_seconds is None or sleep_baseline_seconds <= 0:
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
        confidence=0.7,
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
                confidence=0.4,
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


def synthesize_enrichment_proposals(
    companions: Iterable[CompanionCopresence],
    *,
    window_start: date,
    window_end: date,
    min_distinct_days: int = COMPANION_MIN_DISTINCT_DAYS,
) -> list[EnrichmentProposal]:
    """Recurring-companion enrichment proposals, one per qualifying companion.

    A companion qualifies once it is co-present on ``min_distinct_days`` or more
    distinct local days across ``[window_start, window_end]``.
    """
    window_days = (window_end - window_start).days + 1
    out: list[EnrichmentProposal] = []
    for c in sorted(companions, key=lambda x: str(x.entity_id)):
        if not c.entity_id or c.distinct_days < min_distinct_days:
            continue
        dedup_key = (
            f"{PREDICATE_RECURRING_COMPANION}:{c.entity_id}:"
            f"{window_start.isoformat()}_{window_end.isoformat()}"
        )
        message = (
            f"Recurring companion: co-present on {c.distinct_days} of the last "
            f"{window_days} days ({c.episode_count} social episodes)."
        )
        out.append(
            EnrichmentProposal(
                entity_id=str(c.entity_id),
                predicate=PREDICATE_RECURRING_COMPANION,
                distinct_days=c.distinct_days,
                episode_count=c.episode_count,
                window_start=window_start,
                window_end=window_end,
                dedup_key=dedup_key,
                message=message,
                metadata={
                    "last_seen_at": c.last_seen_at.isoformat() if c.last_seen_at else None,
                    "source": SOURCE_BUTLER,
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
    proposals: Sequence[EnrichmentProposal],
    store_fact_fn: StoreFactFn,
    propose_enrichment_fn: ProposeEnrichmentFn | None,
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

    if propose_enrichment_fn is not None:
        for proposal in proposals:
            try:
                await propose_enrichment_fn(proposal)
            except Exception:
                logger.exception(
                    "chronicler writeback: failed to propose enrichment for entity %s",
                    proposal.entity_id,
                )
                result.errors += 1
                continue
            result.proposals_sent += 1

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


def _local_window_utc(
    start_date: date, end_date: date, tz: str | ZoneInfo
) -> tuple[datetime, datetime]:
    """UTC bounds of ``[start_date 00:00, (end_date + 1 day) 00:00)`` local."""
    zone = _coerce_zone(tz)
    start_local = datetime(start_date.year, start_date.month, start_date.day, tzinfo=zone)
    end_exclusive = end_date + timedelta(days=1)
    end_local = datetime(end_exclusive.year, end_exclusive.month, end_exclusive.day, tzinfo=zone)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


async def fetch_companion_copresence(
    pool: Any,
    *,
    start_date: date,
    end_date: date,
    timezone: str,
) -> list[CompanionCopresence]:
    """Aggregate distinct-day co-presence per companion from OWN episodes.

    Reads only chronicler-owned tables (``v_episodes_corrected`` +
    ``episode_entities``, role != 'owner'), mirroring the ``/who-you-were-with``
    handler's social-lane filter (``lane_for_activity(...) == "social"``) so the
    two surfaces agree on what counts as company. Days are bucketed in the
    owner's local timezone.
    """
    window_start_utc, window_end_utc = _local_window_utc(start_date, end_date, timezone)
    zone = _coerce_zone(timezone)
    try:
        rows = await pool.fetch(
            """
            SELECT
                e.source_name,
                e.episode_type,
                e.start_at,
                e.end_at,
                e.payload,
                ee.entity_id
            FROM v_episodes_corrected e
            JOIN episode_entities ee
                ON ee.episode_id = e.id AND ee.role != 'owner'
            WHERE e.layer = 'activity'
              AND e.tombstone_at IS NULL
              AND e.privacy IN ('normal', 'sensitive')
              AND e.start_at < $2
              AND (e.end_at IS NULL OR e.end_at > $1)
              AND ee.entity_id IS NOT NULL
            """,
            window_start_utc,
            window_end_utc,
        )
    except Exception:
        logger.exception(
            "chronicler writeback: companion co-presence query failed for %s..%s",
            start_date,
            end_date,
        )
        return []

    days_by_entity: dict[str, set[date]] = {}
    count_by_entity: dict[str, int] = {}
    last_seen_by_entity: dict[str, datetime] = {}
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        lane = lane_for_activity(
            "activity",
            row["source_name"],
            row["episode_type"],
            trigger_source=payload.get("trigger_source"),
        )
        if lane != "social":
            continue
        entity_id = str(row["entity_id"])
        ep_start: datetime = row["start_at"]
        local_day = ep_start.astimezone(zone).date()
        days_by_entity.setdefault(entity_id, set()).add(local_day)
        count_by_entity[entity_id] = count_by_entity.get(entity_id, 0) + 1
        prev = last_seen_by_entity.get(entity_id)
        if prev is None or ep_start > prev:
            last_seen_by_entity[entity_id] = ep_start

    return [
        CompanionCopresence(
            entity_id=entity_id,
            distinct_days=len(days),
            episode_count=count_by_entity[entity_id],
            last_seen_at=last_seen_by_entity.get(entity_id),
        )
        for entity_id, days in days_by_entity.items()
    ]


# ---------------------------------------------------------------------------
# Top-level day-close write-back
# ---------------------------------------------------------------------------


async def run_day_close_writeback(
    pool: Any,
    *,
    day_date: date,
    timezone: str,
    store_fact_fn: StoreFactFn,
    propose_enrichment_fn: ProposeEnrichmentFn | None = None,
    baseline_lookback_days: int = DEFAULT_BASELINE_LOOKBACK_DAYS,
) -> WriteBackResult:
    """Synthesize and persist the day-close write-back for ``day_date``.

    Reads the chronicler's OWN ``daily_rollups`` / ``daily_rollup_flags`` for
    the closed day plus a trailing baseline window, synthesizes insights +
    self-reminders + (when a proposer is wired) recurring-companion proposals,
    and dispatches them via the injected collaborators. Returns outcome counts;
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
    except Exception:
        logger.exception(
            "chronicler writeback: rollup fetch failed for %s; skipping write-back", day_date
        )
        return WriteBackResult()

    day_seconds_by_lane = {r.lane: r.seconds for r in day_rollups}
    baseline_by_lane: dict[str, list[int]] = {lane: [] for lane in LANES}
    for r in baseline_rollups:
        if r.lane in baseline_by_lane:
            baseline_by_lane[r.lane].append(r.seconds)

    balances = compute_daily_balance(day_seconds_by_lane, baseline_by_lane)

    insights = synthesize_lane_skew_insights(day_date, balances)
    sleep_window = [*baseline_by_lane.get("sleep", []), day_seconds_by_lane.get("sleep", 0)]
    sleep_debt = synthesize_sleep_debt_insight(
        day_date,
        sleep_daily_seconds=sleep_window,
        sleep_baseline_seconds=_mean(baseline_by_lane.get("sleep", [])),
    )
    if sleep_debt is not None:
        insights.append(sleep_debt)

    self_reminders = synthesize_self_reminders(day_date, flags)

    proposals: list[EnrichmentProposal] = []
    if propose_enrichment_fn is not None:
        companions = await fetch_companion_copresence(
            pool, start_date=baseline_start, end_date=day_date, timezone=timezone
        )
        proposals = synthesize_enrichment_proposals(
            companions, window_start=baseline_start, window_end=day_date
        )

    return await execute_writeback(
        insights=insights,
        self_reminders=self_reminders,
        proposals=proposals,
        store_fact_fn=store_fact_fn,
        propose_enrichment_fn=propose_enrichment_fn,
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
            permanence=permanence,
            tags=tags,
            metadata=metadata,
            source_butler=SOURCE_BUTLER,
            source_schema=SOURCE_BUTLER,
        )

    return _write


def build_relationship_enrichment_proposer(
    switchboard_client_getter: Callable[[], Any],
) -> ProposeEnrichmentFn:
    """Return a proposer that hands enrichment to relationship over MCP.

    ``switchboard_client_getter`` is read lazily at call time (the switchboard
    connection is established after the scheduler loop starts, and the day-close
    hook fires ~once daily long after). Uses the switchboard's ``post_mail``
    tool to deliver a structured, non owner-facing proposal to the relationship
    butler's mailbox. The relationship butler decides whether/how to assert it
    through its OWN authoritative writer; the chronicler never touches
    ``relationship.entity_facts``. A missing client is a silent no-op.
    """

    async def _propose(proposal: EnrichmentProposal) -> Any:
        switchboard_client = switchboard_client_getter()
        if switchboard_client is None:
            logger.debug(
                "chronicler writeback: no switchboard client; skipping enrichment proposal for %s",
                proposal.entity_id,
            )
            return None
        return await switchboard_client.call_tool(
            "post_mail",
            {
                "target_butler": RELATIONSHIP_BUTLER,
                "sender": SOURCE_BUTLER,
                "sender_channel": "butler",
                "subject": "Recurring-companion enrichment proposal",
                "body": proposal.message,
                "metadata": {
                    "kind": "enrichment_proposal",
                    "predicate": proposal.predicate,
                    "entity_id": proposal.entity_id,
                    "distinct_days": proposal.distinct_days,
                    "episode_count": proposal.episode_count,
                    "window_start": proposal.window_start.isoformat(),
                    "window_end": proposal.window_end.isoformat(),
                    "dedup_key": proposal.dedup_key,
                    **proposal.metadata,
                },
            },
        )

    return _propose


__all__ = [
    "COMPANION_MIN_DISTINCT_DAYS",
    "PREDICATE_LANE_SKEW",
    "PREDICATE_RECURRING_COMPANION",
    "PREDICATE_SELF_REMINDER",
    "PREDICATE_SLEEP_DEBT",
    "SLEEP_DEBT_THRESHOLD_SECONDS",
    "SOURCE_BUTLER",
    "CompanionCopresence",
    "EnrichmentProposal",
    "InsightFact",
    "WriteBackResult",
    "build_chronicler_fact_writer",
    "build_relationship_enrichment_proposer",
    "execute_writeback",
    "fetch_companion_copresence",
    "run_day_close_writeback",
    "synthesize_enrichment_proposals",
    "synthesize_lane_skew_insights",
    "synthesize_self_reminders",
    "synthesize_sleep_debt_insight",
]
