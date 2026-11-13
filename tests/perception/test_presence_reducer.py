"""The pure owner.place presence reducer: confirmation, veto, unknown and retraction rules."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from butlers.perception.presence import (
    CONFIRM_AFTER,
    HEARTBEAT,
    OBSERVATION_FRESH,
    RETRACT_WINDOW,
    Observation,
    PresenceState,
    gps_outside,
    observation_key,
    reduce,
)

HOME = UUID("11111111-1111-4111-8111-111111111111")
T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def obs(at, side, key=None, *, ssid=False, evidence="home_assistant"):
    return Observation(at, side, HOME, evidence, key or observation_key(side, at.isoformat()), ssid)


def run(state, observations, now):
    return reduce(state, observations, now, place_ref=HOME)


def inside_state():
    state, decisions = run(PresenceState(), [obs(T0, "inside")], T0)
    assert decisions == []
    state, decisions = run(state, [], T0 + CONFIRM_AFTER)
    return state, decisions


def test_gps_qualification_boundaries():
    assert gps_outside(distance_m=251, accuracy_m=100, radius_m=150) is True
    assert gps_outside(distance_m=250, accuracy_m=100, radius_m=150) is False  # not strictly beyond
    assert gps_outside(distance_m=300, accuracy_m=101, radius_m=150) is False  # too inaccurate
    assert gps_outside(distance_m=201, accuracy_m=10, radius_m=150) is True  # 50 m margin floor
    assert gps_outside(distance_m=200, accuracy_m=10, radius_m=150) is False


def test_unknown_prior_is_established_with_a_reset_never_an_edge():
    _, decisions = inside_state()
    (decision,) = decisions
    assert decision.action == "establish" and decision.reset_first and decision.side == "inside"
    assert decision.at == T0


def test_leave_needs_prior_known_inside_and_confirms_after_quiet_period():
    state, _ = inside_state()
    leave_at = T0 + timedelta(minutes=10)
    state, decisions = run(state, [obs(leave_at, "outside")], leave_at)
    assert decisions == [] and state.candidate is not None
    state, decisions = run(state, [], leave_at + CONFIRM_AFTER - timedelta(seconds=1))
    assert decisions == []
    now = leave_at + CONFIRM_AFTER
    state, (decision,) = run(state, [], now)
    assert (decision.action, decision.side, decision.at, decision.confirmed_at) == (
        "leave",
        "outside",
        leave_at,
        now,
    )
    assert not decision.reset_first and decision.retracts is None
    assert state.side == "outside" and state.leave_event == decision.event_id


def test_two_distinct_fixes_confirm_before_the_quiet_period():
    state, _ = inside_state()
    first = T0 + timedelta(minutes=10)
    second = first + timedelta(seconds=40)
    state, decisions = run(state, [obs(first, "outside", "a"), obs(second, "outside", "b")], second)
    (decision,) = decisions
    assert decision.action == "leave" and decision.at == first and decision.observed_at == second


def test_a_repeated_fix_is_not_a_second_distinct_fix():
    state, _ = inside_state()
    first = T0 + timedelta(minutes=10)
    state, decisions = run(state, [obs(first, "outside", "a")], first)
    state, decisions = run(state, [obs(first, "outside", "a")], first + timedelta(seconds=30))
    assert decisions == []


def test_fresh_inside_observation_cancels_a_pending_leave():
    state, _ = inside_state()
    first = T0 + timedelta(minutes=10)
    state, _ = run(state, [obs(first, "outside")], first)
    back = first + timedelta(minutes=1)
    state, decisions = run(state, [obs(back, "inside")], back + CONFIRM_AFTER)
    assert {d.action for d in decisions} <= {"heartbeat"}
    assert state.side == "inside" and state.candidate is None


def test_home_ssid_vetoes_a_gps_outside_fix():
    state, _ = inside_state()
    first = T0 + timedelta(minutes=10)
    fixes = [
        obs(first, "outside", "a", ssid=True),
        obs(first + timedelta(seconds=30), "outside", "b", ssid=True),
    ]
    state, decisions = run(state, fixes, first + timedelta(minutes=5))
    assert {d.action for d in decisions} <= {"heartbeat"} and state.side == "inside"


def test_enter_within_ten_minutes_retracts_the_leave_and_later_enter_does_not():
    state, _ = inside_state()
    leave_at = T0 + timedelta(minutes=10)
    state, _ = run(state, [obs(leave_at, "outside")], leave_at)
    state, (leave,) = run(state, [], leave_at + CONFIRM_AFTER)

    back = leave_at + RETRACT_WINDOW  # exactly ten minutes: still earned
    state_a, _ = run(state, [obs(back, "inside", "i1")], back)
    state_a, (enter,) = run(
        state_a, [obs(back + timedelta(seconds=30), "inside", "i2")], back + timedelta(seconds=30)
    )
    assert enter.action == "enter" and enter.retracts == leave.event_id

    late = leave_at + RETRACT_WINDOW + timedelta(seconds=1)
    state_b, _ = run(state, [obs(late, "inside", "j1")], late)
    state_b, (enter,) = run(
        state_b, [obs(late + timedelta(seconds=30), "inside", "j2")], late + timedelta(seconds=30)
    )
    assert enter.action == "enter" and enter.retracts is None


def test_stale_input_drops_to_unknown_and_never_fabricates_a_leave():
    state, _ = inside_state()
    much_later = T0 + OBSERVATION_FRESH + timedelta(minutes=5)
    state, decisions = run(state, [], much_later)
    assert state.side is None and decisions == []
    out = much_later + timedelta(minutes=1)
    state, decisions = run(state, [obs(out, "outside")], out + CONFIRM_AFTER)
    (decision,) = decisions
    assert decision.action == "establish" and decision.reset_first  # not a "leave"


def test_observations_for_another_place_or_in_the_future_are_ignored():
    other = Observation(T0, "outside", UUID(int=7), "home_assistant", "x")
    state, decisions = run(PresenceState(), [other, obs(T0 + timedelta(minutes=1), "inside")], T0)
    assert state.side is None and decisions == []


def test_heartbeat_republishes_a_held_state_but_not_without_fresh_evidence():
    state, _ = inside_state()
    later = T0 + CONFIRM_AFTER + HEARTBEAT
    state, (beat,) = run(state, [obs(later - timedelta(seconds=5), "inside")], later)
    assert beat.action == "heartbeat" and beat.side == "inside" and beat.at == later
    again, decisions = run(state, [], later + HEARTBEAT)
    assert decisions == []  # no fresh observation this run: the slot is left to expire


def test_decisions_carry_no_coordinates_or_identifiers_beyond_opaque_refs():
    state, _ = inside_state()
    first = T0 + timedelta(minutes=10)
    state, _ = run(state, [obs(first, "outside")], first)
    _, (decision,) = run(state, [], first + CONFIRM_AFTER)
    text = repr(decision)
    assert "lat" not in text and "lon" not in text and "ssid" not in text.lower()


def test_heartbeat_holds_on_an_unchanged_fresh_observation_until_it_goes_stale():
    state, _ = inside_state()  # the only observation is the one at T0, already seen
    same = [obs(T0, "inside")]
    beat_at = T0 + CONFIRM_AFTER + HEARTBEAT
    state, (beat,) = run(state, same, beat_at)
    assert beat.action == "heartbeat" and beat.observed_at == T0
    state, decisions = run(state, same, T0 + OBSERVATION_FRESH + timedelta(seconds=1))
    assert decisions == [] and state.side is None  # the observation went stale: unknown
