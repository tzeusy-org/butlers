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
    LANES,
    category_for,
    lane_for_activity,
    lane_for_category,
    union_seconds,
)
from butlers.chronicler.contracts import INITIAL_SOURCES
from butlers.chronicler.models import Compatibility

# ── Mapping fixture: all active SUPPORTED source/episode_type pairs ────────

# The D1 table — expected (source_name, episode_type, source category).
# core.sessions is listed once per trigger_source branch. The trigger_source
# column is the fourth element; None means "no trigger_source" (→ 'tasks').
#
# Calendar is intentionally absent: calendar rows are the intent layer and have
# no source category (they resolve to 'other' and are dropped by the layer
# filter, see lane_for_activity). workout_episode now has its own 'workout'
# category so it can fold into the Exercise lane (IEA reframe, tasks.md §4).
_D1_PAIRS: list[tuple[str, str, str | None, str]] = [
    ("core.sessions", "work", "route", "conversations"),
    ("core.sessions", "work", "trigger", "tasks"),
    ("core.sessions", "work", "external", "tasks"),
    ("core.sessions", "work", "dashboard", "tasks"),
    ("core.sessions", "work", None, "tasks"),
    ("spotify.session_summary", "listening_episode", None, "music"),
    ("steam.play_history", "play_episode", None, "gaming"),
    ("owntracks.points", "movement_episode", None, "travel"),
    ("google_health.measurements", "sleep_episode", None, "sleep"),
    ("google_health.measurements", "workout_episode", None, "workout"),
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
    """Every D1 mapping must return its declared source category."""
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


def test_calendar_has_no_source_category() -> None:
    """Calendar projections are intent, not a source category → 'other'."""
    assert category_for("google_calendar.completed", "scheduled_block") == "other"


# ── Activity lane mapping (one assertion per lane) ─────────────────────────

# Maps each source category to the life-balance lane it folds into. Drives a
# per-lane test so adding/renaming a lane fails loudly.
_LANE_BY_CATEGORY: dict[str, str] = {
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


@pytest.mark.parametrize("category,lane", sorted(_LANE_BY_CATEGORY.items()))
def test_lane_for_category_per_lane(category: str, lane: str) -> None:
    """Every source category folds into its declared Activity lane."""
    assert lane_for_category(category) == lane
    assert lane in LANES


def test_every_lane_is_covered() -> None:
    """The mapping table must exercise all eight Activity lanes."""
    assert set(_LANE_BY_CATEGORY.values()) == set(LANES)


def test_lane_for_category_unmapped_is_none() -> None:
    """'other' and any absent category (e.g. dropped 'calendar') → no lane."""
    assert lane_for_category("other") is None
    assert lane_for_category("calendar") is None
    assert lane_for_category("nonexistent") is None


# ── Activity-layer counting seam (lane_for_activity) ───────────────────────


def test_lane_for_activity_counts_activity_layer() -> None:
    """An activity-layer episode folds into its lane."""
    assert lane_for_activity("activity", "owntracks.points", "movement_episode") == "travel"
    assert (
        lane_for_activity("activity", "google_health.measurements", "workout_episode") == "exercise"
    )
    # core.sessions conversations + tasks both count as Work.
    assert lane_for_activity("activity", "core.sessions", "work", trigger_source="route") == "work"
    assert lane_for_activity("activity", "core.sessions", "work") == "work"


def test_lane_for_activity_drops_intent_and_evidence() -> None:
    """intent (calendar) and evidence (raw signals) layers never count."""
    # An uncorroborated 5h calendar block is intent → 0s in every lane.
    assert lane_for_activity("intent", "google_calendar.completed", "scheduled_block") is None
    # Raw GPS points (evidence) do not count on their own.
    assert lane_for_activity("evidence", "owntracks.points", "movement_episode") is None


def test_lane_for_activity_drops_unmapped_activity() -> None:
    """An activity row whose source has no lane is not counted."""
    assert lane_for_activity("activity", "totally.unknown", "mystery") is None


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
    wiring it in the D1 mapping table unless it is explicitly point-event-only
    or an intent-only source. Point-event-only sources can still be SUPPORTED
    without becoming lanes; intent-only sources (calendar) are never counted.
    """
    point_event_only_sources = {"health.steps", "health.heart_rate"}
    # Calendar is the intent layer: shown as a planned block, never counted as
    # lived time, so it has no source category / lane (IEA reframe, §4).
    intent_only_sources = {"google_calendar.completed"}
    supported_source_names = {
        s.source_name
        for s in INITIAL_SOURCES
        if s.chronicler_compatibility == Compatibility.SUPPORTED
    }
    d1_source_names = {pair[0] for pair in _D1_PAIRS}

    # Every lane-bearing SUPPORTED source must have at least one D1 entry.
    missing = (
        supported_source_names - d1_source_names - point_event_only_sources - intent_only_sources
    )
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
