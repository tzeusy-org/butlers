"""Owner-local schedules follow the owner clock (bu-5ii4gj.5 P5): pure planning and evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from butlers.core.scheduler.timing import _next_run
from butlers.core.temporal.projected_zone import TripLeg
from butlers.perception import clock_reanchor as ra
from butlers.perception.clock import ClockState, ClockValue

pytestmark = pytest.mark.unit

HOME = "Asia/Singapore"
NOW = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(hours=1)


def row(cron="0 9 * * *", tz="UTC", nxt=None, task_type="cron", name="brief"):
    nxt = nxt or _next_run(cron or "0 9 * * *", timezone=HOME, now=NOW)
    return {
        "id": name,
        "name": name,
        "cron": cron,
        "timezone": tz,
        "next_run_at": nxt,
        "task_type": task_type,
    }


def plan(rows, new_zone="Asia/Tokyo", **kw):
    args = {
        "old_zone": HOME,
        "new_zone": new_zone,
        "transition_at": LONG_AGO,
        "now": NOW,
    }
    args.update(kw)
    return ra.plan_reanchor(rows, **args)


def test_an_owner_local_row_moves_to_the_same_wall_clock_in_the_new_zone():
    [move] = plan([row()]).moves
    assert move.new_next_run_at == _next_run("0 9 * * *", timezone="Asia/Tokyo", now=NOW)
    # 09:00 Tokyo is 00:00Z, an hour before 09:00 Singapore (01:00Z).
    assert move.new_next_run_at.astimezone(ZoneInfo("Asia/Tokyo")).hour == 9
    assert move.new_next_run_at != move.old_next_run_at


def test_a_pinned_zone_and_a_one_shot_row_are_never_touched():
    rows = [
        row(tz="Europe/London", name="pinned"),
        row(cron=None, name="oneshot"),
        row(task_type="deadline", name="deadline"),
    ]
    result = plan(rows)
    assert result.moves == () and result.kept_pinned == 3


def test_an_unknown_clock_moves_nothing_so_obligations_stay_on_the_home_zone():
    result = plan([row()], new_zone=None)
    assert result.moves == () and result.reason == "clock_unknown"


def test_the_same_zone_is_a_no_op():
    assert plan([row()], new_zone=HOME).reason == "same_zone"


def test_a_transition_inside_the_retraction_window_waits_so_a_flip_back_is_a_no_op():
    young = NOW - timedelta(minutes=9)
    result = plan([row()], transition_at=young)
    assert result.moves == () and result.reason == "settling"
    assert plan([row()], transition_at=NOW - ra.RETRACTION_WINDOW).moves  # old enough


def test_a_row_already_due_fires_once_on_its_old_anchor():
    result = plan([row(nxt=NOW - timedelta(minutes=1))])
    assert result.moves == () and result.skipped == 1


def test_a_row_whose_next_run_is_already_right_is_not_rewritten():
    same = _next_run("0 9 * * *", timezone="Asia/Tokyo", now=NOW)
    assert plan([row(nxt=same)]).moves == ()


def test_a_westward_move_across_dst_lands_on_the_local_hour():
    [move] = plan([row(cron="30 7 * * *")], new_zone="America/Los_Angeles").moves
    local = move.new_next_run_at.astimezone(ZoneInfo("America/Los_Angeles"))
    assert (local.hour, local.minute) == (7, 30) and local.utcoffset() == timedelta(hours=-7)


def test_naive_instants_are_refused():
    with pytest.raises(ValueError):
        plan([row()], now=datetime(2026, 7, 1))


# -- evidence from legs ---------------------------------------------------------------------


def leg(arr, city="London", code="LHR", confirmed=True):
    return TripLeg(arr - timedelta(hours=13), arr, code, city, confirmed)


def booked(*legs):
    return [ra.BookedLeg(item, f"leg-{i}") for i, item in enumerate(legs)]


def test_a_confirmed_leg_moves_the_clock_at_arrival_not_before():
    arrival = NOW + timedelta(days=1)
    state = ClockState.initial(HOME)
    before = ra.clock_after_legs(state, booked(leg(arrival)), now=NOW)
    after = ra.clock_after_legs(state, booked(leg(arrival)), now=arrival + timedelta(minutes=1))
    assert before.current == ClockValue(HOME, "home")
    assert after.current == ClockValue("Europe/London", "owner_confirmed_booking")


def test_an_unconfirmed_leg_only_asks_once_and_never_switches():
    arrival = NOW - timedelta(hours=2)
    items = booked(leg(arrival, confirmed=False))
    state = ra.clock_after_legs(ClockState.initial(HOME), items, now=NOW)
    again = ra.clock_after_legs(state, items, now=NOW)
    assert state.current.zone == HOME and "leg-0" in state.asked and again == state


def test_a_place_the_table_does_not_name_yields_no_evidence():
    atlantis = leg(NOW - timedelta(hours=2), city="Atlantis", code=None)
    assert ra.evidence_from_legs(booked(atlantis), now=NOW) == []


def test_a_return_leg_into_the_home_city_confirms_the_home_zone():
    out = leg(NOW - timedelta(days=3))
    back = leg(NOW - timedelta(hours=2), city="Singapore", code=None)
    state = ra.clock_after_legs(
        ClockState.initial(HOME), booked(out, back), now=NOW, home_city="Singapore"
    )
    assert state.current.zone == HOME
