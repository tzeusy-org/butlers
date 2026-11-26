"""Overlap resolution options: truth tables for the pure compiler (bu-5ii4gj.11 R1). Synthetic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from butlers.core.temporal import conflict_resolution as cr
from butlers.core.temporal.conflict_resolution import ResolutionEvent, options_for

pytestmark = pytest.mark.unit

_T = datetime(2026, 12, 1, 10, 0, tzinfo=UTC)
_NOW = _T - timedelta(days=1)
_FREE = [(_T + timedelta(hours=3), _T + timedelta(hours=6))]


def _ev(entry, cls, **kw):
    base = dict(
        entry_id=entry,
        provider_event_id=f"prov-{entry}",
        calendar_id="primary",
        start_at=_T,
        end_at=_T + timedelta(hours=1),
        etag=f"etag-{entry}",
        event_class=cls,
    )
    return ResolutionEvent(**{**base, **kw})


def _kinds(res):
    return [(o.kind, o.id.split(":")[-1], o.authority) for o in res.options]


def _run(a, b, free=_FREE, **kw):
    return options_for("pair-1", a, b, free, not_before=_NOW, **kw)


def test_a_butlers_block_against_a_meeting_moves_the_block_with_no_park():
    res = _run(_ev("a", "butlers_block"), _ev("b", "invited", owner_response="accepted"))
    kinds = [(o.kind, o.authority) for o in res.options]
    assert kinds[0] == ("move_block", "no_park_undo")
    move = res.options[0].exact_call
    assert move["tool"] == "calendar_update_event"
    assert move["args"]["start_at"] == (_T + timedelta(hours=3)).isoformat()
    assert move["args"]["request_id"] == "resolve:pair-1:move_block:a"
    assert res.options[-1].kind == "keep_both"


def test_a_solo_owner_event_moves_on_the_owners_own_submit():
    res = _run(_ev("a", "owner_organized"), _ev("b", "invited", owner_response="accepted"))
    assert ("move_own", "owner_submit") in [(o.kind, o.authority) for o in res.options]


def test_an_owner_event_with_attendees_is_parked_because_shifting_it_notifies_them():
    res = _run(
        _ev("a", "owner_organized", has_other_attendees=True),
        _ev("b", "butlers_block"),
    )
    kinds = {(o.kind, o.authority) for o in res.options}
    assert ("move_own", "parked_per_event") in kinds
    assert ("move_block", "no_park_undo") in kinds


def test_two_invitations_offer_parked_responses_on_either_and_ask_is_withheld_until_templates():
    a, b = (
        _ev("a", "invited", owner_response="accepted"),
        _ev("b", "invited", owner_response="accepted"),
    )
    res = _run(a, b)
    assert {o.kind for o in res.options} == {"decline", "tentative", "keep_both"}
    assert all(o.authority == "parked_per_event" for o in res.options if o.kind != "keep_both")
    with_templates = _run(a, b, templates_active=True)
    two_windows = [*_FREE, (_T + timedelta(hours=8), _T + timedelta(hours=10))]
    asks = [
        o
        for o in _run(a, b, free=two_windows, templates_active=True).options
        if o.kind == "ask_organizer"
    ]
    assert asks and all(len(o.exact_call["args"]["slots"]) == 2 for o in asks)
    assert [o.kind for o in with_templates.options].count("ask_organizer") <= 2


def test_an_already_tentative_invitation_does_not_offer_tentative_again():
    res = _run(
        _ev("a", "invited", owner_response="tentative"),
        _ev("b", "invited", owner_response="accepted"),
    )
    ids = [o.id for o in res.options]
    assert "tentative:a" not in ids and "tentative:b" in ids and "decline:a" in ids


def test_options_never_exceed_five_and_keep_both_always_holds_the_last_place():
    a, b = (
        _ev("a", "invited", owner_response="accepted"),
        _ev("b", "invited", owner_response="accepted"),
    )
    res = _run(a, b, templates_active=True)
    assert len(res.options) <= cr.MAX_OPTIONS and res.options[-1].kind == "keep_both"


def test_a_stale_source_offers_nothing_and_says_why():
    res = _run(_ev("a", "butlers_block"), _ev("b", "invited"), stale_source=True)
    assert res.options == [] and res.reason == "stale_source"


def test_no_fitting_slot_leaves_the_responses_and_keep_both():
    short = [(_T + timedelta(hours=3), _T + timedelta(hours=3, minutes=20))]
    res = _run(
        _ev("a", "butlers_block"), _ev("b", "invited", owner_response="accepted"), free=short
    )
    kinds = {o.kind for o in res.options}
    assert "move_block" not in kinds and {"decline", "keep_both"} <= kinds


def test_nothing_is_proposed_in_the_past():
    past = [(_NOW - timedelta(hours=5), _NOW + timedelta(hours=1))]
    res = _run(_ev("a", "butlers_block"), _ev("b", "owner_organized"), free=past)
    for o in res.options:
        if o.kind.startswith("move"):
            assert datetime.fromisoformat(o.exact_call["args"]["start_at"]) >= _NOW


def test_a_recurring_event_is_only_ever_moved_per_occurrence():
    original = _T + timedelta(days=7)
    res = _run(
        _ev("a", "owner_organized", instance_start_at=original),
        _ev("b", "invited", owner_response="accepted"),
    )
    move = next(o for o in res.options if o.kind == "move_own").exact_call["args"]
    assert move["recurrence_scope"] == "this" and move["instance_start_at"] == original.isoformat()
    assert all(o.exact_call["args"].get("recurrence_scope") != "series" for o in res.options)


def test_options_hold_no_event_text():
    res = _run(_ev("a", "butlers_block"), _ev("b", "invited", owner_response="accepted"))
    assert "title" not in repr(res).lower() and "description" not in repr(res).lower()


def test_the_same_input_gives_the_same_options_and_digest_in_either_order():
    a, b = _ev("a", "butlers_block"), _ev("b", "invited", owner_response="accepted")
    one, two = _run(a, b), _run(b, a)
    assert one == two
    assert cr.issue_digest([a, b], one.options) == cr.issue_digest([b, a], two.options)


def test_the_digest_changes_with_an_etag_or_a_time():
    a, b = _ev("a", "butlers_block"), _ev("b", "invited", owner_response="accepted")
    options = _run(a, b).options
    base = cr.issue_digest([a, b], options)
    assert cr.issue_digest([_ev("a", "butlers_block", etag="new"), b], options) != base
    moved = _ev("b", "invited", start_at=_T + timedelta(minutes=5), owner_response="accepted")
    assert cr.issue_digest([a, moved], options) != base


def test_equal_inputs_within_one_grid_step_give_equal_digests():
    first = _run_at(_NOW + timedelta(seconds=1))
    again = _run_at(_NOW + timedelta(seconds=9))  # eight seconds later, same quarter-hour
    assert first == again


def _run_at(now):
    a, b = _ev("a", "butlers_block"), _ev("b", "invited", owner_response="accepted")
    free = [(now - timedelta(hours=1), now + timedelta(hours=6))]
    res = options_for("pair-1", a, b, free, not_before=now)
    return cr.issue_digest([a, b], res.options)


def test_slots_sit_on_the_grid_even_when_a_busy_interval_ends_at_an_odd_minute():
    odd = [(_T + timedelta(hours=3, minutes=7, seconds=13), _T + timedelta(hours=9))]
    res = _run(_ev("a", "butlers_block"), _ev("b", "owner_organized"), free=odd)
    start = datetime.fromisoformat(
        next(o for o in res.options if o.kind.startswith("move")).exact_call["args"]["start_at"]
    )
    assert start == _T + timedelta(hours=3, minutes=15)


def test_a_movable_event_with_no_free_slot_says_no_free_slot():
    res = _run(_ev("a", "butlers_block"), _ev("b", "invited", owner_response="accepted"), free=[])
    assert res.reason == "no_free_slot"
    assert {o.kind for o in res.options} >= {"decline", "keep_both"}


def test_a_recurring_event_without_its_occurrence_is_never_moved():
    res = _run(
        _ev("a", "owner_organized", recurring=True),  # no instance_start_at
        _ev("b", "invited", owner_response="accepted"),
    )
    assert not [o for o in res.options if o.kind.startswith("move")]
    assert res.reason is None  # not a lack of slots: the occurrence is simply unknown


def test_a_radar_decline_sends_no_updates():
    res = _run(_ev("a", "invited", owner_response="accepted"), _ev("b", "invited"))
    declines = [o for o in res.options if o.kind in ("decline", "tentative")]
    assert declines and all(o.exact_call["args"]["send_updates"] == "none" for o in declines)
