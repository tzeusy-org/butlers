"""``calendar_undo_plan``: one bound card per applied step (bu-5ii4gj.11 P2a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers.modules.calendar.plan_undo import undo_plan
from butlers.modules.calendar.resolution_move import (
    record_applied_move,
    resolution_refusal,
)

pytestmark = pytest.mark.unit

APPLIED = {
    "status": "executed",
    "execution_result": {"success": True, "result": {"status": "updated"}},
}
DECLINED = {
    "status": "executed",
    "execution_result": {
        "success": False,
        "result": {"status": "resolution_declined", "reason": "changed_since_check"},
    },
}
PENDING = {"status": "pending", "execution_result": None}


class _Pool:
    def __init__(self, receipts, cards, bindings, tool_args):
        self.receipts, self.cards = receipts, cards
        self.bindings, self.tool_args = bindings, tool_args

    async def fetch(self, sql, *args):
        return [{"action_payload": r} for r in self.receipts]

    async def fetchrow(self, sql, *args):
        if "idempotency_key" in sql:
            return {"action_payload": {"plan_digest": "d" * 64}}
        if "tool_args" in sql:
            args_ = self.tool_args.get(args[0])
            return {"tool_args": args_} if args_ is not None else None
        if "resolution_move" in sql:
            b = self.bindings.get(args[0])
            return {"action_result": b} if b is not None else None
        return self.cards.get(args[0])


def _step(index, card, **extra):
    return {
        "plan_id": "plan_x",
        "index": index,
        "event_ref": f"e{index}",
        "action": "move",
        "action_id": str(card),
        "authority": "owner_submit",
        **extra,
    }


def _world(*steps):
    """steps: (index, card state row, binding or None)."""
    receipts, cards, bindings, tool_args = [], {}, {}, {}
    for index, state, binding in steps:
        card = uuid.uuid4()
        receipts.append(_step(index, card))
        cards[card] = state
        tool_args[card] = {"event_id": f"evt-{index}", "calendar_id": "primary"}
        if binding is not None:
            bindings[str(card)] = {"action_id": str(card), **binding}
    return _Pool(receipts, cards, bindings, tool_args)


async def _run(pool):
    parked: list[dict] = []

    async def park(pool_, **kw):
        parked.append(kw)
        return uuid.uuid4(), True

    with (
        patch("butlers.modules.calendar.plan_undo.pool_of", return_value=pool),
        patch("butlers.modules.calendar.plan_undo.park_resolution_move", park),
        patch(
            "butlers.modules.calendar.plan_undo.load_configured_general_timezone",
            AsyncMock(return_value="UTC"),
        ),
    ):
        result = await undo_plan(
            SimpleNamespace(), "general", plan_id="plan_x", now=datetime(2026, 3, 1, tzinfo=UTC)
        )
    return result, parked


GOOD = {
    "etag": "e0",
    "etag_after": "e1",
    "pre_start": "2026-03-10T09:00:00+00:00",
    "pre_end": "2026-03-10T10:00:00+00:00",
}


async def test_only_applied_steps_are_reversed_each_bound_to_its_recorded_etag():
    pool = _world(
        (0, APPLIED, GOOD), (1, PENDING, GOOD), (2, APPLIED, {**GOOD, "etag_after": "e9"})
    )

    result, parked = await _run(pool)

    assert result["status"] == "pending"
    # highest step first, so a reversal's dependencies are parked before it
    assert [p["etag"] for p in parked] == ["e9", "e1"]
    assert all(p["stale_reason"] == "changed_since_move" for p in parked)
    assert parked[1]["call_args"] == {
        "event_id": "evt-0",
        "calendar_id": "primary",
        "start_at": GOOD["pre_start"],
        "end_at": GOOD["pre_end"],
    }
    by_index = {r["index"]: r for r in result["reversals"]}
    assert by_index[1] == {"index": 1, "event_ref": "e1", "undone": False, "reason": "step_pending"}
    assert by_index[0]["approval_id"] and by_index[0]["undone"] is None


async def test_a_step_with_no_recorded_result_is_never_offered_unbound():
    result, parked = await _run(_world((0, APPLIED, {"etag": "e0"}), (1, APPLIED, None)))

    assert parked == []
    assert {r["reason"] for r in result["reversals"]} == {"no_recorded_result"}


async def test_an_unknown_plan_is_refused():
    pool = _world()
    pool.fetchrow = AsyncMock(return_value=None)

    with (
        patch("butlers.modules.calendar.plan_undo.pool_of", return_value=pool),
        patch(
            "butlers.modules.calendar.plan_undo.load_configured_general_timezone",
            AsyncMock(return_value="UTC"),
        ),
    ):
        result = await undo_plan(SimpleNamespace(), "general", plan_id="plan_none")

    assert result["reason"] == "unknown_plan"


def test_an_undo_card_declines_changed_since_move_not_changed_since_check():
    offer = {"etag": "e1", "stale_reason": "changed_since_move"}
    assert resolution_refusal(offer, SimpleNamespace(etag="e1")) is None
    assert resolution_refusal(offer, SimpleNamespace(etag="e2")) == "changed_since_move"
    assert resolution_refusal({"etag": "e1"}, SimpleNamespace(etag="e2")) == "changed_since_check"
    assert (
        resolution_refusal({"etag": "e1", "stale_reason": "bogus"}, SimpleNamespace(etag="x"))
        == "changed_since_check"
    )


async def test_an_applied_move_records_the_etag_it_left_and_the_times_it_replaced():
    pool = SimpleNamespace(execute=AsyncMock())
    action = uuid.uuid4()

    await record_applied_move(pool, action, etag_after="e1", pre_start="s", pre_end="e")

    _sql, bound_id, payload = pool.execute.await_args.args
    assert bound_id == str(action)
    assert payload == {"etag_after": "e1", "pre_start": "s", "pre_end": "e"}


async def test_recording_is_best_effort_and_skipped_without_an_etag():
    failing = SimpleNamespace(execute=AsyncMock(side_effect=RuntimeError("down")))
    await record_applied_move(failing, uuid.uuid4(), etag_after="e1", pre_start="s", pre_end="e")
    silent = SimpleNamespace(execute=AsyncMock())
    await record_applied_move(silent, uuid.uuid4(), etag_after=None, pre_start="s", pre_end="e")
    silent.execute.assert_not_awaited()


def _shift_world(a_state, b_state, *, depends=True):
    """The A/B shift +60 plan: b (index 0) moved first, a (index 1) landed on b's old slot."""
    pool = _world((0, b_state, GOOD), (1, a_state, {**GOOD, "etag_after": "e2"}))
    if depends:
        pool.receipts[1]["depends_on"] = [0]
    pool.receipts[0]["event_ref"], pool.receipts[1]["event_ref"] = "b", "a"
    return pool


async def test_a_reversal_into_a_slot_another_step_occupies_waits_for_that_reversal():
    pool = _shift_world(APPLIED, APPLIED)

    result, parked = await _run(pool)

    # a (index 1) is reversed first; b's reversal returns into the slot a held, so it waits for it.
    assert [p["etag"] for p in parked] == ["e2", "e1"]
    assert parked[0]["depends_on"] == []
    by_ref = {r["event_ref"]: r for r in result["reversals"]}
    assert by_ref["a"]["depends_on"] == [] and by_ref["b"]["depends_on"] == [1]
    assert len(parked[1]["depends_on"]) == 1


async def test_a_reversal_does_not_wait_for_a_step_that_never_applied():
    result, parked = await _run(_shift_world(DECLINED, APPLIED))

    assert len(parked) == 1 and parked[0]["depends_on"] == []
    by_ref = {r["event_ref"]: r for r in result["reversals"]}
    assert by_ref["a"]["reason"] == "step_declined" and by_ref["b"]["approval_id"]


async def test_a_reversal_blocked_by_an_unreversible_dependent_is_not_offered():
    pool = _shift_world(APPLIED, APPLIED)
    pool.bindings = {k: v for k, v in pool.bindings.items() if v["etag_after"] != "e2"}

    result, parked = await _run(pool)

    assert parked == []
    reasons = {r["event_ref"]: r["reason"] for r in result["reversals"]}
    assert reasons == {"a": "no_recorded_result", "b": "blocked_by_step_1"}


async def test_a_declined_card_is_never_reversed():
    result, parked = await _run(_world((0, DECLINED, GOOD)))

    assert parked == [] and result["reversals"][0]["reason"] == "step_declined"


async def test_a_reversal_into_the_past_is_refused_with_a_reason():
    late = {
        **GOOD,
        "pre_start": "2020-01-01T09:00:00+00:00",
        "pre_end": "2020-01-01T10:00:00+00:00",
    }

    result, parked = await _run(_world((0, APPLIED, late)))

    assert parked == [] and result["reversals"][0]["reason"] == "in_the_past"
