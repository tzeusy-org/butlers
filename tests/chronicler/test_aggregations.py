"""Tests for butlers.chronicler.aggregations.

Covers:
- category_for() returns the correct non-'other' category for every active
  SUPPORTED source/episode_type pair declared in contracts.py.
- category_for() returns 'other' for unknown pairs.
- Guardrail: aggregations.py imports nothing from anthropic, openai, or
  claude_agent_sdk.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from butlers.chronicler.aggregations import (
    CATEGORIES,
    category_for,
    is_excluded_all_day,
    union_seconds,
)
from butlers.chronicler.contracts import INITIAL_SOURCES
from butlers.chronicler.models import Compatibility

# ── Mapping fixture: all active SUPPORTED source/episode_type pairs ────────

# The D1 table from design.md — expected (source_name, episode_type, category).
# core.sessions is listed twice: once for each trigger_source branch.
# The trigger_source column is carried through as the fourth element;
# None means "no trigger_source provided" (default → 'tasks').
_D1_PAIRS: list[tuple[str, str, str | None, str]] = [
    ("core.sessions", "work", "route", "conversations"),
    ("core.sessions", "work", "trigger", "tasks"),
    ("core.sessions", "work", "external", "tasks"),
    ("core.sessions", "work", "dashboard", "tasks"),
    ("core.sessions", "work", None, "tasks"),
    ("google_calendar.completed", "scheduled_block", None, "calendar"),
    ("spotify.session_summary", "listening_episode", None, "music"),
    ("steam.play_history", "play_episode", None, "gaming"),
    ("owntracks.points", "movement_episode", None, "travel"),
    ("google_health.measurements", "sleep_episode", None, "sleep"),
    ("google_health.measurements", "workout_episode", None, "other"),
    ("health.meals", "eating_event", None, "meal"),
    ("home_assistant.history", "presence_episode", None, "home"),
    # Inferred chronicler-derived sources (bu-i29ix). Both fold into 'tasks'.
    ("chronicler.focus_inferred", "focus_block", None, "tasks"),
    ("chronicler.reading_inferred", "reading_block", None, "tasks"),
]


@pytest.mark.parametrize("source_name,episode_type,trigger_source,expected", _D1_PAIRS)
def test_category_for_known_pairs(
    source_name: str, episode_type: str, trigger_source: str | None, expected: str
) -> None:
    """Every D1 mapping must return its declared category.

    Most entries map to a specific lane; a small set explicitly maps to
    ``other`` (e.g. ``workout_episode`` per bu-i29ix owner direction:
    no taxonomy reshape, workouts ride in the existing ``other`` lane
    while payload metadata distinguishes activity_type).
    """
    result = category_for(source_name, episode_type, trigger_source=trigger_source)
    assert result == expected, (
        f"category_for({source_name!r}, {episode_type!r}, trigger_source={trigger_source!r}) "
        f"→ {result!r}; expected {expected!r}"
    )


def test_category_for_unknown_pair_returns_other() -> None:
    """Unmapped (source_name, episode_type) pairs must return 'other'."""
    assert category_for("unknown.source", "unknown_type") == "other"
    assert category_for("core.sessions", "nonexistent_type") == "other"
    assert category_for("", "") == "other"


def test_category_for_result_is_always_in_taxonomy() -> None:
    """category_for() must always return a value from the stable taxonomy."""
    for source_name, episode_type, trigger_source, _ in _D1_PAIRS:
        result = category_for(source_name, episode_type, trigger_source=trigger_source)
        assert result in CATEGORIES, (
            f"category_for({source_name!r}, {episode_type!r}, trigger_source={trigger_source!r}) "
            f"returned {result!r} which is not in CATEGORIES"
        )
    # Unknown pair
    assert category_for("x", "y") in CATEGORIES


def test_all_supported_sources_have_non_other_category() -> None:
    """Every lane-bearing SUPPORTED source in contracts.py must map to a category.

    This test enforces that adding a new SUPPORTED source requires also
    wiring it in the D1 mapping table unless it is explicitly point-event-only.
    Point-event-only sources can still be SUPPORTED without becoming lanes.
    """
    point_event_only_sources = {"health.steps", "health.heart_rate"}
    supported_source_names = {
        s.source_name
        for s in INITIAL_SOURCES
        if s.chronicler_compatibility == Compatibility.SUPPORTED
    }
    d1_source_names = {pair[0] for pair in _D1_PAIRS}

    # Every lane-bearing SUPPORTED source must have at least one D1 entry.
    missing = supported_source_names - d1_source_names - point_event_only_sources
    assert not missing, (
        f"SUPPORTED sources without D1 mapping entries: {sorted(missing)}. "
        "Add the (source_name, episode_type) → category mapping to "
        "aggregations._CATEGORY_MAP and a test row to _D1_PAIRS."
    )


# ── Guardrail: no LLM imports ──────────────────────────────────────────────

# ── union_seconds ───────────────────────────────────────────────────────────


def _dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 19, hour, minute, tzinfo=UTC)


def test_union_seconds_empty_is_zero() -> None:
    assert union_seconds([]) == 0.0


def test_union_seconds_disjoint_sums() -> None:
    intervals = [(_dt(9), _dt(10)), (_dt(11), _dt(12, 30))]
    assert union_seconds(intervals) == (1 + 1.5) * 3600


def test_union_seconds_merges_overlap() -> None:
    # Two overlapping hours [9,11) and [10,12) → union is [9,12) = 3h, not 4h.
    intervals = [(_dt(9), _dt(11)), (_dt(10), _dt(12))]
    assert union_seconds(intervals) == 3 * 3600


def test_union_seconds_nested_and_unsorted() -> None:
    # A long span fully containing a short one, supplied out of order.
    intervals = [(_dt(10), _dt(10, 30)), (_dt(9), _dt(13))]
    assert union_seconds(intervals) == 4 * 3600


def test_union_seconds_caps_overlapping_at_window() -> None:
    # Regression for "Calendar 26h of a 24h day": a 24h span plus an overlapping
    # 2h timed event unions to 24h, never 26h.
    day_start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    day_end = day_start + timedelta(hours=24)
    timed = (day_start + timedelta(hours=13), day_start + timedelta(hours=15))
    assert union_seconds([(day_start, day_end), timed]) == 24 * 3600


# ── is_excluded_all_day ──────────────────────────────────────────────────────


def test_all_day_calendar_event_is_excluded() -> None:
    start = datetime(2026, 6, 8, 0, 0, tzinfo=UTC)
    end = datetime(2026, 6, 20, 0, 0, tzinfo=UTC)  # 12-day "Reservist" span
    assert is_excluded_all_day("calendar", start, end) is True


def test_exactly_24h_calendar_event_is_excluded() -> None:
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=24)
    assert is_excluded_all_day("calendar", start, end) is True


def test_timed_calendar_event_is_not_excluded() -> None:
    start = datetime(2026, 6, 19, 13, 0, tzinfo=UTC)
    end = datetime(2026, 6, 19, 15, 0, tzinfo=UTC)
    assert is_excluded_all_day("calendar", start, end) is False


def test_all_day_exclusion_is_scoped_to_calendar() -> None:
    # A full-day presence/home episode is real time spent — never excluded.
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(hours=24)
    assert is_excluded_all_day("home", start, end) is False
    assert is_excluded_all_day("travel", start, end) is False


_AGGREGATIONS_MODULE = (
    Path(__file__).parent.parent.parent / "src" / "butlers" / "chronicler" / "aggregations.py"
)

_FORBIDDEN_IMPORTS = frozenset({"anthropic", "openai", "claude_agent_sdk"})


def test_aggregations_no_llm_imports() -> None:
    """aggregations.py must not import any LLM provider package."""
    source = _AGGREGATIONS_MODULE.read_text()
    tree = ast.parse(source)

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".")[0]
                if root in _FORBIDDEN_IMPORTS:
                    violations.append(node.module)

    assert not violations, f"aggregations.py must not import LLM packages; found: {violations}"
