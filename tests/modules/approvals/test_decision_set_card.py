"""The Telegram decision set card names exactly what Approve covers (bu-jv1moa.27 P2)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from butlers.modules.approvals.decision_set_card import (
    action_digest,
    approve_selection,
    reject_selection,
    render_set_card,
)
from butlers.modules.approvals.decision_set_view import build_set_view
from tests.modules.approvals.test_decision_set_view import NOW, SEND, _item, _row, _set

pytestmark = pytest.mark.unit

OTHER = {**SEND, "message": "Be there at 5"}


def _built(*pairs, **row_extra):
    """A decidable view over ``(item, args)`` pairs."""
    shown = _set(*(item for item, _ in pairs))
    rows = {item.effect_id: {**_row(item.effect_id, args), **row_extra} for item, args in pairs}
    return build_set_view(shown, rows, now=NOW)


def test_default_selection_is_the_plain_items_with_the_digests_shown():
    a, b = _item(0), _item(1, OTHER)
    view = _built((a, SEND), (b, OTHER))
    assert approve_selection(view) == {a.effect_id: a.item_digest, b.effect_id: b.item_digest}


def test_an_explicit_only_item_is_left_out_of_approve_and_named_on_the_card():
    plain = _item(0)
    admin = _item(1, OTHER, explicit=True)
    view = _built((plain, SEND), (admin, OTHER))
    assert view.decidable
    assert approve_selection(view) == {plain.effect_id: plain.item_digest}
    card = render_set_card(view)
    assert card.offer_decision
    assert "Approve covers: 1" in card.message
    assert "Needs your own tick on the dashboard: 2" in card.message


def test_a_set_with_only_explicit_only_items_offers_no_approve():
    admin = _item(0, explicit=True)
    view = _built((admin, SEND))
    assert approve_selection(view) is None
    assert not render_set_card(view).offer_decision


def test_reject_covers_every_item_with_explicit_only_items_in_their_own_map():
    plain, admin = _item(0), _item(1, OTHER, explicit=True)
    view = _built((plain, SEND), (admin, OTHER))
    selected, explicit = reject_selection(view)
    assert selected == {plain.effect_id: plain.item_digest}
    assert explicit == {admin.effect_id: admin.item_digest}


def test_the_action_digest_binds_the_set_and_the_exact_default_selection():
    a, b = _item(0), _item(1, OTHER)
    view = _built((a, SEND), (b, OTHER))
    assert action_digest(view) == action_digest(view)
    assert action_digest(view) != action_digest(replace(view, digest="e" * 64))
    assert action_digest(view) != action_digest(replace(view, version=2))
    fewer = replace(view, items=(view.items[0],))
    assert action_digest(view) != action_digest(fewer)


def test_an_undecidable_view_has_a_digest_no_card_token_can_match():
    a = _item(0)
    ok = _built((a, SEND))
    gone = build_set_view(_set(a), {}, now=NOW)
    assert not gone.decidable
    assert action_digest(gone) != action_digest(ok)


def test_the_card_states_what_an_unticked_item_comes_to_from_the_producer_value():
    a = _item(0)
    view = _built((a, SEND))
    assert "stay waiting for your decision" in render_set_card(view).message
    rejected = render_set_card(replace(view, unticked_outcome="rejected")).message
    assert "are rejected" in rejected
    unknown = render_set_card(replace(view, unticked_outcome="whatever")).message
    assert "whatever" not in unknown and "are not approved" in unknown


def test_facts_are_server_text_and_assistant_words_are_labelled_claims():
    a = _item(0)
    view = _built((a, SEND), why="Owner asked for it", blast_radius="one chat")
    message = render_set_card(view).message
    assert "`Running late`" in message
    assert "Assistant says (claims, not verified):" in message
    assert "Why: `Owner asked for it`" in message
    assert message.index("Running late") < message.index("Assistant says")


def test_a_claim_cannot_break_out_of_its_span_or_carry_a_live_link():
    a = _item(0)
    view = _built((a, SEND), why="x` [click](https://evil.example/p?q=1) **bold**‮evil")
    message = render_set_card(view).message
    why_line = next(line for line in message.splitlines() if "Why:" in line)
    assert why_line.count("`") == 2
    assert "https://" not in why_line and "‮" not in why_line


def test_a_fact_with_a_backtick_or_hidden_character_makes_the_set_open_only():
    a = _item(0)
    view = _built((a, {**SEND, "message": "hi `x`"}))
    card = render_set_card(view)
    assert not card.offer_decision
    assert "shown exactly here" in card.message
    assert "Approve and Reject are not offered" in card.message


def test_a_set_too_long_for_one_message_is_open_only_never_truncated():
    items = [_item(i, {**SEND, "message": f"{i} " + "x" * 900}) for i in range(8)]
    view = _built(*((it, {**SEND, "message": f"{it.position} " + "x" * 900}) for it in items))
    card = render_set_card(view)
    assert not card.offer_decision
    assert "Too long to show here" in card.message
    assert "x" * 900 not in card.message


def test_a_set_that_is_not_decidable_is_open_only():
    a = _item(0)
    card = render_set_card(build_set_view(_set(a), {}, now=NOW))
    assert not card.offer_decision
    assert "Approve and Reject are not offered" in card.message


# -- the notify envelope --------------------------------------------------------------------------

TOKENS = {"approve": "od1:" + "A" * 43, "reject": "od1:" + "B" * 43}


def _envelope(view, tokens=TOKENS):
    from butlers.modules.approvals.decision_set_card import build_set_envelope

    return build_set_envelope(
        view, origin_butler="switchboard", owner_recipient="9001", tokens=tokens
    )


def test_the_envelope_carries_the_card_the_tokens_and_a_set_id_only_link():
    a, b = _item(0), _item(1, OTHER)
    view = _built((a, SEND), (b, OTHER))
    envelope = _envelope(view)
    assert envelope["delivery"]["message"] == render_set_card(view).message
    assert envelope["delivery"]["intent"] == "approval_request"
    verbs = [(x["verb"], x.get("callback_token")) for x in envelope["actions"]]
    assert verbs == [
        ("approve", TOKENS["approve"]),
        ("reject", TOKENS["reject"]),
        ("open_dashboard", None),
    ]
    link = envelope["actions"][-1]["reference"]
    assert link == {
        "kind": "self",
        "route_key": "decision_set",
        "params": {"set_id": str(view.set_id)},
    }


def test_an_open_only_card_gets_no_decision_buttons_even_if_tokens_are_supplied():
    view = build_set_view(_set(_item(0)), {}, now=NOW)
    verbs = [x["verb"] for x in _envelope(view)["actions"]]
    assert verbs == ["open_dashboard"]


def test_one_missing_token_means_neither_button_is_attached():
    view = _built((_item(0), SEND))
    assert [x["verb"] for x in _envelope(view, {"approve": TOKENS["approve"]})["actions"]] == [
        "open_dashboard"
    ]
    assert [x["verb"] for x in _envelope(view, None)["actions"]] == ["open_dashboard"]
