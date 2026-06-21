"""Chronicler aggregation helpers.

This module contains pure, deterministic functions used by aggregate endpoints
and episode list responses. NO I/O. NO LLM. NO side effects.

See design.md §D1 for the full category taxonomy contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# ── Category taxonomy ──────────────────────────────────────────────────────

# Stable category strings. The frontend LANE_TAXONOMY maps these to
# display labels, colours, and icons. The backend never emits colours.
#
# core.sessions episodes are split into two lanes by trigger_source:
#   "conversations" — trigger_source='route'  (user→butler interactions)
#   "tasks"         — trigger_source IN {'trigger','external','dashboard'}
#                     or any other / NULL value (scheduled & daemon-fired work)
CATEGORIES: frozenset[str] = frozenset(
    {
        "conversations",
        "tasks",
        "calendar",
        "music",
        "gaming",
        "travel",
        "sleep",
        "meal",
        "home",
        "other",
    }
)

# Static mapping: (source_name, episode_type) → category.
# Mirrors the SUPPORTED source declarations in contracts.py.
# core.sessions is handled separately in category_for() via trigger_source.
# Anything not in this table and not handled by trigger_source → "other".
_CATEGORY_MAP: dict[tuple[str, str], str] = {
    ("google_calendar.completed", "scheduled_block"): "calendar",
    ("spotify.session_summary", "listening_episode"): "music",
    ("steam.play_history", "play_episode"): "gaming",
    ("owntracks.points", "movement_episode"): "travel",
    ("google_health.measurements", "sleep_episode"): "sleep",
    ("google_health.measurements", "workout_episode"): "other",
    ("health.meals", "eating_event"): "meal",
    ("home_assistant.history", "presence_episode"): "home",
    # Inferred chronicler-derived sources (bu-i29ix). Both fold into 'tasks'
    # to avoid a lane taxonomy reshape; payload.signal carries the kind.
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


# ── Duration aggregation helpers ───────────────────────────────────────────

# An episode whose span covers a full calendar day or more is treated as an
# all-day / multi-day background event (e.g. a Google all-day calendar entry or
# a multi-day reservation). These do not represent active "time spent": a
# multi-day event clips to a full 24 h for *every* day it touches, which would
# saturate (and overflow) its category's daily total. They are excluded from the
# "where the time went" aggregations. Detected by span shape because the
# upstream ``all_day`` flag is unreliable.
ALL_DAY_SPAN_THRESHOLD = timedelta(hours=24)

# Categories whose all-day/multi-day episodes are dropped from time-spent
# aggregation. Scoped to calendar: an all-day calendar event is not active time,
# whereas a long presence/location episode (e.g. a full day at home) genuinely
# is and must keep counting.
_ALL_DAY_EXCLUDED_CATEGORIES: frozenset[str] = frozenset({"calendar"})


def is_excluded_all_day(category: str, start: datetime, end: datetime) -> bool:
    """True if the episode is an all-day/multi-day event to drop from totals.

    Only applies to calendar-category episodes spanning a full day or more.
    """
    return category in _ALL_DAY_EXCLUDED_CATEGORIES and (end - start) >= ALL_DAY_SPAN_THRESHOLD


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
    "ALL_DAY_SPAN_THRESHOLD",
    "CATEGORIES",
    "category_for",
    "is_excluded_all_day",
    "union_seconds",
]
