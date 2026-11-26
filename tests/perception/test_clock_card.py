"""The one owner card per clock transition (bu-5ii4gj.5): wording, doors and honesty."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from butlers.perception import clock_card as card
from butlers.perception.clock import ClockValue
from butlers.perception.clock_reanchor import Kept, Move, ReanchorPlan

pytestmark = pytest.mark.unit

HOME = ClockValue("Asia/Singapore", "home")
TOKYO = ClockValue("Asia/Tokyo", "owner_confirmed_booking")
AT = datetime(2026, 7, 2, 11, 0, tzinfo=UTC)  # 19:00 in Singapore, 20:00 in Tokyo


def move(name="brief", n=0):
    return Move(f"id{n}", name, AT, AT, name.title())


def result(plans, **extra):
    base = {"live": True, "complete": True, "failed": [], "per_schema": {}, "plans": plans}
    base.update(extra)
    return base


def compose(plans, new=TOKYO, **extra):
    return card.compose_transition_card(
        result(plans, **extra), old_clock=HOME, new_clock=new, needs_ok=extra.pop("ok", ())
    )


def test_a_move_names_the_new_place_and_counts_items_with_a_door_each():
    plan = ReanchorPlan(moves=(move("a", 1), move("b", 2), move("c", 3)))
    out = compose({"general": plan})
    assert out.text == "Moved to Tokyo time: 3 items."
    assert [(d.schema, d.name) for d in out.doors] == [
        ("general", "a"),
        ("general", "b"),
        ("general", "c"),
    ]


def test_a_pinned_row_is_kept_and_shown_in_both_zones():
    kept = Kept("call_mum", "Call Mum", "Asia/Singapore", AT)
    out = compose({"general": ReanchorPlan(moves=(move(),), kept=(kept,))})
    assert "Kept: Call Mum 19:00 Singapore (20:00 here)." in out.text
    assert any(d.name == "call_mum" for d in out.doors)


def test_one_item_is_singular_and_a_long_list_is_capped():
    assert compose({"g": ReanchorPlan(moves=(move(),))}).text == "Moved to Tokyo time: 1 item."
    many = tuple(Kept(f"k{i}", f"Item {i}", "Asia/Singapore", AT) for i in range(8))
    out = compose({"g": ReanchorPlan(moves=(move(),), kept=many)})
    assert "and 3 more" in out.text and "Item 4" in out.text and "Item 5" not in out.text


def test_approvals_are_listed_under_needs_ok():
    out = card.compose_transition_card(
        result({"g": ReanchorPlan(moves=(move(),))}),
        old_clock=HOME,
        new_clock=TOKYO,
        needs_ok=["Metformin shift plan"],
    )
    assert "Needs OK: Metformin shift plan." in out.text


def test_an_unknown_clock_says_so_and_keeps_the_old_zone():
    out = compose({}, new=None)
    assert out.text == "Clock unknown: schedules stay on Singapore time."


def test_an_incomplete_run_names_the_unread_schema_and_never_claims_done():
    out = compose({"g": ReanchorPlan(moves=(move(),))}, failed=["travel"], complete=False)
    assert "Not checked: travel; will retry." in out.text


def test_nothing_to_say_composes_no_card_and_a_dark_run_none_at_all():
    assert compose({"g": ReanchorPlan()}) is None
    assert card.compose_transition_card({"live": False}, old_clock=HOME, new_clock=TOKYO) is None
