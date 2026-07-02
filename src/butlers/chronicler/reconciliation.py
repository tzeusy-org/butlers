"""Deterministic day-close reconciliation core (tasks.md §7).

Pure, deterministic reconciliation of a day's candidate ``activity`` episodes
and calendar ``intent`` blocks. No I/O, no LLM, no randomness — this is the
aggregate-correctness seam design.md promises under "Tier 2 — day-close
reconciliation": the once-daily ``chronicler_day_close`` LLM only *narrates
over* what :func:`reconcile_day` decides. It cannot change which blocks are
counted or dropped (spec: "Deterministic Reconciliation Core With LLM
Narration").

Two rules, matching design.md's worked examples exactly:

1. **Duplicate merge** — ``activity``-layer candidates in the same
   :func:`~butlers.chronicler.aggregations.lane_for_activity` lane whose
   windows overlap describe one lived block observed by more than one
   source. They are merged into a single episode: the time window becomes
   the union span, ``evidence_refs`` becomes the union of every candidate's
   own evidence, and confidence is bumped one rung when the merge itself is
   corroboration (2+ distinct ``source_name`` values agreeing on the same
   block).
2. **Conflict drop** — an ``intent``-layer (calendar) episode is dropped when
   a ``rest``-lane activity (idle / at-home presence — the only lane whose
   *mere presence* contradicts nearly any planned block) overlaps
   ``contradiction_overlap_fraction`` or more of its window. This is the
   design.md example verbatim: "calendar said gym 9am but GPS says home →
   drop the intent, do not count." The drop is reported, never silently
   discarded (:class:`DroppedIntent`), so the day-close narration can mention
   the contradiction without asserting attendance either way
   (butler-chronicler/spec.md §4.15 — calendar is never an attendance
   assertion).

Everything else (``evidence``-layer rows, or a row with no recognized
``layer``) passes through untouched — this function only ever adjudicates
``activity`` vs ``intent``.

Input/output shape
-------------------
Episodes are plain mappings (dicts), matching how the rest of the day-close
path already treats rows (``aggregations.category_for``/``lane_for_activity``,
``bundle_assembler``, the aggregate API handlers) rather than requiring the
heavier ``CorrectedEpisode`` dataclass. Expected keys: ``layer``,
``source_name``, ``episode_type``, ``source_ref``, ``confidence``,
``evidence_refs``, ``payload`` (for ``trigger_source``, only read for
``core.sessions`` rows), and a time window via ``canonical_start_at`` /
``canonical_end_at`` (falling back to ``start_at`` / ``end_at``). ``layer`` and
``confidence`` accept either plain strings or the corresponding
``butlers.chronicler.models`` Enum members (both shapes appear across
call sites — ``dataclasses.asdict(CorrectedEpisode(...))`` keeps Enum members,
while raw SQL rows are already plain strings).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from butlers.chronicler.aggregations import lane_for_activity

# Lane whose presence during a calendar window is treated as direct
# contradicting evidence. A rest/idle-presence activity spanning most of an
# intent's window means the owner was home, not out attending it — the sole
# deterministic contradiction rule design.md specifies. Extending this to
# other lane-vs-intent contradictions (e.g. explicit "away" evidence vs a
# "stay home" block) is future work, not required by tasks.md §7.
CONTRADICTING_LANE = "rest"

# Fraction of the intent's own duration that a contradicting rest-lane
# activity must overlap before the intent is dropped. 0.5 requires the
# "at home" evidence to cover the majority of the scheduled window — a
# five-minute stop at home mid-commute should not silently drop an entire
# afternoon block.
DEFAULT_CONTRADICTION_OVERLAP_FRACTION = 0.5

_CONFIDENCE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
_CONFIDENCE_BY_RANK: dict[int, str] = {rank: name for name, rank in _CONFIDENCE_RANK.items()}


def _str_value(value: Any) -> str:
    """Normalize an Enum member (or plain string) to its string value."""
    return str(getattr(value, "value", value))


def _window(ep: Mapping[str, Any]) -> tuple[datetime, datetime]:
    """Return the effective ``(start, end)`` window for an episode mapping.

    Prefers the corrected/canonical fields, falling back to the raw
    ``start_at``/``end_at``. A missing end is treated as instantaneous
    (``end == start``) so a point-in-time episode never merges with an
    unrelated neighbour purely because its window was left unbounded.
    """
    start = ep.get("canonical_start_at") or ep.get("start_at")
    end = ep.get("canonical_end_at") or ep.get("end_at") or start
    return start, end


def _overlap_seconds(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    return max(0.0, (end - start).total_seconds())


def _lane(ep: Mapping[str, Any]) -> str | None:
    payload = ep.get("payload") or {}
    trigger_source = payload.get("trigger_source") if isinstance(payload, Mapping) else None
    return lane_for_activity(
        _str_value(ep.get("layer", "evidence")),
        ep.get("source_name", ""),
        ep.get("episode_type", ""),
        trigger_source=trigger_source,
    )


def _evidence_refs(ep: Mapping[str, Any]) -> list[str]:
    return [str(ref) for ref in (ep.get("evidence_refs") or [])]


@dataclass(frozen=True)
class DroppedIntent:
    """A calendar ``intent`` episode dropped by the reconciler.

    ``reason`` is a short, LLM-narratable explanation. It never asserts
    attendance either way (butler-chronicler/spec.md §4.15) — it only reports
    the deterministic contradiction that caused the drop.
    """

    intent: Mapping[str, Any]
    contradicting_activity: Mapping[str, Any]
    overlap_fraction: float
    reason: str = "activity evidence contradicts the scheduled window"


@dataclass(frozen=True)
class ReconciliationResult:
    """Output of :func:`reconcile_day`.

    ``activities``
        ``activity``-layer episodes to count and narrate, with same-lane
        overlapping duplicates already merged. This is the counted set —
        aggregate correctness is fully determined here, before any LLM runs.
    ``kept_intents``
        ``intent``-layer (calendar) episodes not contradicted; still
        displayed as planned blocks, still never counted.
    ``dropped_intents``
        ``intent``-layer episodes dropped as contradicted by activity
        evidence, with the contradicting activity and overlap fraction.
    ``passthrough``
        Everything else (``evidence``-layer rows, or rows with no recognized
        ``layer``) — unmodified.
    """

    activities: list[dict[str, Any]] = field(default_factory=list)
    kept_intents: list[dict[str, Any]] = field(default_factory=list)
    dropped_intents: list[DroppedIntent] = field(default_factory=list)
    passthrough: list[dict[str, Any]] = field(default_factory=list)


def _merge_cluster(cluster: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge one cluster of overlapping same-lane candidates into one episode."""
    if len(cluster) == 1:
        return dict(cluster[0])

    ordered = sorted(cluster, key=_window)
    windows = [_window(ep) for ep in ordered]
    merged_start = min(start for start, _ in windows)
    merged_end = max(end for _, end in windows)

    # Primary = highest-confidence candidate supplies the surviving identity
    # (id, title, source_name, source_ref, ...). Ties resolve to the
    # earliest-starting candidate: max() keeps the first-seen item on a tie,
    # and `ordered` is already sorted by start time ascending.
    primary = max(
        ordered, key=lambda ep: _CONFIDENCE_RANK.get(_str_value(ep.get("confidence", "low")), 0)
    )

    evidence_refs: list[str] = []
    seen: set[str] = set()
    for ep in ordered:
        for ref in _evidence_refs(ep):
            if ref not in seen:
                evidence_refs.append(ref)
                seen.add(ref)

    # 2+ distinct sources independently emitting the same block is itself
    # corroboration — bump confidence one rung (capped at "high").
    distinct_sources = {ep.get("source_name") for ep in ordered}
    confidence_rank = _CONFIDENCE_RANK.get(_str_value(primary.get("confidence", "low")), 0)
    if len(distinct_sources) >= 2:
        confidence_rank = min(confidence_rank + 1, _CONFIDENCE_RANK["high"])

    merged = dict(primary)
    merged["canonical_start_at"] = merged_start
    merged["canonical_end_at"] = merged_end
    merged["evidence_refs"] = evidence_refs
    merged["confidence"] = _CONFIDENCE_BY_RANK[confidence_rank]
    # Narration/debug convenience: every source_ref that fed the merge,
    # including the primary's own — lets the day-close narration say "this
    # block combines candidates from Spotify and Steam" without re-deriving
    # cluster membership.
    merged["merged_source_refs"] = [ep.get("source_ref") for ep in ordered]
    return merged


def _merge_same_lane(lane_episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Time-overlap merge candidates within a single lane.

    Sorts by window and greedily merges any episode whose start falls before
    the running cluster's end. Two candidates that merely touch
    (``start == prior_end``) are adjacent, not overlapping, and stay separate
    — this is an *overlap* merge, not an adjacency merge.
    """
    ordered = sorted(lane_episodes, key=_window)
    merged: list[dict[str, Any]] = []
    cluster: list[dict[str, Any]] = []
    cluster_end: datetime | None = None

    for ep in ordered:
        start, end = _window(ep)
        if cluster and cluster_end is not None and start < cluster_end:
            cluster.append(ep)
            cluster_end = max(cluster_end, end)
        else:
            if cluster:
                merged.append(_merge_cluster(cluster))
            cluster = [ep]
            cluster_end = end
    if cluster:
        merged.append(_merge_cluster(cluster))
    return merged


def _find_contradiction(
    intent: Mapping[str, Any],
    rest_activities: Sequence[Mapping[str, Any]],
    *,
    overlap_fraction: float,
) -> DroppedIntent | None:
    i_start, i_end = _window(intent)
    duration = (i_end - i_start).total_seconds()
    if duration <= 0:
        return None

    best: DroppedIntent | None = None
    for activity in rest_activities:
        a_start, a_end = _window(activity)
        overlap = _overlap_seconds(i_start, i_end, a_start, a_end)
        if overlap <= 0:
            continue
        fraction = overlap / duration
        if fraction < overlap_fraction:
            continue
        if best is None or fraction > best.overlap_fraction:
            best = DroppedIntent(
                intent=intent, contradicting_activity=activity, overlap_fraction=fraction
            )
    return best


def reconcile_day(
    episodes: Sequence[Mapping[str, Any]],
    *,
    contradiction_overlap_fraction: float = DEFAULT_CONTRADICTION_OVERLAP_FRACTION,
) -> ReconciliationResult:
    """Deterministically reconcile one day's candidate episodes.

    Pure function — no I/O, no LLM, no randomness (tasks.md §7 / design.md
    "Tier 2 — day-close reconciliation"). Callers pass every episode row for
    the closed day, of any ``layer``; the result is what the once-daily
    ``chronicler_day_close`` LLM narrates over. See the module docstring for
    the two rules applied (duplicate merge, conflict drop) and the expected
    episode-mapping shape.
    """
    activities_by_lane: dict[str | None, list[dict[str, Any]]] = {}
    intents: list[dict[str, Any]] = []
    passthrough: list[dict[str, Any]] = []

    for ep in episodes:
        layer = _str_value(ep.get("layer", "evidence"))
        start = ep.get("canonical_start_at") or ep.get("start_at")
        if layer in ("activity", "intent") and (start is None or start == ""):
            # No usable window to sort/subtract on — comparing None against a
            # datetime crashes _merge_same_lane's sort and _find_contradiction's
            # duration math. Nothing to reconcile without a start time, so
            # pass the row through untouched rather than risk a TypeError.
            passthrough.append(dict(ep))
        elif layer == "activity":
            activities_by_lane.setdefault(_lane(ep), []).append(dict(ep))
        elif layer == "intent":
            intents.append(dict(ep))
        else:
            passthrough.append(dict(ep))

    reconciled_activities: list[dict[str, Any]] = []
    for lane, lane_episodes in activities_by_lane.items():
        if lane is None:
            # Unmapped source/category — nothing to compare against; pass
            # through individually rather than guess at a merge group.
            reconciled_activities.extend(lane_episodes)
        else:
            reconciled_activities.extend(_merge_same_lane(lane_episodes))

    rest_activities = [ep for ep in reconciled_activities if _lane(ep) == CONTRADICTING_LANE]

    kept_intents: list[dict[str, Any]] = []
    dropped_intents: list[DroppedIntent] = []
    for intent in intents:
        dropped = _find_contradiction(
            intent, rest_activities, overlap_fraction=contradiction_overlap_fraction
        )
        if dropped is not None:
            dropped_intents.append(dropped)
        else:
            kept_intents.append(intent)

    return ReconciliationResult(
        activities=reconciled_activities,
        kept_intents=kept_intents,
        dropped_intents=dropped_intents,
        passthrough=passthrough,
    )


__all__ = [
    "CONTRADICTING_LANE",
    "DEFAULT_CONTRADICTION_OVERLAP_FRACTION",
    "DroppedIntent",
    "ReconciliationResult",
    "reconcile_day",
]
