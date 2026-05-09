"""Chronicler aggregation helpers.

This module contains pure, deterministic functions used by aggregate endpoints
and episode list responses. NO I/O. NO LLM. NO side effects.

See design.md §D1 for the full category taxonomy contract.
"""

from __future__ import annotations

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


__all__ = [
    "CATEGORIES",
    "category_for",
]
