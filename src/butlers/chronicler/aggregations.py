"""Chronicler aggregation helpers.

This module contains pure, deterministic functions used by aggregate endpoints
and episode list responses. NO I/O. NO LLM. NO side effects.

See design.md §D1 for the full category taxonomy contract.
"""

from __future__ import annotations

# ── Category taxonomy ──────────────────────────────────────────────────────

# Stable category strings. The frontend LANE_TAXONOMY maps these to
# display labels, colours, and icons. The backend never emits colours.
CATEGORIES: frozenset[str] = frozenset(
    {"work", "calendar", "music", "gaming", "travel", "sleep", "meal", "home", "other"}
)

# Static mapping: (source_name, episode_type) → category.
# Mirrors the SUPPORTED source declarations in contracts.py.
# Anything not in this table → "other".
_CATEGORY_MAP: dict[tuple[str, str], str] = {
    ("core.sessions", "work"): "work",
    ("google_calendar.completed", "scheduled_block"): "calendar",
    ("spotify.session_summary", "listening_episode"): "music",
    ("steam.play_history", "play_episode"): "gaming",
    ("owntracks.points", "movement_episode"): "travel",
    ("google_health.measurements", "sleep_episode"): "sleep",
    ("health.meals", "eating_event"): "meal",
    ("home_assistant.history", "presence_episode"): "home",
}


def category_for(source_name: str, episode_type: str) -> str:
    """Return the stable category string for a (source_name, episode_type) pair.

    Returns one of the values in ``CATEGORIES``. Unknown pairs → ``"other"``.

    Pure deterministic function: no I/O, no LLM, no side effects.
    """
    return _CATEGORY_MAP.get((source_name, episode_type), "other")


__all__ = [
    "CATEGORIES",
    "category_for",
]
