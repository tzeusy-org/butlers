"""Chronicler aggregation helpers.

This module contains pure, deterministic functions used by aggregate endpoints
and episode list responses. NO I/O. NO LLM. NO side effects.

See design.md §D1 for the full category taxonomy contract.
"""

from __future__ import annotations

from datetime import datetime

# ── Category taxonomy ──────────────────────────────────────────────────────

# Stable source-category strings. These are the per-source classification a
# raw episode carries; ``lane_for_category`` folds them into the life-balance
# Activity lanes the dashboard renders. The backend never emits colours.
#
# core.sessions episodes are split by trigger_source:
#   "conversations" — trigger_source='route'  (user→butler interactions)
#   "tasks"         — trigger_source IN {'trigger','external','dashboard'}
#                     or any other / NULL value (scheduled & daemon-fired work)
#
# Calendar is deliberately ABSENT: calendar projections are the *intent* layer
# (planned blocks), never counted as lived time, so they have no source
# category here (they resolve to "other" and are dropped by the layer filter).
CATEGORIES: frozenset[str] = frozenset(
    {
        "conversations",
        "tasks",
        "music",
        "gaming",
        "travel",
        "sleep",
        "meal",
        "home",
        "workout",
        "other",
    }
)

# Static mapping: (source_name, episode_type) → source category.
# Mirrors the SUPPORTED source declarations in contracts.py.
# core.sessions is handled separately in category_for() via trigger_source.
# Anything not in this table and not handled by trigger_source → "other".
_CATEGORY_MAP: dict[tuple[str, str], str] = {
    ("spotify.session_summary", "listening_episode"): "music",
    ("steam.play_history", "play_episode"): "gaming",
    ("owntracks.points", "movement_episode"): "travel",
    ("google_health.measurements", "sleep_episode"): "sleep",
    ("google_health.measurements", "workout_episode"): "workout",
    ("health.meals", "eating_event"): "meal",
    ("home_assistant.history", "presence_episode"): "home",
    # Inferred chronicler-derived sources (bu-i29ix). Both fold into 'tasks'
    # (→ Work lane); payload.signal carries the kind.
    ("chronicler.focus_inferred", "focus_block"): "tasks",
    ("chronicler.reading_inferred", "reading_block"): "tasks",
}

# trigger_source values that represent user→butler conversations.
# Everything else (including None) is classified as "tasks".
_CONVERSATION_TRIGGER_SOURCES: frozenset[str] = frozenset({"route"})


def category_for(
    source_name: str,
    episode_type: str,
    *,
    trigger_source: str | None = None,
) -> str:
    """Return the stable category string for an episode.

    For ``core.sessions`` work episodes the category is resolved from
    ``trigger_source``:
    - ``'route'`` → ``'conversations'``  (user→butler interactions)
    - any other value or ``None`` → ``'tasks'``  (scheduled / daemon work)

    For all other sources the ``(source_name, episode_type)`` pair is looked up
    in the static ``_CATEGORY_MAP``.

    Returns one of the values in ``CATEGORIES``. Unknown pairs → ``"other"``.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if source_name == "core.sessions" and episode_type == "work":
        if trigger_source in _CONVERSATION_TRIGGER_SOURCES:
            return "conversations"
        return "tasks"
    return _CATEGORY_MAP.get((source_name, episode_type), "other")


# ── Activity lane taxonomy (IEA, tasks.md §4) ───────────────────────────────

# Top-level life-balance lanes. The frontend LANE_TAXONOMY maps these to
# display labels/colours/icons. These — not data sources — are what every
# time/balance aggregate buckets by. ``music``/``gaming`` fold into Play;
# ``calendar`` is intent and never reaches a lane. See design.md §"Activity
# lane taxonomy".
LANES: frozenset[str] = frozenset(
    {
        "sleep",
        "exercise",
        "work",
        "play",
        "social",
        "travel",
        "eat",
        "rest",
    }
)

# Source category → Activity lane. The left-hand side is a ``category_for``
# output (plus a few forward-compat categories not yet emitted by any adapter:
# ``idle-presence`` and ``social`` arrive with the comms/presence work in
# tasks.md §6). Categories with no lane (``other``, and the absent
# ``calendar``) resolve to ``None`` and are not counted.
_CATEGORY_TO_LANE: dict[str, str] = {
    "conversations": "work",
    "tasks": "work",
    "music": "play",
    "gaming": "play",
    "meal": "eat",
    "home": "rest",
    "idle-presence": "rest",
    "workout": "exercise",
    "movement": "travel",
    "travel": "travel",
    "sleep": "sleep",
    "social": "social",
}


def lane_for_category(category: str) -> str | None:
    """Map a source category onto a life-balance Activity lane.

    Returns one of ``LANES`` or ``None`` when the category has no lane (e.g.
    ``other`` or a calendar/intent category). Pure deterministic function.
    """
    return _CATEGORY_TO_LANE.get(category)


def lane_for_activity(
    layer: str,
    source_name: str,
    episode_type: str,
    *,
    trigger_source: str | None = None,
) -> str | None:
    """Return the Activity lane an episode counts toward, or ``None``.

    This is the single counting seam (tasks.md §4): an episode is counted only
    when it is on the ``activity`` layer. ``intent`` (calendar) and ``evidence``
    (raw signals) layers return ``None`` — this is what drops an uncorroborated
    5 h calendar block to 0 s in every lane. An overlapping ``activity`` episode
    (e.g. a GPS-dwell projection) is the thing that actually counts; calendar is
    never attributed to a lane on its own.

    For ``activity``-layer rows the source category (see ``category_for``) is
    folded onto a lane via ``lane_for_category``; an activity row whose category
    has no lane (e.g. an unmapped source) also returns ``None``.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if str(layer) != "activity":
        return None
    return lane_for_category(category_for(source_name, episode_type, trigger_source=trigger_source))


# ── Duration aggregation helpers ───────────────────────────────────────────


def union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total seconds covered by the union of half-open ``[start, end)`` intervals.

    Overlapping intervals are merged so two concurrent episodes within the same
    bucket are counted once rather than summed (which is what let a category
    exceed the window length). Returns ``0.0`` for an empty list.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    if not intervals:
        return 0.0
    ordered = sorted(intervals, key=lambda iv: iv[0])
    total = 0.0
    cur_start, cur_end = ordered[0]
    for start, end in ordered[1:]:
        if start > cur_end:
            total += (cur_end - cur_start).total_seconds()
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    total += (cur_end - cur_start).total_seconds()
    return total


__all__ = [
    "CATEGORIES",
    "LANES",
    "category_for",
    "lane_for_activity",
    "lane_for_category",
    "union_seconds",
]
