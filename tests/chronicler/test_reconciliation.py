"""Tests for the deterministic day-close reconciliation core (tasks.md §7, bu-boo52q).

Covers the two rules ``reconcile_day`` enforces without an LLM:

- Conflict drop: a calendar ``intent`` block contradicted by ``rest``-lane
  (at-home/idle) activity evidence is dropped and reported, never counted.
- Duplicate merge: overlapping same-lane ``activity`` candidates from
  different sources are merged into one, combining evidence and bumping
  confidence.

Every assertion here exercises a pure deterministic code path — no LLM,
no I/O. Aggregate correctness must not depend on LLM output (spec:
"Deterministic Reconciliation Core With LLM Narration").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from butlers.chronicler.reconciliation import (
    CONTRADICTING_LANE,
    DEFAULT_CONTRADICTION_OVERLAP_FRACTION,
    DroppedIntent,
    ReconciliationResult,
    reconcile_day,
)

pytestmark = pytest.mark.unit

_DAY = datetime(2026, 6, 1, tzinfo=UTC)


def _episode(
    *,
    layer: str,
    source_name: str,
    episode_type: str,
    source_ref: str,
    start_at: datetime,
    end_at: datetime | None = None,
    confidence: str = "low",
    evidence_refs: list[str] | None = None,
    title: str | None = None,
    payload: dict | None = None,
) -> dict:
    return {
        "layer": layer,
        "source_name": source_name,
        "episode_type": episode_type,
        "source_ref": source_ref,
        "canonical_start_at": start_at,
        "canonical_end_at": end_at,
        "canonical_title": title,
        "confidence": confidence,
        "evidence_refs": evidence_refs or [],
        "payload": payload or {},
    }


def _calendar_intent(
    *, source_ref: str = "google_calendar.completed:ev-1", start_at: datetime, end_at: datetime
) -> dict:
    return _episode(
        layer="intent",
        source_name="google_calendar.completed",
        episode_type="scheduled_block",
        source_ref=source_ref,
        start_at=start_at,
        end_at=end_at,
        title="Gym",
    )


def _home_presence(
    *, source_ref: str = "home_assistant.history:presence-1", start_at: datetime, end_at: datetime
) -> dict:
    return _episode(
        layer="activity",
        source_name="home_assistant.history",
        episode_type="presence_episode",
        source_ref=source_ref,
        start_at=start_at,
        end_at=end_at,
        confidence="medium",
    )


def _workout(
    *,
    source_ref: str,
    start_at: datetime,
    end_at: datetime,
    confidence: str = "medium",
    evidence_refs: list[str] | None = None,
) -> dict:
    return _episode(
        layer="activity",
        source_name="google_health.measurements",
        episode_type="workout_episode",
        source_ref=source_ref,
        start_at=start_at,
        end_at=end_at,
        confidence=confidence,
        evidence_refs=evidence_refs,
        title="Workout",
    )


def _inferred_exercise(
    *,
    source_ref: str,
    start_at: datetime,
    end_at: datetime,
    confidence: str = "high",
    evidence_refs: list[str] | None = None,
) -> dict:
    return _episode(
        layer="activity",
        source_name="chronicler.exercise_inferred",
        episode_type="exercise_episode",
        source_ref=source_ref,
        start_at=start_at,
        end_at=end_at,
        confidence=confidence,
        evidence_refs=evidence_refs,
        title="Exercise",
    )


# ---------------------------------------------------------------------------
# Conflict drop: calendar intent contradicted by rest-lane evidence
# ---------------------------------------------------------------------------


def test_conflicting_intent_dropped_when_home_evidence_covers_window() -> None:
    """design.md example: calendar says "gym 9am" but GPS/presence says home."""
    intent = _calendar_intent(start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=10))
    home = _home_presence(
        start_at=_DAY + timedelta(hours=8, minutes=30), end_at=_DAY + timedelta(hours=11)
    )

    result = reconcile_day([intent, home])

    assert result.kept_intents == []
    assert len(result.dropped_intents) == 1
    dropped = result.dropped_intents[0]
    assert isinstance(dropped, DroppedIntent)
    assert dropped.intent["source_ref"] == intent["source_ref"]
    assert dropped.contradicting_activity["source_name"] == "home_assistant.history"
    assert dropped.overlap_fraction == pytest.approx(1.0)
    # Never asserts attendance either way.
    assert "attend" not in dropped.reason.lower()


def test_conflicting_intent_dropped_holds_without_llm() -> None:
    """The drop is a pure function of the inputs -- deterministic, repeatable."""
    intent = _calendar_intent(start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=10))
    home = _home_presence(start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=10))

    r1 = reconcile_day([intent, home])
    r2 = reconcile_day([intent, home])

    assert len(r1.dropped_intents) == len(r2.dropped_intents) == 1
    assert r1.dropped_intents[0].overlap_fraction == r2.dropped_intents[0].overlap_fraction


def test_intent_kept_when_no_contradicting_activity() -> None:
    """An uncontradicted calendar block stays -- still displayed, never counted."""
    intent = _calendar_intent(start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=10))

    result = reconcile_day([intent])

    assert result.dropped_intents == []
    assert len(result.kept_intents) == 1
    assert result.kept_intents[0]["source_ref"] == intent["source_ref"]


def test_intent_kept_when_home_activity_is_a_different_lane() -> None:
    """Only the rest lane is a contradiction signal -- a workout overlap is
    corroboration, not a contradiction, and must not drop the intent."""
    intent = _calendar_intent(start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=10))
    workout = _workout(
        source_ref="google_health.measurements:w-1",
        start_at=_DAY + timedelta(hours=9),
        end_at=_DAY + timedelta(hours=10),
    )

    result = reconcile_day([intent, workout])

    assert result.dropped_intents == []
    assert len(result.kept_intents) == 1


def test_intent_kept_when_overlap_below_threshold() -> None:
    """A brief at-home dip mid-window is not enough evidence to drop a whole block."""
    intent = _calendar_intent(
        start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=13)
    )  # 4h block
    home = _home_presence(
        start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=9, minutes=30)
    )  # 30min at home = 12.5% overlap

    result = reconcile_day(
        [intent, home], contradiction_overlap_fraction=DEFAULT_CONTRADICTION_OVERLAP_FRACTION
    )

    assert result.dropped_intents == []
    assert len(result.kept_intents) == 1


def test_contradiction_threshold_is_configurable() -> None:
    """Lowering the threshold drops an intent that the default would keep."""
    intent = _calendar_intent(start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=13))
    home = _home_presence(
        start_at=_DAY + timedelta(hours=9), end_at=_DAY + timedelta(hours=9, minutes=30)
    )

    result = reconcile_day([intent, home], contradiction_overlap_fraction=0.1)

    assert len(result.dropped_intents) == 1


# ---------------------------------------------------------------------------
# Duplicate merge: overlapping same-lane candidates from different sources
# ---------------------------------------------------------------------------


def test_duplicate_candidates_merged_with_combined_evidence() -> None:
    """Two sources emit overlapping same-lane candidates for one lived block."""
    workout = _workout(
        source_ref="google_health.measurements:w-1",
        start_at=_DAY + timedelta(hours=7),
        end_at=_DAY + timedelta(hours=7, minutes=45),
        confidence="medium",
        evidence_refs=["hr-event-1"],
    )
    inferred = _inferred_exercise(
        source_ref="chronicler.exercise_inferred:e-1",
        start_at=_DAY + timedelta(hours=7, minutes=10),
        end_at=_DAY + timedelta(hours=8),
        confidence="high",
        evidence_refs=["hr-event-2", "gps-event-1"],
    )

    result = reconcile_day([workout, inferred])

    assert len(result.activities) == 1
    merged = result.activities[0]
    # Combined evidence from both sources, deduplicated, order-preserving.
    assert merged["evidence_refs"] == ["hr-event-1", "hr-event-2", "gps-event-1"]
    # The merged block is counted once, spanning the union window.
    assert merged["canonical_start_at"] == _DAY + timedelta(hours=7)
    assert merged["canonical_end_at"] == _DAY + timedelta(hours=8)


def test_duplicate_merge_bumps_confidence_on_cross_source_corroboration() -> None:
    """Two distinct sources agreeing on one block is itself corroboration."""
    workout = _workout(
        source_ref="google_health.measurements:w-1",
        start_at=_DAY,
        end_at=_DAY + timedelta(minutes=30),
        confidence="low",
    )
    inferred = _inferred_exercise(
        source_ref="chronicler.exercise_inferred:e-1",
        start_at=_DAY + timedelta(minutes=10),
        end_at=_DAY + timedelta(minutes=40),
        confidence="low",
    )

    result = reconcile_day([workout, inferred])

    assert len(result.activities) == 1
    assert result.activities[0]["confidence"] == "medium"


def test_duplicate_merge_confidence_capped_at_high() -> None:
    """Bumping never overshoots the top of the confidence ladder."""
    workout = _workout(
        source_ref="google_health.measurements:w-1",
        start_at=_DAY,
        end_at=_DAY + timedelta(minutes=30),
        confidence="high",
    )
    inferred = _inferred_exercise(
        source_ref="chronicler.exercise_inferred:e-1",
        start_at=_DAY + timedelta(minutes=10),
        end_at=_DAY + timedelta(minutes=40),
        confidence="high",
    )

    result = reconcile_day([workout, inferred])

    assert result.activities[0]["confidence"] == "high"


def test_non_overlapping_same_lane_candidates_not_merged() -> None:
    """Two exercise candidates hours apart are two separate blocks, not one."""
    morning = _workout(
        source_ref="google_health.measurements:w-1", start_at=_DAY, end_at=_DAY + timedelta(hours=1)
    )
    evening = _inferred_exercise(
        source_ref="chronicler.exercise_inferred:e-1",
        start_at=_DAY + timedelta(hours=18),
        end_at=_DAY + timedelta(hours=19),
    )

    result = reconcile_day([morning, evening])

    assert len(result.activities) == 2


def test_touching_but_not_overlapping_candidates_not_merged() -> None:
    """Adjacency (end == next start) is not overlap."""
    first = _workout(
        source_ref="google_health.measurements:w-1", start_at=_DAY, end_at=_DAY + timedelta(hours=1)
    )
    second = _inferred_exercise(
        source_ref="chronicler.exercise_inferred:e-1",
        start_at=_DAY + timedelta(hours=1),
        end_at=_DAY + timedelta(hours=2),
    )

    result = reconcile_day([first, second])

    assert len(result.activities) == 2


def test_different_lane_activities_not_merged() -> None:
    """Overlapping candidates in different lanes are independent activities."""
    workout = _workout(
        source_ref="google_health.measurements:w-1", start_at=_DAY, end_at=_DAY + timedelta(hours=1)
    )
    home = _home_presence(start_at=_DAY, end_at=_DAY + timedelta(hours=1))

    result = reconcile_day([workout, home])

    assert len(result.activities) == 2


def test_unmapped_source_activity_passes_through_unmerged() -> None:
    """An activity-layer episode with no lane mapping is not dropped or merged."""
    unmapped = _episode(
        layer="activity",
        source_name="some_new_connector",
        episode_type="mystery_episode",
        source_ref="some_new_connector:x-1",
        start_at=_DAY,
        end_at=_DAY + timedelta(hours=1),
    )

    result = reconcile_day([unmapped])

    assert result.activities == [unmapped]


def test_evidence_layer_rows_pass_through_untouched() -> None:
    """Evidence-layer rows are neither counted nor reconciled -- pure passthrough."""
    evidence_row = _episode(
        layer="evidence",
        source_name="owntracks.points",
        episode_type="movement_episode",
        source_ref="owntracks.points:pt-1",
        start_at=_DAY,
        end_at=_DAY + timedelta(minutes=5),
    )

    result = reconcile_day([evidence_row])

    assert result.passthrough == [evidence_row]
    assert result.activities == []
    assert result.kept_intents == []


def test_activity_missing_start_time_passes_through_without_crashing() -> None:
    """An activity row with no start time can't be sorted/diffed -- must not
    raise, and must not silently join a merge cluster."""
    starless = _episode(
        layer="activity",
        source_name="some_new_connector",
        episode_type="mystery_episode",
        source_ref="some_new_connector:x-1",
        start_at=None,  # type: ignore[arg-type]
        end_at=None,
    )
    workout = _workout(
        source_ref="google_health.measurements:w-1", start_at=_DAY, end_at=_DAY + timedelta(hours=1)
    )

    result = reconcile_day([starless, workout])

    assert starless in result.passthrough
    assert len(result.activities) == 1


def test_intent_missing_start_time_passes_through_without_crashing() -> None:
    """A calendar intent with no start time can't compute a duration to check
    for contradiction -- must not raise, and must not be silently dropped or
    kept as if reconciled."""
    starless_intent = _episode(
        layer="intent",
        source_name="google_calendar.completed",
        episode_type="scheduled_block",
        source_ref="google_calendar.completed:ev-starless",
        start_at=None,  # type: ignore[arg-type]
        end_at=None,
        title="Mystery Block",
    )
    home = _home_presence(start_at=_DAY, end_at=_DAY + timedelta(hours=2))

    result = reconcile_day([starless_intent, home])

    assert starless_intent in result.passthrough
    assert result.dropped_intents == []
    assert result.kept_intents == []


# ---------------------------------------------------------------------------
# Structural / result-shape sanity
# ---------------------------------------------------------------------------


def test_empty_input_yields_empty_result() -> None:
    result = reconcile_day([])

    assert result == ReconciliationResult()


def test_contradicting_lane_constant_is_rest() -> None:
    """Regression guard: the contradiction rule is scoped to the rest lane."""
    assert CONTRADICTING_LANE == "rest"


def test_reconciliation_is_pure_no_llm_module() -> None:
    """Guardrail: no LLM/interpretation imports anywhere in the module source."""
    import ast
    from pathlib import Path

    import butlers.chronicler.reconciliation as recon_module

    source = Path(recon_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_roots = {"anthropic", "openai", "claude_agent_sdk"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] not in forbidden_roots
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module.split(".")[0] not in forbidden_roots
            assert module != "butlers.chronicler.interpretation"


# ---------------------------------------------------------------------------
# Travel location check and plan provenance (bu-2tsgqb.7)
# ---------------------------------------------------------------------------

_SGT = timezone(timedelta(hours=8))
_CHANGI = (1.3644, 103.9915)
_PEK = (40.08, 116.61)
_CENTRAL_BEIJING = (39.91, 116.40)


def _fix(at: datetime, where: tuple[float, float], **payload: object) -> dict:
    return {
        "source_name": "owntracks.points",
        "event_type": "location",
        "occurred_at": at,
        "payload": {"lat": where[0], "lon": where[1], **payload},
    }


def _travel_intent(title: str, start_at: datetime, end_at: datetime, **payload: object) -> dict:
    return _episode(
        layer="intent",
        source_name="google_calendar.completed",
        episode_type="scheduled_block",
        source_ref="calendar:travel-1",
        start_at=start_at,
        end_at=end_at,
        title=title,
        payload=dict(payload),
    )


_SQ800_START = datetime(2026, 10, 16, 1, 10, tzinfo=_SGT)
_SQ800_END = datetime(2026, 10, 16, 7, 15, tzinfo=_SGT)
_SQ800 = _travel_intent("Flight to Beijing (SQ 800)", _SQ800_START, _SQ800_END)


def _sq800_fixes(**post_payload: object) -> list[dict]:
    """Oct 16 trail: Changi until 01:33 (delayed), Beijing airport from 08:14."""
    return [
        _fix(datetime(2026, 10, 16, 0, 40, tzinfo=_SGT), _CHANGI),
        _fix(datetime(2026, 10, 16, 1, 33, tzinfo=_SGT), _CHANGI),
        _fix(datetime(2026, 10, 16, 8, 14, tzinfo=_SGT), _PEK, **post_payload),
        _fix(datetime(2026, 10, 16, 8, 40, tzinfo=_SGT), _PEK, **post_payload),
    ]


def test_flight_with_origin_then_destination_fixes_is_location_consistent_only() -> None:
    result = reconcile_day([_SQ800], location_fixes=_sq800_fixes())

    [kept] = result.kept_intents
    location = kept["reconciliation"]["location"]
    assert location["verdict"] == "consistent"
    assert 4000 < location["displacement_km"] < 5000
    # The only claim is displacement; nothing marks the flight as taken.
    assert "does not confirm the planned trip" in location["reason"]
    assert kept["reconciliation"]["plan_producer"] == "calendar"
    assert result.dropped_intents == []


def test_butler_route_plan_contradicted_by_same_place_before_and_after() -> None:
    """Oct 21: a butler-written 'SIN -> Aomori' block while the owner stayed in Beijing."""
    start = datetime(2026, 10, 21, 0, 30, tzinfo=_SGT)
    end = datetime(2026, 10, 21, 17, 0, tzinfo=_SGT)
    intent = _travel_intent("BUTLER: Japan Trip: SIN -> Aomori", start, end)
    fixes = [
        _fix(datetime(2026, 10, 20, 22, 0, tzinfo=_SGT), _CENTRAL_BEIJING),
        _fix(datetime(2026, 10, 20, 23, 30, tzinfo=_SGT), _CENTRAL_BEIJING),
        _fix(datetime(2026, 10, 21, 17, 30, tzinfo=_SGT), _CENTRAL_BEIJING),
        _fix(datetime(2026, 10, 21, 18, 5, tzinfo=_SGT), _CENTRAL_BEIJING),
    ]

    result = reconcile_day([intent], location_fixes=fixes)

    assert result.kept_intents == []
    [dropped] = result.dropped_intents
    assert dropped.evidence_kind == "location"
    assert dropped.contradicting_activity["verdict"] == "contradicted"
    assert dropped.intent["reconciliation"]["plan_producer"] == "butler"


@pytest.mark.parametrize(
    "fixes",
    [
        pytest.param([], id="no-location"),
        pytest.param(_sq800_fixes(trail_outlier={"reason": "implied_speed"}), id="outliers"),
        pytest.param(_sq800_fixes(accuracy=5000.0), id="poor-accuracy"),
        pytest.param(_sq800_fixes()[1:3], id="one-fix-each-side"),
        pytest.param(
            # The SGT wall-clock trail stamped as UTC: the same clock times are
            # eight hours later, so no fix sits before the departure.
            [
                {**row, "occurred_at": row["occurred_at"].replace(tzinfo=UTC)}
                for row in _sq800_fixes()
            ],
            id="timezone-shifted",
        ),
        pytest.param(
            [
                {**row, "occurred_at": row["occurred_at"].replace(tzinfo=None)}
                for row in _sq800_fixes()
            ],
            id="naive-timestamps",
        ),
    ],
)
def test_missing_or_unreliable_location_leaves_travel_unknown(fixes: list[dict]) -> None:
    result = reconcile_day([_SQ800], location_fixes=fixes)

    [kept] = result.kept_intents
    assert kept["reconciliation"]["location"]["verdict"] == "unknown"


def test_non_travel_intent_gets_provenance_but_no_location_check() -> None:
    gym = _calendar_intent(
        start_at=datetime(2026, 10, 16, 1, 10, tzinfo=_SGT),
        end_at=datetime(2026, 10, 16, 7, 15, tzinfo=_SGT),
    )

    result = reconcile_day([gym], location_fixes=_sq800_fixes())

    [kept] = result.kept_intents
    assert kept["reconciliation"] == {"plan_producer": "calendar"}


def test_butler_plan_producer_read_from_calendar_flag() -> None:
    intent = _travel_intent("Japan Trip", _SQ800_START, _SQ800_END, butler_generated=True)

    [kept] = reconcile_day([intent]).kept_intents

    assert kept["reconciliation"]["plan_producer"] == "butler"
