"""Situational activity precedence: where the owner was, minus what outranks it.

Pure core of ``chronicler.situation_inferred`` (bu-r3fmja, REQ-butler-chronicler-016).
Given one owner-local day's where-segments (place stays plus a provisional hold at
the last known place), the owner's anchor places and the day's other counted
activity, :func:`derive_blocks` returns the situational blocks to record:

- at the work anchor → ``work_presence_block`` (Work);
- at an airport anchor → ``airport_block`` (Travel);
- at home → ``home_block`` (Rest);
- anywhere else within :data:`TRIP_RADIUS_M` of home → ``outing_block`` (Social).

Each block is its segment minus every span of a higher-ranked lane
(:data:`LANE_PRECEDENCE`) and every owner gap-interview answer, so sleep at home is
Sleep, a Steam session at home is Play, and the rest of the evening is Rest. Pieces
shorter than :data:`MIN_PIECE` are dropped. No I/O, no clock, no model.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo

from butlers.chronicler.adapters.owntracks_trail import haversine_meters
from butlers.chronicler.aggregations import lane_for_activity
from butlers.chronicler.anchors import (
    KIND_AIRPORT,
    KIND_HOME,
    KIND_WORK,
    SOURCE_INFERRED,
    AnchorPlace,
    home_anchor,
    nearest_anchor,
)
from butlers.chronicler.confidence import EvidenceKind, derive_confidence
from butlers.chronicler.models import Confidence

SOURCE_NAME = "chronicler.situation_inferred"
OWNER_ANSWER_SOURCE = "chronicler.owner_answer"

EPISODE_TYPE_WORK = "work_presence_block"
EPISODE_TYPE_OUTING = "outing_block"
EPISODE_TYPE_AIRPORT = "airport_block"
EPISODE_TYPE_HOME = "home_block"

PLACE_KIND_UNREGISTERED = "unregistered"

# Highest first. butler_ops is the butlers' own work, never the owner's whereabouts.
LANE_PRECEDENCE: tuple[str, ...] = (
    "sleep",
    "exercise",
    "travel",
    "work",
    "play",
    "eat",
    "social",
    "rest",
)

MIN_PREEMPTOR = timedelta(minutes=10)
MIN_PIECE = timedelta(minutes=15)
TRIP_RADIUS_M = 50_000.0
TAIL_HOLD_MAX = timedelta(hours=12)
TAIL_RADIUS_M = 150.0
TAIL_BRIDGE_MAX = timedelta(hours=16)

# Sources that never displace a place-derived block: point-like bursts, the
# butlers' own sessions, and ambient music (REQ-butler-chronicler-016).
NON_PREEMPTING_SOURCES: frozenset[str] = frozenset(
    {SOURCE_NAME, "core.sessions", "comms.message_bursts", "spotify.session_summary"}
)

# (source_name, episode_type) → the place kind whose block it corroborates.
CORROBORATORS: dict[tuple[str, str], str] = {
    ("owntracks.ssid_presence", "presence_episode"): KIND_HOME,
    ("home_assistant.history", "presence_episode"): KIND_HOME,
    ("owntracks.ssid_presence", "occupation_presence_episode"): KIND_WORK,
}

_LOCATION_DWELL = EvidenceKind("location_dwell", correlated_with="location")
_REGISTERED_ANCHOR = EvidenceKind("registered_anchor", strong=True, correlated_with="location")
_WIFI = EvidenceKind("wifi_presence")


@dataclass(frozen=True)
class WhereSegment:
    """An interval the owner was at one place (a stay, or the provisional hold)."""

    start_at: datetime
    end_at: datetime
    lat: float
    lon: float
    place_episode_ids: tuple[str, ...] = ()
    bridged: bool = False
    provisional: bool = False


@dataclass(frozen=True)
class ActivitySpan:
    """Another episode of the day, reduced to its counted lane and span."""

    start_at: datetime
    end_at: datetime
    source_name: str
    episode_type: str
    lane: str | None


@dataclass(frozen=True)
class Fix:
    """One accepted location fix."""

    at: datetime
    lat: float
    lon: float


@dataclass(frozen=True)
class SituationBlock:
    episode_type: str
    lane: str
    start_at: datetime
    end_at: datetime
    place_kind: str
    place_label: str | None
    anchor_source: str | None
    distance_from_home_m: int | None
    place_episode_ids: tuple[str, ...]
    bridged: bool
    provisional: bool
    confidence: Confidence

    def source_ref(self, local_date: str) -> str:
        kind = self.episode_type.removesuffix("_block")
        return f"chronicler.situation:{local_date}:{kind}:{int(self.start_at.timestamp())}"


def activity_span(
    *,
    layer: str,
    source_name: str,
    episode_type: str,
    start_at: datetime,
    end_at: datetime | None,
    trigger_source: str | None = None,
) -> ActivitySpan | None:
    """The span an episode can preempt with, or ``None`` if it never preempts."""
    if end_at is None or source_name in NON_PREEMPTING_SOURCES:
        return None
    if end_at - start_at < MIN_PREEMPTOR:
        return None
    lane = lane_for_activity(layer, source_name, episode_type, trigger_source=trigger_source)
    if lane is None:
        return None
    return ActivitySpan(start_at, end_at, source_name, episode_type, lane)


def tail_hold(
    fixes: Sequence[Fix],
    *,
    after: datetime | None,
    now: datetime,
    tz: tzinfo,
    last_stay: WhereSegment | None = None,
    hold_max: timedelta = TAIL_HOLD_MAX,
) -> WhereSegment | None:
    """Hold the owner at their last known place until a newer fix says otherwise.

    ``after`` is the latest end of any place or movement episode; ``last_stay``
    is the place stay ending then, if the latest-ending episode is one.

    - When fixes arrive after ``after``, the hold starts at the earliest fix of
      the trailing run within :data:`TAIL_RADIUS_M` of the latest one with no
      silence over :data:`TAIL_BRIDGE_MAX`, so an open stay the clusterer has
      not emitted yet is covered from its start.
    - Otherwise the clusterer has emitted the open stay up to the latest fix;
      the hold continues it from its end, if the latest fix lies at that place.

    The hold ends at the earliest of latest fix + ``hold_max``, ``now`` and the
    end of the latest fix's owner-local day. Always ``provisional``.
    """
    ordered = sorted(fixes, key=lambda f: f.at)
    if not ordered:
        return None
    latest = ordered[-1]
    next_midnight = datetime.combine(
        latest.at.astimezone(tz).date() + timedelta(days=1), time.min, tz
    )
    end = min(latest.at + hold_max, now, next_midnight)

    if after is not None and latest.at <= after:
        if last_stay is None or last_stay.end_at < latest.at:
            return None
        if haversine_meters(last_stay.lat, last_stay.lon, latest.lat, latest.lon) > TAIL_RADIUS_M:
            return None
        if end <= last_stay.end_at:
            return None
        return WhereSegment(
            start_at=last_stay.end_at,
            end_at=end,
            lat=last_stay.lat,
            lon=last_stay.lon,
            place_episode_ids=last_stay.place_episode_ids,
            bridged=last_stay.bridged,
            provisional=True,
        )

    usable = [f for f in ordered if after is None or f.at > after]
    start = latest
    for fix in reversed(usable[:-1]):
        if start.at - fix.at > TAIL_BRIDGE_MAX:
            break
        if haversine_meters(latest.lat, latest.lon, fix.lat, fix.lon) > TAIL_RADIUS_M:
            break
        start = fix
    if end <= start.at:
        return None
    return WhereSegment(
        start_at=start.at,
        end_at=end,
        lat=latest.lat,
        lon=latest.lon,
        bridged=start is not latest,
        provisional=True,
    )


def subtract(
    start: datetime, end: datetime, spans: Iterable[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """``[start, end)`` minus the union of ``spans``, as ordered pieces."""
    pieces = [(start, end)]
    for s_start, s_end in sorted(spans):
        nxt: list[tuple[datetime, datetime]] = []
        for p_start, p_end in pieces:
            if s_end <= p_start or s_start >= p_end:
                nxt.append((p_start, p_end))
                continue
            if s_start > p_start:
                nxt.append((p_start, s_start))
            if s_end < p_end:
                nxt.append((s_end, p_end))
        pieces = nxt
    return pieces


def _outranks(lane: str | None, block_lane: str) -> bool:
    if lane is None or lane not in LANE_PRECEDENCE:
        return False
    return LANE_PRECEDENCE.index(lane) < LANE_PRECEDENCE.index(block_lane)


def _classify(
    segment: WhereSegment, anchors: Sequence[AnchorPlace]
) -> tuple[str, str, AnchorPlace | None, int | None] | None:
    """(episode_type, lane, anchor, distance_from_home_m), or ``None`` for a trip."""
    home = home_anchor(anchors)
    distance = None
    if home is not None:
        raw = home.distance_m(segment.lat, segment.lon)
        distance = int(round(raw / 100.0)) * 100
    anchor = nearest_anchor(anchors, segment.lat, segment.lon)
    if anchor is not None:
        if anchor.kind == KIND_WORK:
            return EPISODE_TYPE_WORK, "work", anchor, distance
        if anchor.kind == KIND_HOME:
            return EPISODE_TYPE_HOME, "rest", anchor, distance
        if anchor.kind == KIND_AIRPORT:
            return EPISODE_TYPE_AIRPORT, "travel", anchor, distance
        return EPISODE_TYPE_OUTING, "social", anchor, distance
    if home is not None and home.distance_m(segment.lat, segment.lon) > TRIP_RADIUS_M:
        return None
    return EPISODE_TYPE_OUTING, "social", None, distance


def _confidence(
    anchor: AnchorPlace | None,
    *,
    provisional: bool,
    corroborated: bool,
) -> Confidence:
    kinds = [_LOCATION_DWELL]
    if anchor is not None and anchor.source != SOURCE_INFERRED and not provisional:
        kinds.append(_REGISTERED_ANCHOR)
    if corroborated:
        kinds.append(_WIFI)
    return derive_confidence(kinds)


def derive_blocks(
    *,
    day_start: datetime,
    day_end: datetime,
    segments: Sequence[WhereSegment],
    anchors: Sequence[AnchorPlace],
    activities: Sequence[ActivitySpan],
    corrected: Sequence[tuple[datetime, datetime]] = (),
    min_piece: timedelta = MIN_PIECE,
) -> list[SituationBlock]:
    """The day's situational blocks, ordered by start (see module docstring).

    ``corrected`` are the owner-corrected spans of this source's own earlier
    blocks. Re-derivation keeps those rows, so new blocks never overlap them.
    """
    owner_answers = [
        (a.start_at, a.end_at) for a in activities if a.source_name == OWNER_ANSWER_SOURCE
    ]
    owner_answers.extend(corrected)
    blocks: list[SituationBlock] = []
    for segment in sorted(segments, key=lambda s: s.start_at):
        start = max(segment.start_at, day_start)
        end = min(segment.end_at, day_end)
        if end <= start:
            continue
        classified = _classify(segment, anchors)
        if classified is None:
            continue
        episode_type, lane, anchor, distance = classified
        preemptors = [
            (a.start_at, a.end_at)
            for a in activities
            if a.source_name != OWNER_ANSWER_SOURCE and _outranks(a.lane, lane)
        ]
        place_kind = anchor.kind if anchor is not None else PLACE_KIND_UNREGISTERED
        for p_start, p_end in subtract(start, end, [*preemptors, *owner_answers]):
            if p_end - p_start < min_piece:
                continue
            corroborated = any(
                CORROBORATORS.get((a.source_name, a.episode_type)) == place_kind
                and a.start_at < p_end
                and a.end_at > p_start
                for a in activities
            )
            blocks.append(
                SituationBlock(
                    episode_type=episode_type,
                    lane=lane,
                    start_at=p_start,
                    end_at=p_end,
                    place_kind=place_kind,
                    place_label=anchor.label if anchor is not None else None,
                    anchor_source=anchor.source if anchor is not None else None,
                    distance_from_home_m=distance,
                    place_episode_ids=segment.place_episode_ids,
                    bridged=segment.bridged,
                    provisional=segment.provisional,
                    confidence=_confidence(
                        anchor, provisional=segment.provisional, corroborated=corroborated
                    ),
                )
            )
    return blocks


__all__ = [
    "CORROBORATORS",
    "EPISODE_TYPE_AIRPORT",
    "EPISODE_TYPE_HOME",
    "EPISODE_TYPE_OUTING",
    "EPISODE_TYPE_WORK",
    "LANE_PRECEDENCE",
    "MIN_PIECE",
    "MIN_PREEMPTOR",
    "NON_PREEMPTING_SOURCES",
    "OWNER_ANSWER_SOURCE",
    "PLACE_KIND_UNREGISTERED",
    "SOURCE_NAME",
    "TAIL_HOLD_MAX",
    "TRIP_RADIUS_M",
    "ActivitySpan",
    "Fix",
    "SituationBlock",
    "WhereSegment",
    "activity_span",
    "derive_blocks",
    "subtract",
    "tail_hold",
]
