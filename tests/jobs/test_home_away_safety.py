"""Home away safety: freezer, away door and sensing blind (bu-jv1moa.87).

away-safety requirement002..007.  The pure rules, the per-source plans and the delivery
rules run anywhere.  The end-to-end runs need real PostgreSQL and run CI-first where Docker is
unavailable.
"""

from __future__ import annotations

import asyncio
import inspect
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from butlers.core.condition_ledger import ConditionTransition
from butlers.jobs import home_away_safety as away
from butlers.jobs.home_away_safety import (
    AWAY_DOOR_SOURCE,
    BLIND_SOURCE,
    FREEZER_SOURCE,
    Door,
    Freezer,
    Premise,
    Reading,
    Streak,
    advance_streak,
    away_premise,
    blind_fingerprint,
    blind_message,
    blind_roles,
    door_fingerprint,
    door_state,
    freezer_fingerprint,
    freezer_message,
    freezer_run,
    late_detection,
    leave_fate,
    run_away_safety,
    sensing_request,
    sensing_state,
)
from butlers.jobs.home_departure import Edge, PresenceRead

_NOW = datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
_FREEZER = Freezer("sensor.garage_freezer", "Garage", "°C", -15.0, 2.0, 30, 60)
_DOOR = Door("binary_sensor.front_door", "Front door", ("off",), 45)
_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available")


def _reading(state: str | None, *, unit: str | None = "°C", age_min: float = 1, changed_min=None):
    changed = None if changed_min is None else _NOW - timedelta(minutes=changed_min)
    return Reading(state, unit, _NOW - timedelta(minutes=age_min), changed)


def _edge(direction: str, minutes_ago: float, *, late=False, retracted=False) -> Edge:
    return Edge(
        id=uuid.uuid4(),
        direction=direction,  # type: ignore[arg-type]
        confirmed_at=_NOW - timedelta(minutes=minutes_ago),
        late=late,
        retracted=retracted,
    )


def _slot(presence: str | None, *, fresh_min: float = 30) -> dict[str, Any]:
    if presence is None:
        return {"state": "unknown", "value": None}
    return {
        "state": "known",
        "value": {"presence": presence, "place_ref": None, "locality_ref": None},
        "fresh_until": (_NOW + timedelta(minutes=fresh_min)).isoformat(),
    }


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lease", "states", "expected"),
    [
        (True, {}, "unconfigured"),
        (False, {"a": "on", "b": "off"}, "unmeasurable"),
        (True, {"a": "on", "b": "off"}, "available"),
        (True, {"a": "on", "b": "unavailable"}, "degraded"),
        (True, {"a": "unknown", "b": None}, "unavailable"),
    ],
)
def test_sensing_state_follows_the_lease_then_readability(lease, states, expected) -> None:
    """away-safety requirement003: the slot states, unmeasurable before unavailable."""
    readings = {e: Reading(s) for e, s in states.items() if s is not None}
    assert (
        sensing_state(lease_healthy=lease, entity_ids=list(states), readings=readings) == expected
    )


@pytest.mark.unit
def test_a_whole_role_lost_is_blind_while_the_slot_is_only_degraded() -> None:
    """away-safety requirement003: leak and smoke dark, freezer still reads."""
    roles = {"leak": ["l1"], "smoke": ["s1", "s2"], "freezer": ["f1"], "gas": []}
    readings = {"l1": Reading("unavailable"), "s1": Reading("unknown"), "f1": Reading("-18")}

    blind = blind_roles(lease_healthy=True, roles=roles, readings=readings)
    slot = sensing_state(lease_healthy=True, entity_ids=["l1", "s1", "s2", "f1"], readings=readings)

    assert blind == {"leak", "smoke"}
    assert slot == "degraded"


@pytest.mark.unit
def test_a_partial_loss_within_a_role_is_not_blind() -> None:
    roles = {"smoke": ["s1", "s2"]}
    readings = {"s1": Reading("unavailable"), "s2": Reading("off")}
    assert blind_roles(lease_healthy=True, roles=roles, readings=readings) == frozenset()


@pytest.mark.unit
def test_a_lost_lease_blinds_every_declared_role_and_no_undeclared_one() -> None:
    roles = {"leak": ["l1"], "co": [], "away_door": ["d1"]}
    assert blind_roles(lease_healthy=False, roles=roles, readings={}) == {"leak", "away_door"}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prior_start", "prior_last", "qualifies", "expected_start"),
    [
        (None, None, True, 0),
        (50, 2, True, 50),
        (50, 4, True, 50),
        (50, 20, True, 0),
        (50, 2, False, None),
    ],
)
def test_streaks_extend_restart_after_a_gap_and_end(
    prior_start, prior_last, qualifies, expected_start
) -> None:
    """away-safety requirement003/004: a gap over two job periods restarts, never bridges."""
    prior = (
        None
        if prior_start is None
        else Streak(_NOW - timedelta(minutes=prior_start), _NOW - timedelta(minutes=prior_last))
    )
    streak = advance_streak(prior, qualifies, _NOW)
    if expected_start is None:
        assert streak is None
    else:
        assert streak == Streak(_NOW - timedelta(minutes=expected_start), _NOW)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "unit", "age_min", "lease", "expected"),
    [
        ("-10", "°C", 1, True, "warm"),
        ("-15", "°C", 1, True, "below"),
        ("-16.5", "°C", 1, True, "below"),
        ("-17", "°C", 1, True, "cold"),
        ("-10", "°C", 61, True, "unknown"),
        ("-10", "°F", 1, True, "unknown"),
        ("unavailable", "°C", 1, True, "unknown"),
        ("nan", "°C", 1, True, "unknown"),
        ("-10", "°C", 1, False, "unknown"),
    ],
)
def test_a_freezer_run_qualifies_only_on_a_known_fresh_declared_unit_reading(
    state, unit, age_min, lease, expected
) -> None:
    """away-safety requirement004: stale, wrong-unit, unreadable or unleased is unknown."""
    verdict, _ = freezer_run(
        _FREEZER, _reading(state, unit=unit, age_min=age_min), lease_healthy=lease, now=_NOW
    )
    assert verdict == expected


@pytest.mark.unit
def test_a_fahrenheit_freezer_is_judged_in_celsius() -> None:
    freezer = Freezer("sensor.chest", "Chest", "°F", -15.0, 2.0, 30, 60)
    warm, warm_c = freezer_run(freezer, _reading("10", unit="°F"), lease_healthy=True, now=_NOW)
    cold, _ = freezer_run(freezer, _reading("0", unit="°F"), lease_healthy=True, now=_NOW)

    assert (warm, round(warm_c or 0, 1)) == ("warm", -12.2)
    assert cold == "cold"


@pytest.mark.unit
def test_the_away_door_counts_from_the_later_of_last_change_and_the_leave() -> None:
    """away-safety requirement005: an item open at departure counts from the leave."""
    leave_at = _NOW - timedelta(minutes=50)
    early = door_state(
        _DOOR, _reading("on", changed_min=600), leave_at=leave_at, lease_healthy=True, now=_NOW
    )
    recent = door_state(
        _DOOR, _reading("on", changed_min=20), leave_at=leave_at, lease_healthy=True, now=_NOW
    )

    assert early == ("due", leave_at)
    assert recent == ("open", _NOW - timedelta(minutes=20))


@pytest.mark.unit
def test_the_away_door_reads_last_changed_never_last_updated() -> None:
    """An attribute update moves last_updated only; without last_changed the item is unknown."""
    leave_at = _NOW - timedelta(minutes=100)
    fresh_update = Reading("on", None, _NOW, None)
    assert door_state(_DOOR, fresh_update, leave_at=leave_at, lease_healthy=True, now=_NOW) == (
        "unknown",
        None,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "lease", "expected"),
    [("off", True, "secure"), ("unavailable", True, "unknown"), ("on", False, "unknown")],
)
def test_an_away_door_is_secure_only_on_its_own_reading(state, lease, expected) -> None:
    result, _ = door_state(
        _DOOR,
        _reading(state, changed_min=100),
        leave_at=_NOW - timedelta(minutes=90),
        lease_healthy=lease,
        now=_NOW,
    )
    assert result == expected


@pytest.mark.unit
def test_away_needs_a_fresh_outside_slot_agreeing_with_a_timely_leave() -> None:
    """away-safety requirement006."""
    leave = _edge("leave", 60)
    premise = away_premise(PresenceRead(_slot("outside"), (leave, _edge("enter", 120))), _NOW)

    assert premise.status == "away"
    assert premise.leave == leave


@pytest.mark.unit
@pytest.mark.parametrize(
    "case", ["stale", "slot_unknown", "disagree", "late_leave", "no_reader", "no_edges"]
)
def test_conflicting_or_missing_presence_is_unknown(case) -> None:
    """away-safety requirement006: conflicting or missing presence is unknown."""
    slot = _slot("outside", fresh_min=-1) if case == "stale" else _slot("outside")
    edges: tuple[Edge, ...] = (_edge("leave", 60),)
    if case == "slot_unknown":
        slot = _slot(None)
    if case == "disagree":
        edges = (_edge("enter", 5), _edge("leave", 60))
    if case == "late_leave":
        edges = (_edge("leave", 60, late=True),)
    if case == "no_edges":
        edges = ()
    read = None if case == "no_reader" else PresenceRead(slot, edges)

    assert away_premise(read, _NOW).status == "unknown"


@pytest.mark.unit
def test_a_retracted_latest_leave_is_skipped_for_the_one_before() -> None:
    older = _edge("leave", 90)
    premise = away_premise(
        PresenceRead(_slot("outside"), (_edge("leave", 10, retracted=True), older)), _NOW
    )
    assert premise.leave == older


@pytest.mark.unit
def test_a_leave_ends_by_return_retraction_or_stays_unknown() -> None:
    """away-safety requirement005/006: return, retraction, or nothing concluded."""
    leave = _edge("leave", 90)
    retracted = Edge(leave.id, "leave", leave.confirmed_at, False, True)
    enter = _edge("enter", 10)

    home = away_premise(PresenceRead(_slot("inside"), (enter, leave)), _NOW)
    pulled = away_premise(PresenceRead(_slot("inside"), (retracted, _edge("enter", 300))), _NOW)
    unknown = away_premise(PresenceRead(_slot("outside", fresh_min=-1), (enter, leave)), _NOW)
    current = away_premise(PresenceRead(_slot("outside"), (leave,)), _NOW)

    assert leave_fate(home, leave.id, leave.confirmed_at) == "returned"
    assert leave_fate(pulled, leave.id, leave.confirmed_at) == "retracted"
    assert leave_fate(unknown, leave.id, leave.confirmed_at) == "unknown"
    assert leave_fate(current, leave.id, leave.confirmed_at) == "current"


@pytest.mark.unit
def test_a_late_enter_is_no_return() -> None:
    leave = _edge("leave", 90)
    premise = Premise("inside", edges=(_edge("enter", 10, late=True), leave))
    assert leave_fate(premise, leave.id, leave.confirmed_at) == "unknown"


@pytest.mark.unit
def test_messages_use_owner_labels_fixed_words_and_numbers_only() -> None:
    since = _NOW - timedelta(minutes=42)
    freezer = Freezer("sensor.f", "*Garage* [x]", "°C", -15.0, 2.0, 30, 60)

    assert (
        freezer_message(freezer, "warm", -9.04, since, _NOW)
        == "Freezer Garage x: -9.0 °C, above -15.0 °C for 42 min"
    )
    assert freezer_message(freezer, "unknown", None, since, _NOW) == (
        "Freezer Garage x: sensor not reporting"
    )
    assert away.door_message(_DOOR, "on", since, _NOW) == (
        "Front door still on 42 min into your absence"
    )


@pytest.mark.unit
def test_the_blind_message_names_roles_in_fixed_words_and_owner_local_time() -> None:
    message = blind_message({"away_door", "co", "leak"}, _NOW, ZoneInfo("Asia/Singapore"))
    assert message == "Home cannot see leak, CO, doors since 16:00; those alerts cannot reach you"


@pytest.mark.unit
def test_a_late_open_records_its_lag_past_the_deadline() -> None:
    dwell = timedelta(minutes=30)
    assert late_detection(_NOW - timedelta(minutes=33), dwell, _NOW) is None
    assert late_detection(_NOW - timedelta(minutes=40), dwell, _NOW) == {"lag_seconds": 600}


@pytest.mark.unit
def test_away_conditions_are_per_absence_and_the_freezer_per_entity() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    assert door_fingerprint("binary_sensor.a", first) != door_fingerprint("binary_sensor.a", second)
    assert blind_fingerprint(first) != blind_fingerprint(second)
    assert freezer_fingerprint("sensor.a") == freezer_fingerprint("sensor.a")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "slot_state", "value"),
    [
        ("degraded", "known", "degraded"),
        ("unmeasurable", "unmeasurable", None),
        ("unconfigured", "unconfigured", None),
    ],
)
def test_the_sensing_request_is_a_valid_closed_perception_write(state, slot_state, value) -> None:
    body = sensing_request(state, _NOW, 3).body
    assert (body["kind"], body["source"], body["subject"]) == (
        "home.sensing",
        "home.sensing",
        str(away.SENSING_GROUP),
    )
    assert (body["state"], body["typed_value_or_ref"], body["expected_generation"]) == (
        slot_state,
        value,
        3,
    )


@pytest.mark.unit
def test_the_module_has_no_actuation_model_or_prepared_action_path() -> None:
    """away-safety requirement007: no model call, no HA service call, no prepared action."""
    source = inspect.getsource(away)
    for forbidden in ("call_service", "ha_call", "spawn", "anthropic", "prepared_action"):
        assert forbidden not in source
    assert "friendly_name" not in source.replace("``friendly_name``", "")


@pytest.mark.unit
async def test_dark_switch_reads_and_writes_nothing() -> None:
    pool = MagicMock()
    reader = AsyncMock()

    result = await run_away_safety(pool, None, read_presence=reader, now=_NOW)

    assert result == {"status": "dark"}
    reader.assert_not_awaited()
    assert pool.method_calls == []


# ---------------------------------------------------------------------------
# Plans: what each producer observes, carries and resolves
# ---------------------------------------------------------------------------


def _run(**overrides: Any) -> away._Run:
    base: dict[str, Any] = {
        "now": _NOW,
        "lease_healthy": True,
        "readings": {},
        "freezers": [],
        "doors": [],
        "safety": {},
        "blind_after": None,
        "streaks": {},
        "actives": {FREEZER_SOURCE: {}, AWAY_DOOR_SOURCE: {}, BLIND_SOURCE: {}},
        "premise": Premise("unknown"),
    }
    base.update(overrides)
    return away._Run(**base)


def _streak(minutes_ago: float, last_ago: float = 2) -> Streak:
    return Streak(_NOW - timedelta(minutes=minutes_ago), _NOW - timedelta(minutes=last_ago))


def _active(**metadata: Any) -> dict[str, Any]:
    return {"metadata": metadata, "first_detected_at": _NOW - timedelta(hours=2)}


def _confirm_only(plan: away._Plan) -> set[str]:
    return {o.fingerprint for o in plan.observations if o.confirm_only}


def _opened(plan: away._Plan) -> set[str]:
    return {o.fingerprint for o in plan.observations if not o.confirm_only}


@pytest.mark.unit
def test_a_held_warm_freezer_opens_once_its_streak_reaches_the_dwell() -> None:
    fp = freezer_fingerprint(_FREEZER.entity_id)
    warm = {_FREEZER.entity_id: _reading("-10")}
    early = _run(
        freezers=[_FREEZER], readings=warm, streaks={("freezer", _FREEZER.entity_id): _streak(20)}
    )
    due = _run(
        freezers=[_FREEZER], readings=warm, streaks={("freezer", _FREEZER.entity_id): _streak(30)}
    )

    early_plan = away._plan_freezers(early)
    due_plan = away._plan_freezers(due)

    assert _opened(early_plan) == set()
    assert _opened(due_plan) == {fp}
    assert due_plan.metadata[fp]["detected_since"] == (_NOW - timedelta(minutes=30)).isoformat()
    assert due.new_streaks[("freezer", _FREEZER.entity_id)].start == _NOW - timedelta(minutes=30)


@pytest.mark.unit
def test_a_gap_in_runs_delays_the_freezer_open() -> None:
    """away-safety requirement004 scenario: streak 50 min old, 20 min without a run."""
    run = _run(
        freezers=[_FREEZER],
        readings={_FREEZER.entity_id: _reading("-10")},
        streaks={("freezer", _FREEZER.entity_id): _streak(50, last_ago=20)},
    )
    plan = away._plan_freezers(run)
    assert _opened(plan) == set()
    assert run.new_streaks[("freezer", _FREEZER.entity_id)] == Streak(_NOW, _NOW)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "lease", "resolved", "carried", "streak_kept"),
    [
        ("-17", True, "temperature_recovered", False, False),
        ("-16", True, None, True, False),
        ("unavailable", True, None, True, False),
        ("-10", False, None, True, False),
        ("-10", True, None, True, True),
    ],
)
def test_an_open_freezer_resolves_only_past_the_hysteresis(
    state, lease, resolved, carried, streak_kept
) -> None:
    fp = freezer_fingerprint(_FREEZER.entity_id)
    run = _run(
        lease_healthy=lease,
        freezers=[_FREEZER],
        readings={_FREEZER.entity_id: _reading(state)},
        streaks={("freezer", _FREEZER.entity_id): _streak(90)},
        actives={
            FREEZER_SOURCE: {fp: _active(entity_id=_FREEZER.entity_id)},
            AWAY_DOOR_SOURCE: {},
            BLIND_SOURCE: {},
        },
    )
    plan = away._plan_freezers(run)
    assert plan.resolutions.get(fp) == resolved
    assert (fp in _confirm_only(plan)) is carried
    assert (("freezer", _FREEZER.entity_id) in run.new_streaks) is streak_kept


@pytest.mark.unit
@pytest.mark.parametrize(("lease", "expected"), [(True, "watch_removed"), (False, None)])
def test_a_removed_freezer_resolves_only_on_a_healthy_run(lease, expected) -> None:
    fp = freezer_fingerprint("sensor.old")
    run = _run(
        lease_healthy=lease,
        actives={
            FREEZER_SOURCE: {fp: _active(entity_id="sensor.old")},
            AWAY_DOOR_SOURCE: {},
            BLIND_SOURCE: {},
        },
    )
    assert away._plan_freezers(run).resolutions.get(fp) == expected


def _away(leave: Edge) -> Premise:
    return Premise("away", leave=leave, edges=(leave,))


@pytest.mark.unit
def test_an_armed_door_opens_only_while_away_and_past_its_duration() -> None:
    leave = _edge("leave", 50)
    readings = {_DOOR.entity_id: _reading("on", changed_min=600)}
    unarmed = Door(_DOOR.entity_id, "Front door", ("off",), None)

    due = away._plan_doors(_run(doors=[_DOOR], readings=readings, premise=_away(leave)))
    unknown = away._plan_doors(_run(doors=[_DOOR], readings=readings, premise=Premise("unknown")))
    not_armed = away._plan_doors(_run(doors=[unarmed], readings=readings, premise=_away(leave)))

    fp = door_fingerprint(_DOOR.entity_id, leave.id)
    assert _opened(due) == {fp}
    assert due.metadata[fp]["leave_edge_id"] == str(leave.id)
    assert due.leaves[fp] == (leave.id, leave.confirmed_at)
    assert _opened(unknown) == set()
    assert _opened(not_armed) == set()


def _door_active(leave: Edge) -> dict[str, dict[str, Any]]:
    fp = door_fingerprint(_DOOR.entity_id, leave.id)
    return {
        fp: _active(
            entity_id=_DOOR.entity_id,
            leave_edge_id=str(leave.id),
            leave_confirmed_at=leave.confirmed_at.isoformat(),
        )
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("premise_case", "state", "expected"),
    [
        ("returned", "on", "owner_returned"),
        ("retracted", "on", "premise_retracted"),
        ("unknown", "off", "secured"),
        ("current", "off", "secured"),
        ("unknown", "on", None),
    ],
)
def test_an_open_away_door_resolves_by_its_reading_return_or_retraction(
    premise_case, state, expected
) -> None:
    """away-safety requirement005/006: unknown presence resolves only via a secure reading."""
    leave = _edge("leave", 90)
    premises = {
        "returned": Premise("inside", edges=(_edge("enter", 5), leave)),
        "retracted": Premise(
            "inside", edges=(Edge(leave.id, "leave", leave.confirmed_at, False, True),)
        ),
        "unknown": Premise("unknown"),
        "current": _away(leave),
    }
    run = _run(
        doors=[_DOOR],
        readings={_DOOR.entity_id: _reading(state, changed_min=600)},
        premise=premises[premise_case],
        actives={FREEZER_SOURCE: {}, AWAY_DOOR_SOURCE: _door_active(leave), BLIND_SOURCE: {}},
    )
    plan = away._plan_doors(run)
    fp = door_fingerprint(_DOOR.entity_id, leave.id)
    assert plan.resolutions.get(fp) == expected
    if expected is None:
        assert fp in _confirm_only(plan)
        assert fp not in plan.messages, "an unknown premise holds the send"


@pytest.mark.unit
@pytest.mark.parametrize("edit", ["removed", "disarmed", "longer"])
def test_an_edit_the_open_door_no_longer_meets_resolves_watch_removed(edit) -> None:
    leave = _edge("leave", 90)
    doors = {
        "removed": [],
        "disarmed": [Door(_DOOR.entity_id, "Front door", ("off",), None)],
        "longer": [Door(_DOOR.entity_id, "Front door", ("off",), 120)],
    }[edit]
    run = _run(
        doors=doors,
        readings={_DOOR.entity_id: _reading("on", changed_min=600)},
        premise=_away(leave),
        actives={FREEZER_SOURCE: {}, AWAY_DOOR_SOURCE: _door_active(leave), BLIND_SOURCE: {}},
    )
    plan = away._plan_doors(run)
    assert plan.resolutions == {door_fingerprint(_DOOR.entity_id, leave.id): "watch_removed"}
    assert _opened(plan) == set()


@pytest.mark.unit
def test_an_open_away_door_is_carried_with_a_message_while_away() -> None:
    leave = _edge("leave", 90)
    run = _run(
        doors=[_DOOR],
        readings={_DOOR.entity_id: _reading("on", changed_min=600)},
        premise=_away(leave),
        actives={FREEZER_SOURCE: {}, AWAY_DOOR_SOURCE: _door_active(leave), BLIND_SOURCE: {}},
    )
    plan = away._plan_doors(run)
    fp = door_fingerprint(_DOOR.entity_id, leave.id)
    assert _confirm_only(plan) == {fp}
    assert _opened(plan) == set(), "the open absence is not opened twice"
    assert plan.messages[fp] == "Front door still on 90 min into your absence"


def _blind_run(**overrides: Any) -> away._Run:
    leak, smoke = "binary_sensor.leak", "binary_sensor.smoke"
    base: dict[str, Any] = {
        "safety": {"leak": [leak], "smoke": [smoke]},
        "freezers": [_FREEZER],
        "readings": {
            leak: Reading("unavailable"),
            smoke: Reading("unavailable"),
            _FREEZER.entity_id: _reading("-18"),
        },
        "blind_after": 15,
        "streaks": {("blind", "leak"): _streak(20), ("blind", "smoke"): _streak(16)},
    }
    base.update(overrides)
    return _run(**base)


@pytest.mark.unit
def test_whole_roles_dark_while_away_open_one_blind_condition_naming_them() -> None:
    """away-safety requirement003 scenario: leak and smoke dark, the freezer reads."""
    leave = _edge("leave", 60)
    run = _blind_run(premise=_away(leave))
    plan = away._plan_blind(run, configured=True, tz=ZoneInfo("UTC"))

    fp = blind_fingerprint(leave.id)
    assert _opened(plan) == {fp}
    assert plan.metadata[fp]["roles"] == ["leak", "smoke"]
    assert plan.messages[fp] == (
        "Home cannot see leak, smoke since 07:40; those alerts cannot reach you"
    )
    assert "freezer" not in plan.messages[fp]


@pytest.mark.unit
@pytest.mark.parametrize("case", ["unconfigured", "no_settings", "not_away", "short"])
def test_blindness_opens_nothing_without_configuration_presence_or_dwell(case) -> None:
    leave = _edge("leave", 60)
    run = _blind_run(
        premise=Premise("unknown") if case == "not_away" else _away(leave),
        blind_after=None if case == "no_settings" else (30 if case == "short" else 15),
    )
    plan = away._plan_blind(run, configured=case != "unconfigured", tz=ZoneInfo("UTC"))
    assert _opened(plan) == set()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("leak_state", "premise_case", "expected"),
    [
        ("off", "current", "sight_restored"),
        ("unavailable", "returned", "owner_returned"),
        ("unavailable", "unknown", None),
    ],
)
def test_an_open_blind_condition_resolves_on_sight_or_return(
    leak_state, premise_case, expected
) -> None:
    leave = _edge("leave", 60)
    fp = blind_fingerprint(leave.id)
    active = {
        fp: _active(
            leave_edge_id=str(leave.id),
            leave_confirmed_at=leave.confirmed_at.isoformat(),
            roles=["leak"],
        )
    }
    premise = {
        "current": _away(leave),
        "returned": Premise("inside", edges=(_edge("enter", 5), leave)),
        "unknown": Premise("unknown"),
    }[premise_case]
    run = _blind_run(
        safety={"leak": ["binary_sensor.leak"]},
        readings={"binary_sensor.leak": Reading(leak_state), _FREEZER.entity_id: _reading("-18")},
        premise=premise,
        actives={FREEZER_SOURCE: {}, AWAY_DOOR_SOURCE: {}, BLIND_SOURCE: active},
    )
    plan = away._plan_blind(run, configured=True, tz=ZoneInfo("UTC"))
    assert plan.resolutions.get(fp) == expected
    if expected is None:
        assert fp in _confirm_only(plan)


@pytest.mark.unit
def test_blindness_never_resolves_another_condition() -> None:
    """away-safety requirement003: a blind run resolves nothing by omission."""
    fp = freezer_fingerprint(_FREEZER.entity_id)
    run = _run(
        lease_healthy=False,
        freezers=[_FREEZER],
        actives={
            FREEZER_SOURCE: {fp: _active(entity_id=_FREEZER.entity_id)},
            AWAY_DOOR_SOURCE: {},
            BLIND_SOURCE: {},
        },
    )
    plan = away._plan_freezers(run)
    assert plan.resolutions == {}
    assert plan.messages[fp] == "Freezer Garage: sensor not reporting"


# ---------------------------------------------------------------------------
# Delivery: markers only on "sent", held rungs, the pre-send premise re-read
# ---------------------------------------------------------------------------


def _transition(fp: str, kind: str) -> ConditionTransition:
    return ConditionTransition(
        condition_id=uuid.uuid4(),
        source=FREEZER_SOURCE,
        fingerprint=fp,
        episode=1,
        state="open",
        transition=kind,  # type: ignore[arg-type]
        escalation_level="L0",
        next_reescalate_at=None,
    )


class _Ledger:
    """Records reconcile/resolve calls; the first reconcile returns the scripted transitions."""

    def __init__(self, transitions: list[ConditionTransition]) -> None:
        self.transitions = transitions
        self.marker_writes: list[dict[str, Any]] = []
        self.resolved: list[tuple[str, str]] = []

    async def reconcile(self, pool, *, source, observations, **kwargs):  # noqa: ANN001
        if self.transitions is not None:
            result, self.transitions = self.transitions, None
            return result
        self.marker_writes.extend(o.metadata for o in observations)
        return []

    async def resolve(self, pool, *, source, fingerprint, resolution_metadata):  # noqa: ANN001
        self.resolved.append((fingerprint, resolution_metadata["resolution_reason"]))
        return object()


def _ledger(monkeypatch: pytest.MonkeyPatch, *transitions: ConditionTransition) -> _Ledger:
    ledger = _Ledger(list(transitions))
    monkeypatch.setattr(away, "reconcile_snapshot", ledger.reconcile)
    monkeypatch.setattr(away, "resolve_condition", ledger.resolve)
    return ledger


def _plan(fp: str, leave: Edge | None = None) -> away._Plan:
    plan = away._Plan()
    plan.observe(fp, "message", {"entity_id": "x"})
    if leave is not None:
        plan.leaves[fp] = (leave.id, leave.confirmed_at)
    return plan


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outcome", "markers"),
    [
        ("sent", [{"entity_id": "x", "notified_at": _NOW.isoformat()}]),
        ("suppressed", []),
        ("failed", []),
    ],
)
async def test_an_opened_condition_is_marked_delivered_only_when_sent(
    monkeypatch, outcome, markers
) -> None:
    """away-safety requirement007: a suppressed first send leaves no delivery marker."""
    ledger = _ledger(monkeypatch, _transition("fp", "opened"))
    notify = AsyncMock(return_value=outcome)

    counts = await away._apply(
        MagicMock(), FREEZER_SOURCE, _plan("fp"), {}, now=_NOW, notify=notify
    )

    notify.assert_awaited_once_with("message")
    assert ledger.marker_writes == markers
    assert counts[outcome] == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("notified", "age_s", "sends"),
    [(None, 121, True), (None, 60, False), ("2026-11-14T07:00:00+00:00", 3600, False)],
)
async def test_an_undelivered_condition_is_retried_after_two_minutes(
    monkeypatch, notified, age_s, sends
) -> None:
    _ledger(monkeypatch, _transition("fp", "confirmed"))
    notify = AsyncMock(return_value="sent")
    prior = {
        "metadata": {"notified_at": notified} if notified else {},
        "first_detected_at": _NOW - timedelta(seconds=age_s),
    }

    await away._apply(
        MagicMock(), FREEZER_SOURCE, _plan("fp"), {"fp": prior}, now=_NOW, notify=notify
    )

    assert notify.await_count == int(sends)


@pytest.mark.unit
@pytest.mark.parametrize("outcome", ["sent", "suppressed"])
async def test_a_rung_is_recorded_when_due_and_marked_only_when_sent(monkeypatch, outcome) -> None:
    """away-safety requirement007: a rung inside quiet hours is held, not lost."""
    due = _NOW - timedelta(minutes=1)
    ledger = _ledger(monkeypatch, _transition("fp", "escalation_due"))
    prior = {
        "metadata": {"notified_at": "2026-11-13T07:00:00+00:00"},
        "first_detected_at": _NOW - timedelta(days=1),
        "next_reescalate_at": due,
    }

    await away._apply(
        MagicMock(),
        FREEZER_SOURCE,
        _plan("fp"),
        {"fp": prior},
        now=_NOW,
        notify=AsyncMock(return_value=outcome),
    )

    (written,) = ledger.marker_writes
    assert written["rung_due_at"] == due.isoformat()
    assert ("rung_notified_at" in written) is (outcome == "sent")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("rung_notified", "sends"),
    [(None, True), ("2026-11-14T06:00:00+00:00", True), ("2026-11-14T07:30:00+00:00", False)],
)
async def test_a_held_rung_is_delivered_once_after_the_window(
    monkeypatch, rung_notified, sends
) -> None:
    ledger = _ledger(monkeypatch, _transition("fp", "confirmed"))
    notify = AsyncMock(return_value="sent")
    metadata = {
        "notified_at": "2026-11-13T07:00:00+00:00",
        "rung_due_at": "2026-11-14T07:00:00+00:00",
    }
    if rung_notified:
        metadata["rung_notified_at"] = rung_notified
    prior = {"metadata": metadata, "first_detected_at": _NOW - timedelta(days=1)}

    await away._apply(
        MagicMock(), FREEZER_SOURCE, _plan("fp"), {"fp": prior}, now=_NOW, notify=notify
    )

    assert notify.await_count == int(sends)
    if sends:
        assert ledger.marker_writes[-1]["rung_notified_at"] == _NOW.isoformat()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fate", "resolved", "sends"),
    [
        ("current", [], True),
        ("returned", [("fp", "owner_returned")], False),
        ("retracted", [("fp", "premise_retracted")], False),
        ("unknown", [], False),
    ],
)
async def test_an_away_send_re_reads_the_premise_first(monkeypatch, fate, resolved, sends) -> None:
    """away-safety requirement007: return resolves, unknown holds, only away sends."""
    ledger = _ledger(monkeypatch, _transition("fp", "opened"))
    notify = AsyncMock(return_value="sent")
    recheck = AsyncMock(return_value=fate)
    leave = _edge("leave", 60)

    counts = await away._apply(
        MagicMock(),
        AWAY_DOOR_SOURCE,
        _plan("fp", leave),
        {},
        now=_NOW,
        notify=notify,
        recheck=recheck,
    )

    recheck.assert_awaited_once_with(leave.id, leave.confirmed_at)
    assert notify.await_count == int(sends)
    assert ledger.resolved == resolved
    assert counts["held"] == int(fate == "unknown")


@pytest.mark.unit
async def test_a_raising_notify_counts_as_failed_and_leaves_no_marker(monkeypatch) -> None:
    ledger = _ledger(monkeypatch, _transition("fp", "opened"))
    counts = await away._apply(
        MagicMock(),
        FREEZER_SOURCE,
        _plan("fp"),
        {},
        now=_NOW,
        notify=AsyncMock(side_effect=RuntimeError("boom")),
    )
    assert counts["failed"] == 1
    assert ledger.marker_writes == []


# ---------------------------------------------------------------------------
# Real Postgres
# ---------------------------------------------------------------------------


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(away, "HOME_AWAY_SAFETY_LIVE", True)


@pytest.fixture
def no_ha_posts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Any HTTP POST fails the test: the producers never actuate."""
    posts: list[str] = []

    async def _post(self, url, *args, **kwargs):  # noqa: ANN001
        posts.append(str(url))
        raise AssertionError(f"unexpected HTTP POST to {url}")

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    return posts


async def _home_pool(db) -> asyncpg.Pool:
    return await db("core", "home", schemas={"home": "home"}, schema="home")


async def _seed_freezer(pool: asyncpg.Pool, state: str) -> None:
    await pool.execute(
        "INSERT INTO temperature_watch (entity_id, role, owner_label, unit, max_c, hysteresis_c, "
        "dwell_minutes, max_reading_age_minutes) VALUES ($1, 'freezer', 'Garage', '°C', -15, 2, "
        "30, 60)",
        _FREEZER.entity_id,
    )
    await pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at) "
        "VALUES ('home_assistant', 'healthy', now())"
    )
    await _set_reading(pool, state)


async def _set_reading(pool: asyncpg.Pool, state: str) -> None:
    await pool.execute(
        "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated, last_changed) "
        'VALUES ($1, $2, \'{"unit_of_measurement": "°C", "friendly_name": "IGNORE"}\'::jsonb, '
        "now(), now()) ON CONFLICT (entity_id) DO UPDATE SET state = EXCLUDED.state, "
        "last_updated = now()",
        _FREEZER.entity_id,
        state,
    )


async def _freezer_condition(pool: asyncpg.Pool) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT state, metadata FROM public.owner_conditions WHERE source = $1 "
        "ORDER BY episode DESC LIMIT 1",
        FREEZER_SOURCE,
    )


def _deps(outcome: str = "sent") -> dict[str, Any]:
    return {
        "read_presence": AsyncMock(return_value=None),
        "sensing_writer": AsyncMock(return_value="unconfigured"),
        "notify_fn": AsyncMock(return_value=outcome),
    }


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_warm_freezer_opens_after_its_dwell_and_resolves_past_hysteresis(
    db, live, no_ha_posts
) -> None:
    pool = await _home_pool(db)
    await _seed_freezer(pool, "-9")
    deps = _deps()

    first = await run_away_safety(pool, None, **deps)
    await pool.execute(
        "UPDATE away_safety_streaks SET streak_start = now() - interval '31 minutes'"
    )
    second = await run_away_safety(pool, None, **deps)

    assert first[FREEZER_SOURCE].get("opened", 0) == 0
    assert second[FREEZER_SOURCE]["opened"] == 1
    (message,) = deps["notify_fn"].await_args.args
    assert message.startswith("Freezer Garage: -9.0 °C, above -15.0 °C for 3")
    assert "IGNORE" not in message
    row = await _freezer_condition(pool)
    assert row is not None and row["state"] in ("open", "aging")

    await _set_reading(pool, "-16")
    await run_away_safety(pool, None, **deps)
    row = await _freezer_condition(pool)
    assert row is not None and row["state"] in ("open", "aging")

    await _set_reading(pool, "-17.5")
    await run_away_safety(pool, None, **deps)
    row = await _freezer_condition(pool)
    assert row is not None and row["state"] == "resolved"
    assert no_ha_posts == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_a_suppressed_open_is_sent_by_the_first_run_after_the_window(db, live) -> None:
    pool = await _home_pool(db)
    await _seed_freezer(pool, "-9")
    await run_away_safety(pool, None, **_deps())
    await pool.execute(
        "UPDATE away_safety_streaks SET streak_start = now() - interval '31 minutes'"
    )

    await run_away_safety(pool, None, **_deps("suppressed"))
    row = await _freezer_condition(pool)
    assert row is not None and "notified_at" not in away._as_dict(row["metadata"])

    await pool.execute(
        "UPDATE public.owner_conditions SET first_detected_at = now() - interval '3 minutes'"
    )
    retry = _deps("sent")
    await run_away_safety(pool, None, **retry)
    again = _deps("sent")
    await run_away_safety(pool, None, **again)

    retry["notify_fn"].assert_awaited_once()
    again["notify_fn"].assert_not_awaited()
    row = await _freezer_condition(pool)
    assert row is not None and away._as_dict(row["metadata"]).get("notified_at")


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_unhealthy_lease_ends_the_streak_and_resolves_nothing(db, live) -> None:
    pool = await _home_pool(db)
    await _seed_freezer(pool, "-9")
    await run_away_safety(pool, None, **_deps())
    await pool.execute(
        "UPDATE away_safety_streaks SET streak_start = now() - interval '31 minutes'"
    )
    await run_away_safety(pool, None, **_deps())
    await pool.execute(
        "UPDATE ha_source_health SET last_success_at = now() - interval '30 minutes'"
    )

    result = await run_away_safety(pool, None, **_deps())

    assert result["status"] == "source_unmeasurable"
    assert (
        await pool.fetchval("SELECT count(*) FROM away_safety_streaks WHERE kind = 'freezer'") == 0
    )
    row = await _freezer_condition(pool)
    assert row is not None and row["state"] in ("open", "aging")


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_an_away_door_opens_while_away_and_resolves_on_return(db, live, no_ha_posts) -> None:
    pool = await _home_pool(db)
    await pool.execute(
        "INSERT INTO departure_watch (entity_id, owner_label, secure_states, "
        "away_alert_after_minutes) VALUES ($1, 'Front door', ARRAY['off'], 30)",
        _DOOR.entity_id,
    )
    await pool.execute(
        "INSERT INTO ha_source_health (source, status, last_success_at) "
        "VALUES ('home_assistant', 'healthy', now())"
    )
    await pool.execute(
        "INSERT INTO ha_entity_snapshot (entity_id, state, attributes, last_updated, last_changed) "
        "VALUES ($1, 'on', '{}'::jsonb, now(), now() - interval '2 hours')",
        _DOOR.entity_id,
    )
    now = datetime.now(UTC)
    leave = Edge(uuid.uuid4(), "leave", now - timedelta(minutes=40), False, False)
    away_read = PresenceRead(
        {
            "state": "known",
            "value": {"presence": "outside"},
            "fresh_until": (now + timedelta(minutes=30)).isoformat(),
        },
        (leave,),
    )
    deps = _deps()
    deps["read_presence"] = AsyncMock(return_value=away_read)

    opened = await run_away_safety(pool, None, **deps)

    assert opened[AWAY_DOOR_SOURCE]["opened"] == 1
    (message,) = deps["notify_fn"].await_args.args
    assert message.startswith("Front door still on 4")
    assert message.endswith("min into your absence")

    enter = Edge(uuid.uuid4(), "enter", now - timedelta(minutes=1), False, False)
    home_read = PresenceRead(
        {
            "state": "known",
            "value": {"presence": "inside"},
            "fresh_until": (now + timedelta(minutes=30)).isoformat(),
        },
        (enter, leave),
    )
    returned = _deps()
    returned["read_presence"] = AsyncMock(return_value=home_read)
    result = await run_away_safety(pool, None, **returned)

    assert result[AWAY_DOOR_SOURCE]["resolved"] == 1
    returned["notify_fn"].assert_not_awaited()
    reason = await pool.fetchval(
        "SELECT metadata ->> 'resolution_reason' FROM public.owner_conditions WHERE source = $1",
        AWAY_DOOR_SOURCE,
    )
    assert reason == "owner_returned"
    assert no_ha_posts == []


@_docker
@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_runs_serialize_and_the_second_is_busy(db, live) -> None:
    """away-safety requirement007 scenario: concurrent runs send once."""
    pool = await _home_pool(db)
    await _seed_freezer(pool, "-9")
    gate = asyncio.Event()
    release = asyncio.Event()

    async def slow_writer(state, now):  # noqa: ANN001
        gate.set()
        await release.wait()
        return "unconfigured"

    first_deps = _deps()
    first_deps["sensing_writer"] = slow_writer
    first = asyncio.create_task(run_away_safety(pool, None, **first_deps))
    await gate.wait()
    second = await run_away_safety(pool, None, **_deps())
    release.set()

    assert second == {"status": "busy"}
    assert (await first)["status"] == "ok"
