"""Outcome shaping: let a measured class verdict steer delivery, only on the owner's say-so.

bu-s11n0s.15 slices 3-5, openspec change ``insight-outcome-efficacy``. Everything here is
deterministic: no model, no new tool and no new route.

The owner-only ``insight_settings.outcome_shaping`` setting is ``observe`` (the default) or
``shape``. Nothing in the repository writes it; turning ``shape`` on is an owner act. Under
``observe`` no function here changes what the broker sends, how it ranks or what it marks.
Under ``shape``:

- a fresh ``self_resolving`` verdict makes that class digest-only: its candidates never go out
  standalone and do not take a delivery slot, they only ride a delivery that is already going
  out (:func:`plan_shaping`);
- a fresh ``effective`` verdict overrides the click-proxy weight with the full weight;
- the owner is told once per class change, at most once per 30 days, in a line that rides the
  delivery and cites the counts, with the existing useful and mute doors on one of the class's
  candidates (:func:`build_notices`);
- the 60-minute any-message engagement mark is retired for classes the outcome signal covers
  (:func:`covered_classes`).

The urgent band (priority at or above ``URGENT_PRIORITY_THRESHOLD``) is never shaped, and the
owner-declared break-through category is never shaped. A verdict older than
:data:`VERDICT_FRESH_FOR` is ignored, so a stalled sweep lapses shaping instead of freezing it.
A ``useful`` reply on a class after its demotion began restores it until the verdict changes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from .outcomes import priority_band

logger = logging.getLogger(__name__)

MODE_OBSERVE = "observe"
MODE_SHAPE = "shape"
#: A class verdict older than this no longer shapes anything.
VERDICT_FRESH_FOR = timedelta(days=14)
#: A class is spoken about at most this often.
NOTICE_INTERVAL = timedelta(days=30)
#: Verdicts the owner is told about when a class enters them.
NOTICED_VERDICTS = frozenset({"self_resolving", "ignored"})
#: The owner's own break-through speech is never shaped.
_UNSHAPED_CATEGORIES = frozenset({"break_through"})


@dataclass(frozen=True)
class ClassVerdict:
    """One class's latest fresh verdict in one priority band."""

    origin_butler: str
    category: str
    band: str
    verdict: str
    n_treated: int
    n_holdout: int
    treated_resolved: int
    holdout_resolved: int
    computed_at: datetime

    @property
    def key(self) -> str:
        return class_key(self.origin_butler, self.category, self.band)


@dataclass(frozen=True)
class Shaped:
    """What a verdict does to one candidate: an optional weight and digest-only delivery."""

    verdict: ClassVerdict
    weight: float | None
    digest_only: bool
    reason: str


@dataclass
class ShapingPlan:
    """The shaping decisions of one delivery cycle."""

    by_candidate: dict[str, Shaped] = field(default_factory=dict)
    verdicts: dict[str, ClassVerdict] = field(default_factory=dict)
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_feedback: dict[tuple[str, str], datetime | None] = field(default_factory=dict)

    def category_reasons(self) -> dict[str, str]:
        """One counted reason per category, for the delivery's ``category_budget_reasons``."""
        return {s.verdict.category: s.reason for s in self.by_candidate.values()}


@dataclass(frozen=True)
class Notice:
    """One class change the owner is told about, with the candidate its doors act on."""

    key: str
    verdict: str
    category: str
    insight_id: str
    text: str


def class_key(origin_butler: str, category: str, band: str) -> str:
    return f"{origin_butler}|{category}|{band}"


async def shaping_mode(pool: Any) -> str:
    """The owner's setting. Anything unreadable or unrecognised is ``observe``."""
    try:
        mode = await pool.fetchval("SELECT outcome_shaping FROM insight_settings WHERE id = 1")
    except Exception:
        logger.warning("insight shaping: setting unreadable; observing", exc_info=True)
        return MODE_OBSERVE
    return MODE_SHAPE if mode == MODE_SHAPE else MODE_OBSERVE


async def _latest_verdicts(
    pool: asyncpg.Pool, classes: Iterable[tuple[str, str]], now: datetime
) -> dict[str, ClassVerdict]:
    """Latest efficacy row per class and non-urgent band, dropped when stale."""
    pairs = sorted(set(classes))
    if not pairs:
        return {}
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (e.origin_butler, e.category, e.priority_band)
               e.origin_butler, e.category, e.priority_band, e.verdict, e.n_treated,
               e.n_holdout, e.treated_resolved, e.holdout_resolved, e.computed_at
        FROM public.insight_class_efficacy e
        JOIN unnest($1::text[], $2::text[]) AS c(origin_butler, category)
          ON c.origin_butler = e.origin_butler AND c.category = e.category
        WHERE e.priority_band <> 'urgent'
        ORDER BY e.origin_butler, e.category, e.priority_band,
                 e.bucket_date DESC, e.computed_at DESC
        """,
        [origin for origin, _ in pairs],
        [category for _, category in pairs],
    )
    verdicts = {}
    for row in rows:
        if now - row["computed_at"] > VERDICT_FRESH_FOR:
            continue
        verdict = ClassVerdict(
            origin_butler=row["origin_butler"],
            category=row["category"],
            band=row["priority_band"],
            verdict=row["verdict"],
            n_treated=row["n_treated"],
            n_holdout=row["n_holdout"],
            treated_resolved=row["treated_resolved"],
            holdout_resolved=row["holdout_resolved"],
            computed_at=row["computed_at"],
        )
        verdicts[verdict.key] = verdict
    return verdicts


async def covered_classes(pool: asyncpg.Pool, *, now: datetime) -> list[tuple[str, str]]:
    """Classes the outcome signal measures: a fresh non-urgent verdict other than unmeasured."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (origin_butler, category, priority_band)
               origin_butler, category, verdict, computed_at
        FROM public.insight_class_efficacy
        WHERE priority_band <> 'urgent'
        ORDER BY origin_butler, category, priority_band, bucket_date DESC, computed_at DESC
        """
    )
    covered = {
        (row["origin_butler"], row["category"])
        for row in rows
        if row["verdict"] != "unmeasured" and now - row["computed_at"] <= VERDICT_FRESH_FOR
    }
    return sorted(covered)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _read_states(settings: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = settings.get("shaping_state")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, Mapping)}


def next_state(previous: Mapping[str, Any] | None, verdict: str, now: datetime) -> dict[str, Any]:
    """Track the verdict a class is in and since when; a changed verdict restarts ``since``."""
    previous = dict(previous or {})
    if previous.get("verdict") == verdict and _parse_time(previous.get("since")) is not None:
        return previous
    return {**previous, "verdict": verdict, "since": now.isoformat()}


def _counts(v: ClassVerdict) -> str:
    return (
        f"{v.holdout_resolved} of {v.n_holdout} resolved without my telling you, "
        f"{v.treated_resolved} of {v.n_treated} when I did"
    )


def decide(
    verdict: ClassVerdict, state: Mapping[str, Any], last_useful: datetime | None
) -> Shaped | None:
    """What one fresh verdict does to its class, or ``None`` when delivery is left alone."""
    if verdict.band == "urgent" or verdict.category in _UNSHAPED_CATEGORIES:
        return None
    if verdict.verdict == "effective":
        return Shaped(
            verdict,
            1.0,
            False,
            f"{verdict.category}: nudges measured as working ({_counts(verdict)}); "
            "baseline attention weight",
        )
    if verdict.verdict == "self_resolving":
        since = _parse_time(state.get("since"))
        if last_useful is not None and since is not None and last_useful >= since:
            return None  # the owner said keep after this verdict began
        return Shaped(
            verdict,
            0.5,
            True,
            f"{verdict.category}: digest only, it resolves on its own ({_counts(verdict)})",
        )
    return None


async def plan_shaping(
    pool: asyncpg.Pool,
    candidates: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
    *,
    now: datetime,
) -> ShapingPlan:
    """Decide, per pending candidate, how a measured verdict shapes this cycle.

    Reads only the efficacy rollup, the owner's feedback and the per-class state. Persists the
    state's verdict-change bookkeeping (not an owner-facing effect). Fails open: a read error
    leaves the plan empty, which is exactly ``observe``.
    """
    plan = ShapingPlan()
    shaped = [
        c
        for c in candidates
        if c["category"] not in _UNSHAPED_CATEGORIES and priority_band(c["priority"]) != "urgent"
    ]
    if not shaped:
        return plan
    try:
        verdicts = await _latest_verdicts(
            pool, {(c["origin_butler"], c["category"]) for c in shaped}, now
        )
        feedback = await pool.fetch(
            """
            SELECT origin_butler, category,
                   max(decided_at) FILTER (WHERE verdict = 'useful') AS last_useful,
                   max(decided_at) AS last_any
            FROM insight_feedback
            WHERE category = ANY($1::text[])
            GROUP BY origin_butler, category
            """,
            sorted({c["category"] for c in shaped}),
        )
    except Exception:
        logger.warning(
            "insight shaping: efficacy unreadable; leaving delivery as is", exc_info=True
        )
        return ShapingPlan()
    last_useful = {(r["origin_butler"], r["category"]): r["last_useful"] for r in feedback}
    plan.last_feedback = {(r["origin_butler"], r["category"]): r["last_any"] for r in feedback}
    plan.verdicts = verdicts
    states = _read_states(settings)
    changed: dict[str, dict[str, Any]] = {}
    for key, verdict in verdicts.items():
        state = next_state(states.get(key), verdict.verdict, now)
        if state != states.get(key):
            changed[key] = state
        plan.states[key] = state
    for candidate in shaped:
        key = class_key(
            candidate["origin_butler"], candidate["category"], priority_band(candidate["priority"])
        )
        verdict = verdicts.get(key)
        if verdict is None:
            continue
        decision = decide(
            verdict,
            plan.states[key],
            last_useful.get((candidate["origin_butler"], candidate["category"])),
        )
        if decision is not None:
            plan.by_candidate[str(candidate["id"])] = decision
    for key, state in changed.items():
        await _store_state(pool, key, state)
    return plan


async def _store_state(pool: asyncpg.Pool, key: str, state: Mapping[str, Any]) -> None:
    try:
        await pool.execute(
            "UPDATE insight_settings SET shaping_state = "
            "COALESCE(shaping_state, '{}'::jsonb) || jsonb_build_object($1::text, $2::jsonb) "
            "WHERE id = 1",
            key,
            json.dumps(dict(state)),
        )
    except Exception:
        logger.warning("insight shaping: could not store class state", exc_info=True)


def _notice_text(v: ClassVerdict) -> str:
    if v.verdict == "self_resolving":
        return (
            f"I stopped pushing {v.category} reminders on their own: {_counts(v)}. "
            "They ride this digest instead. Keep restores them, quiet mutes them."
        )
    return (
        f"My {v.category} nudges have not moved anything: {v.n_treated - v.treated_resolved} "
        f"of {v.n_treated} I sent are still open and I heard nothing back. "
        "Keep leaves them coming, quiet mutes them."
    )


def build_notices(
    plan: ShapingPlan, delivered: Sequence[Mapping[str, Any]], *, now: datetime
) -> list[Notice]:
    """One notice per class change not yet spoken, for classes riding this delivery.

    A class is spoken about when its verdict is one the owner is told about, it differs from
    the verdict last spoken, and nothing was said about the class in the last 30 days. An
    ``ignored`` class is only spoken about when the owner has given no feedback on it in that
    time. The notice rides a delivery already going out and names one delivered candidate, so
    the existing useful and mute doors act on it.
    """
    notices: list[Notice] = []
    seen: set[str] = set()
    for candidate in delivered:
        origin, category = candidate["origin_butler"], candidate["category"]
        key = class_key(origin, category, priority_band(candidate["priority"]))
        state = plan.states.get(key)
        if state is None or key in seen:
            continue
        verdict = plan.verdicts.get(key)
        if verdict is None or verdict.verdict not in NOTICED_VERDICTS:
            continue
        if verdict.verdict == "self_resolving":
            shaped = plan.by_candidate.get(str(candidate["id"]))
            if shaped is None or not shaped.digest_only:
                continue  # restored by the owner: nothing was demoted
        if state.get("noticed_verdict") == verdict.verdict:
            continue
        noticed_at = _parse_time(state.get("noticed_at"))
        if noticed_at is not None and now - noticed_at < NOTICE_INTERVAL:
            continue
        if verdict.verdict == "ignored":
            last_any = plan.last_feedback.get((origin, category))
            if last_any is not None and now - last_any < NOTICE_INTERVAL:
                continue
        seen.add(key)
        notices.append(
            Notice(key, verdict.verdict, category, str(candidate["id"]), _notice_text(verdict))
        )
    return notices


async def record_notices(pool: asyncpg.Pool, notices: Sequence[Notice], *, now: datetime) -> None:
    """Remember what was said, after the delivery that carried it succeeded."""
    for notice in notices:
        try:
            await pool.execute(
                "UPDATE insight_settings SET shaping_state = "
                "COALESCE(shaping_state, '{}'::jsonb) || jsonb_build_object($1::text, "
                "COALESCE(shaping_state -> $1, '{}'::jsonb) || "
                "jsonb_build_object('noticed_at', $2::text, 'noticed_verdict', $3::text)) "
                "WHERE id = 1",
                notice.key,
                now.isoformat(),
                notice.verdict,
            )
        except Exception:
            logger.warning("insight shaping: could not record a notice", exc_info=True)
