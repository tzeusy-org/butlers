"""Tests for butlers.chronicler.aggregations.

Covers:
- category_for() returns the correct non-'other' category for every active
  SUPPORTED source/episode_type pair in the aggregation category map.
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
    _CATEGORY_MAP,
    CATEGORIES,
    LANES,
    category_for,
    lane_for_activity,
    lane_for_category,
    union_seconds,
    untracked_seconds_for_window,
    waking_overlap_seconds,
)
from butlers.chronicler.contracts import INITIAL_SOURCES
from butlers.chronicler.models import Compatibility

# ── Mapping fixture: all active SUPPORTED source/episode_type pairs ────────

# The D1 cases are projected from the canonical adapter registry, never copied
# into a second test-only list. Every map-backed case is sourced from the
# registry's active SUPPORTED entries; core.sessions retains its explicit
# trigger-source branches because category_for() handles it outside _CATEGORY_MAP.
_SUPPORTED_SOURCE_NAMES = frozenset(
    source.source_name
    for source in INITIAL_SOURCES
    if source.chronicler_compatibility == Compatibility.SUPPORTED
)

_CORE_SESSION_CASES: tuple[tuple[str | None, str], ...] = (
    ("route", "conversations"),
    ("trigger", "tasks"),
    ("external", "tasks"),
    ("dashboard", "tasks"),
    (None, "tasks"),
)

_D1_PAIRS: list[tuple[str, str, str | None, str]] = [
    ("core.sessions", "work", trigger_source, category)
    for trigger_source, category in _CORE_SESSION_CASES
    if "core.sessions" in _SUPPORTED_SOURCE_NAMES
] + [
    (source_name, episode_type, None, category)
    for (source_name, episode_type), category in _CATEGORY_MAP.items()
    if source_name in _SUPPORTED_SOURCE_NAMES
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
    # Butler LLM sessions → Butler ops lane (bu-whhll.14), not owner Work.
    "conversations": "butler_ops",
    "tasks": "butler_ops",
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
    # Owner occupation (occupation blocks + graduated focus/reading/screen).
    "occupation": "work",
    "ambient": "rest",
}


@pytest.mark.parametrize("category,lane", sorted(_LANE_BY_CATEGORY.items()))
def test_lane_for_category_per_lane(category: str, lane: str) -> None:
    """Every source category folds into its declared Activity lane."""
    assert lane_for_category(category) == lane
    assert lane in LANES


def test_every_lane_is_covered() -> None:
    """The mapping table must exercise all nine Activity lanes."""
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
    # core.sessions conversations + tasks are butler LLM sessions → Butler ops
    # lane (bu-whhll.14), never the owner's Work lane.
    assert (
        lane_for_activity("activity", "core.sessions", "work", trigger_source="route")
        == "butler_ops"
    )
    assert lane_for_activity("activity", "core.sessions", "work") == "butler_ops"
    # Inferred occupation blocks (bu-whhll.10) are the OWNER's Work lane.
    assert (
        lane_for_activity("activity", "chronicler.occupation_inferred", "occupation_block")
        == "work"
    )
    # Graduated owner focus/reading/screen (bu-whhll.14) also fold into Work.
    assert lane_for_activity("activity", "chronicler.focus_inferred", "focus_block") == "work"
    assert lane_for_activity("activity", "activitywatch.window", "screen_episode") == "work"


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


def test_all_supported_episode_adapters_have_non_other_category() -> None:
    """Every lane-bearing SUPPORTED source in contracts.py must map non-``other``.

    _D1_PAIRS is derived from INITIAL_SOURCES and _CATEGORY_MAP. Adding a new
    SUPPORTED episode adapter therefore cannot make this test pass by editing a
    second test-only list: it must have a category map entry unless it is
    explicitly point-event-only or intent-only.
    """
    point_event_only_sources = {"health.steps", "health.heart_rate", "owner_outbound.messages"}
    # Calendar is the intent layer: shown as a planned block, never counted as
    # lived time, so it has no source category / lane (IEA reframe, §4).
    intent_only_sources = {"google_calendar.completed"}
    d1_source_names = {pair[0] for pair in _D1_PAIRS}
    other_mapped = sorted(
        f"{source_name}/{episode_type}"
        for source_name, episode_type, _trigger_source, expected in _D1_PAIRS
        if source_name not in point_event_only_sources
        and source_name not in intent_only_sources
        and expected == "other"
    )

    # Every lane-bearing SUPPORTED source must have at least one D1 entry.
    missing = (
        _SUPPORTED_SOURCE_NAMES - d1_source_names - point_event_only_sources - intent_only_sources
    )
    assert not missing, (
        f"SUPPORTED sources without D1 mapping entries: {sorted(missing)}. "
        "Add the (source_name, episode_type) → category mapping to aggregations._CATEGORY_MAP."
    )
    assert not other_mapped, (
        "SUPPORTED D1 pairs explicitly mapped to 'other': "
        f"{other_mapped}. Map each pair to a lane-bearing category."
    )


def test_supported_adapter_explicitly_mapped_to_other_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generated D1 case mapped to ``other`` cannot satisfy the guard."""
    source_name = "future.adapter"
    monkeypatch.setitem(globals(), "_SUPPORTED_SOURCE_NAMES", frozenset({source_name}))
    monkeypatch.setitem(
        globals(),
        "_D1_PAIRS",
        [(source_name, "future_episode", None, "other")],
    )

    with pytest.raises(AssertionError, match="explicitly mapped to 'other'"):
        test_all_supported_episode_adapters_have_non_other_category()


def test_d1_pairs_are_derived_only_from_supported_sources() -> None:
    """The parametrized category checks never retain stale adapter cases."""
    assert {source_name for source_name, *_ in _D1_PAIRS} <= _SUPPORTED_SOURCE_NAMES


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


# ── waking_overlap_seconds / untracked_seconds_for_window (bu-whhll.13) ─────


def test_waking_overlap_seconds_full_calendar_day() -> None:
    """A full UTC calendar day overlaps 16h of a 06:00-22:00 waking window."""
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    seconds = waking_overlap_seconds(start, end, UTC, waking_hour_start=6, waking_hour_end=22)
    assert seconds == 16 * 3600


def test_waking_overlap_seconds_gap_entirely_within_waking_hours() -> None:
    seconds = waking_overlap_seconds(_dt(10), _dt(14), UTC, waking_hour_start=6, waking_hour_end=22)
    assert seconds == 4 * 3600


def test_waking_overlap_seconds_gap_entirely_outside_waking_hours() -> None:
    # 23:00-05:00 overnight gap has zero overlap with 06:00-22:00.
    gap_start = datetime(2026, 6, 19, 23, 0, tzinfo=UTC)
    gap_end = datetime(2026, 6, 20, 5, 0, tzinfo=UTC)
    seconds = waking_overlap_seconds(
        gap_start, gap_end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert seconds == 0.0


def test_waking_overlap_seconds_multi_day_window() -> None:
    """A 3-day window accumulates 16h/day of waking overlap."""
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=3)
    seconds = waking_overlap_seconds(start, end, UTC, waking_hour_start=6, waking_hour_end=22)
    assert seconds == 3 * 16 * 3600


def test_untracked_seconds_for_window_no_activity_is_full_waking_window() -> None:
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    untracked = untracked_seconds_for_window(
        [], start, end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert untracked == 16 * 3600


def test_untracked_seconds_for_window_full_coverage_is_zero() -> None:
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    activity = [(_dt(6), _dt(22))]
    untracked = untracked_seconds_for_window(
        activity, start, end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert untracked == 0.0


def test_untracked_seconds_for_window_partial_coverage() -> None:
    """4h of evidence inside a 16h waking window leaves 12h untracked —
    the pie-chart honesty regression (bu-whhll.13): a 4h-evidence day must
    not renormalise to a full day."""
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    activity = [(_dt(9), _dt(13))]
    untracked = untracked_seconds_for_window(
        activity, start, end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert untracked == 12 * 3600


def test_untracked_seconds_for_window_activity_outside_waking_hours_is_free() -> None:
    """An overnight activity episode (e.g. sleep) outside the waking window
    contributes nothing to tracked time and does not reduce untracked."""
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    overnight = [(datetime(2026, 6, 19, 0, 0, tzinfo=UTC), _dt(6))]
    untracked = untracked_seconds_for_window(
        overnight, start, end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert untracked == 16 * 3600


def test_untracked_seconds_for_window_nap_during_waking_hours_counts_as_tracked() -> None:
    """A daytime nap is activity-layer too — it reduces untracked without any
    'minus sleep' special-casing (sleep is just another activity interval)."""
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    nap = [(_dt(13), _dt(14))]  # 1h nap, fully inside waking hours
    untracked = untracked_seconds_for_window(
        nap, start, end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert untracked == 15 * 3600


def test_untracked_seconds_for_window_overlapping_intervals_union_not_sum() -> None:
    """Two overlapping activity spans union, not double-count, tracked time."""
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    activity = [(_dt(9), _dt(12)), (_dt(11), _dt(14))]  # union = [9,14) = 5h
    untracked = untracked_seconds_for_window(
        activity, start, end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert untracked == 16 * 3600 - 5 * 3600


def test_untracked_seconds_for_window_clamped_at_zero() -> None:
    """Activity spanning the whole window never makes untracked negative."""
    start = datetime(2026, 6, 19, 0, 0, tzinfo=UTC)
    end = start + timedelta(days=1)
    activity = [(start, end)]
    untracked = untracked_seconds_for_window(
        activity, start, end, UTC, waking_hour_start=6, waking_hour_end=22
    )
    assert untracked == 0.0


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
