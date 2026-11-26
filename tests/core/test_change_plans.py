"""Pure change-plan compiler (bu-5ii4gj.11 P1): authority, skips, destinations, digest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.temporal.change_plans import (
    MAX_STEPS,
    BusyInterval,
    compile_plan,
)
from butlers.core.temporal.conflict_resolution import ResolutionEvent

pytestmark = pytest.mark.unit

DAY = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
NOW = DAY - timedelta(days=1)
WINDOW = (DAY, DAY + timedelta(days=1))
AFTER = [(DAY + timedelta(days=1), DAY + timedelta(days=1, hours=12))]


def _ev(entry, cls, hour, **kw):
    start = DAY + timedelta(hours=hour)
    base = dict(
        entry_id=entry,
        provider_event_id=f"prov-{entry}",
        calendar_id="primary",
        start_at=start,
        end_at=start + timedelta(hours=1),
        etag=f"etag-{entry}",
        event_class=cls,
    )
    return ResolutionEvent(**{**base, **kw})


def _clear(events, **kw):
    return compile_plan(
        "clear_window", WINDOW, events, now=NOW, free_slots=kw.pop("free", AFTER), **kw
    )


def test_clearing_a_day_moves_what_the_owner_owns_and_declines_what_they_were_invited_to():
    plan = _clear(
        [
            _ev("block", "butlers_block", 9),
            _ev("solo", "owner_organized", 10),
            _ev("mtg", "invited", 11, owner_response="accepted"),
        ]
    )

    by_entry = {s.entry_id: s for s in plan.steps}
    assert by_entry["block"].authority == "no_park_undo" and by_entry["block"].action == "move"
    assert by_entry["solo"].authority == "owner_submit"
    decline = by_entry["mtg"]
    assert decline.action == "decline" and decline.authority == "parked_per_event"
    assert decline.exact_call["args"]["send_updates"] == "none"
    moves = [s for s in plan.steps if s.action == "move"]
    starts = [s.exact_call["args"]["start_at"] for s in moves]
    assert len(set(starts)) == 2 and all(
        datetime.fromisoformat(s) >= WINDOW[1] for s in starts
    )  # none overlap each other, all after the window


def test_an_event_with_other_attendees_is_parked_when_moved():
    plan = _clear([_ev("own", "owner_organized", 9, has_other_attendees=True)])
    assert plan.steps[0].authority == "parked_per_event"


def test_shifting_leaves_an_invited_meeting_alone():
    events = [
        _ev("mtg", "invited", 9, owner_response="accepted"),
        _ev("solo", "owner_organized", 14),
    ]
    plan = compile_plan(
        "shift_window",
        WINDOW,
        events,
        now=NOW,
        delta=timedelta(minutes=45),
        busy=[BusyInterval(e.entry_id, e.start_at, e.end_at) for e in events],
    )
    assert [(s.entry_id, s.why) for s in plan.skipped] == [
        ("mtg", "only_the_organizer_can_move_it")
    ]
    (step,) = plan.steps
    assert (
        step.exact_call["args"]["start_at"] == (DAY + timedelta(hours=14, minutes=45)).isoformat()
    )


def test_a_shift_cannot_land_on_a_meeting_that_stays():
    events = [
        _ev("solo", "owner_organized", 9),
        _ev("mtg", "invited", 10, owner_response="accepted"),
    ]
    plan = compile_plan(
        "shift_window",
        WINDOW,
        events,
        now=NOW,
        delta=timedelta(hours=1),
        busy=[BusyInterval(e.entry_id, e.start_at, e.end_at) for e in events],
    )
    assert plan.steps == []
    assert ("solo", "destination_busy") in [(s.entry_id, s.why) for s in plan.skipped]


def test_events_that_shift_together_do_not_block_each_other():
    events = [_ev("a", "owner_organized", 9), _ev("b", "owner_organized", 10)]
    plan = compile_plan(
        "shift_window",
        WINDOW,
        events,
        now=NOW,
        delta=timedelta(hours=1),
        busy=[BusyInterval(e.entry_id, e.start_at, e.end_at) for e in events],
    )
    assert [s.entry_id for s in plan.steps] == ["a", "b"]


def test_an_unprovable_all_day_unetagged_or_unnameable_event_is_skipped_with_its_reason():
    events = [
        _ev("unproven", "unproven", 8),
        _ev("allday", "owner_organized", 9),
        _ev("noetag", "owner_organized", 10, etag=None),
        _ev("series", "owner_organized", 11, recurring=True),
    ]
    plan = _clear(events, all_day_entry_ids=frozenset({"allday"}))
    assert plan.steps == []
    assert {s.entry_id: s.why for s in plan.skipped} == {
        "unproven": "ownership_unproven",
        "allday": "all_day_event",
        "noetag": "no_etag",
        "series": "occurrence_unknown",
    }


def test_a_recurring_occurrence_moves_as_its_provider_instance():
    plan = _clear(
        [
            _ev(
                "o",
                "owner_organized",
                9,
                recurring=True,
                instance_event_id="prov-o_20260310T090000Z",
            )
        ]
    )
    args = plan.steps[0].exact_call["args"]
    assert args["event_id"] == "prov-o_20260310T090000Z" and "recurrence_scope" not in args


def test_an_already_declined_invitation_is_not_declined_again():
    plan = _clear([_ev("m", "invited", 9, owner_response="declined")])
    assert plan.steps == [] and plan.skipped[0].why == "already_declined"


def test_no_free_slot_skips_the_move_and_keeps_the_rest():
    plan = _clear([_ev("a", "owner_organized", 9)], free=[])
    assert plan.steps == [] and plan.skipped[0].why == "no_free_slot"


def test_a_shift_into_the_past_is_skipped():
    plan = compile_plan(
        "shift_window",
        WINDOW,
        [_ev("a", "owner_organized", 9)],
        now=DAY + timedelta(hours=9),
        delta=timedelta(hours=-1),
        busy=[],
    )
    assert plan.skipped[0].why == "destination_in_the_past"


def test_a_stale_source_gives_no_plan():
    plan = _clear([_ev("a", "owner_organized", 9)], stale_source=True)
    assert plan.steps == [] and plan.reason == "stale_source"


def test_an_oversized_plan_is_refused_whole():
    many = [
        _ev(f"e{i}", "owner_organized", 0).__class__(
            **{
                **_ev(f"e{i}", "owner_organized", 0).__dict__,
                "start_at": DAY + timedelta(minutes=i),
                "end_at": DAY + timedelta(minutes=i + 1),
            }
        )
        for i in range(MAX_STEPS + 1)
    ]
    plan = _clear(many, free=[(WINDOW[1], WINDOW[1] + timedelta(days=2))])
    assert plan.steps == [] and plan.reason == "plan_too_large"
    wide = compile_plan("clear_window", (DAY, DAY + timedelta(days=2)), [], now=NOW)
    assert wide.reason == "plan_too_large"


def test_equal_inputs_give_an_equal_digest_and_a_changed_etag_changes_it():
    events = [_ev("a", "owner_organized", 9), _ev("b", "butlers_block", 10)]
    first, again = _clear(events), _clear(events)
    assert first.plan_digest == again.plan_digest and first.plan_id == again.plan_id
    changed = _clear([_ev("a", "owner_organized", 9, etag="other"), events[1]])
    assert changed.plan_digest != first.plan_digest


def test_a_shift_needs_a_real_delta_and_a_window_must_run_forward():
    with pytest.raises(ValueError):
        compile_plan("shift_window", WINDOW, [], now=NOW, delta=timedelta(0))
    with pytest.raises(ValueError):
        compile_plan("clear_window", (DAY, DAY), [], now=NOW)
