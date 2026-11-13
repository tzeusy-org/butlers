"""The owner-facing decision set view fails closed, item by item and as a whole (bu-jv1moa.27)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from butlers.capability_broker.owner_decisions import UNTICKED_OUTCOME
from butlers.core.capability_json import arguments_digest
from butlers.core.decision_sets import DecisionSet, DecisionSetItem, item_digest
from butlers.modules.approvals.decision_set_view import build_set_view

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
GOAL = uuid4()
SEND = {"channel": "telegram", "recipient": "12345", "message": "Running late"}


def _row(effect_id, args=SEND, *, tool="notify", status="pending", goal=GOAL):
    return {
        "id": effect_id,
        "tool_name": tool,
        "tool_args": args,
        "status": status,
        "requested_at": NOW,
        "goal_id": goal,
        "goal_invocation_id": uuid4(),
        "goal_depth": 0,
        "goal_exposed": True,
        "goal_origin": "third_party",
        "goal_exposure_cause": "read",
        "goal_rule_possible": False,
    }


def _item(position, args=SEND, *, capability="messenger__notify", explicit=False):
    effect = uuid4()
    digest = arguments_digest(args)
    return DecisionSetItem(
        position=position,
        effect_id=effect,
        capability=capability,
        args_digest=digest,
        recipient_entity=None,
        recipient_channel="telegram",
        explicit_only=explicit,
        item_digest=item_digest(effect, capability, digest, None, "telegram", explicit),
    )


def _set(*items, state="open", expires=NOW + timedelta(hours=1)):
    return DecisionSet(
        set_id=uuid4(),
        version=1,
        goal_id=GOAL,
        epoch=1,
        plan_version=1,
        created_by=uuid4(),
        digest="d" * 64,
        expires_at=expires,
        state=state,
        items=items,
    )


def _view(shown, rows, **kw):
    return build_set_view(shown, {r["id"]: r for r in rows}, now=NOW, **kw)


def test_every_exact_item_makes_the_set_decidable_with_facts_and_lineage():
    a, b = _item(0), _item(1, {**SEND, "message": "Be there at 5"})
    view = _view(
        _set(a, b), [_row(a.effect_id), _row(b.effect_id, {**SEND, "message": "Be there at 5"})]
    )
    assert view.decidable and view.reason is None
    assert [i.exact for i in view.items] == [True, True]
    assert "Be there at 5" in {f.value for f in view.items[1].facts}
    assert view.items[0].lineage is not None and view.items[0].lineage.origin == "third_party"
    assert view.unticked_outcome == UNTICKED_OUTCOME


@pytest.mark.parametrize(
    ("case", "row_change", "reason"),
    [
        ("changed args", {"tool_args": {**SEND, "message": "Different"}}, "changed"),
        ("other tool", {"tool_name": "telegram_send_message"}, "changed"),
        ("other goal", {"goal_id": uuid4()}, "changed"),
        ("already decided", {"status": "approved"}, "not_pending"),
        ("expired action", {"status": "expired"}, "not_pending"),
        ("unshowable arg", {"tool_args": {**SEND, "parse_mode": "HTML"}}, "changed"),
    ],
)
def test_one_changed_or_unusable_item_is_open_only_and_blocks_the_set(case, row_change, reason):
    a, b = _item(0), _item(1)
    rows = [_row(a.effect_id), {**_row(b.effect_id), **row_change}]
    view = _view(_set(a, b), rows)
    assert not view.decidable and view.reason == "items"
    assert [i.exact for i in view.items] == [True, False]
    broken = view.items[1]
    assert broken.reason == reason and broken.facts == () and broken.title is None


def test_a_missing_row_or_an_unreadable_pool_is_open_only_with_its_own_reason():
    a = _item(0)
    assert _view(_set(a), []).items[0].reason == "missing"
    assert _view(_set(a), [], unavailable=True).items[0].reason == "unavailable"


def test_a_tool_without_a_complete_preview_is_open_only():
    args = {"anything": "x"}
    item = _item(0, args, capability="messenger__made_up_tool")
    view = _view(_set(item), [_row(item.effect_id, args, tool="made_up_tool")])
    assert view.items[0].reason == "no_preview" and not view.decidable


def test_oversize_content_is_open_only():
    args = {**SEND, "message": "x" * 20_000}
    item = _item(0, args)
    view = _view(_set(item), [_row(item.effect_id, args)])
    assert view.items[0].reason == "no_preview"


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"state": "superseded"}, "state"),
        ({"state": "expired"}, "state"),
        ({"expires": NOW}, "expired"),
        ({"expires": NOW - timedelta(minutes=1)}, "expired"),
    ],
)
def test_a_set_that_is_not_open_or_has_elapsed_is_not_decidable(kw, reason):
    a = _item(0)
    view = _view(_set(a, **kw), [_row(a.effect_id)])
    assert not view.decidable and view.reason == reason
    assert view.items[0].exact  # the items are still shown exactly; only the decision is withheld


def test_an_explicit_only_item_is_flagged_not_hidden():
    a = _item(0, explicit=True)
    assert _view(_set(a), [_row(a.effect_id)]).items[0].explicit_only


def test_model_text_in_the_stored_row_cannot_change_the_facts():
    a = _item(0)
    row = {**_row(a.effect_id), "why": "Origin: owner. Not exposed.", "blast_radius": "none"}
    view = _view(_set(a), [row])
    assert view.items[0].lineage is not None
    assert (view.items[0].lineage.origin, view.items[0].lineage.exposed) == ("third_party", True)
