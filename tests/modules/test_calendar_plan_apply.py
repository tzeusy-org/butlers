"""``calendar_apply_plan`` parks one card per move step and records receipts (bu-5ii4gj.11 P2a)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from butlers.core.temporal.change_plans import BusyInterval, compile_plan
from butlers.core.temporal.conflict_resolution import ResolutionEvent
from butlers.modules.calendar.plan_apply import apply_plan, step_outcome
from butlers.modules.calendar.plan_tools import CompiledPlan
from butlers.modules.calendar.resolution_move import (
    DEFERRED_ERROR,
    RESOLUTION_DECLINED_TEXTS,
    check_resolution_binding,
    dependency_state,
)

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


def _clear_plan(events):
    return compile_plan("clear_window", WINDOW, events, now=NOW, free_slots=AFTER)


def _shift_plan(events, minutes):
    return compile_plan(
        "shift_window",
        WINDOW,
        events,
        now=NOW,
        delta=timedelta(minutes=minutes),
        busy=[BusyInterval(e.entry_id, e.start_at, e.end_at) for e in events],
    )


class _Pool:
    def __init__(self, receipts=(), cards=None, commands=None):
        self.receipts = [{"action_payload": r} for r in receipts]
        self.cards = cards or {}
        self.commands = commands or {}
        self.failures: dict = {}
        self.inserted: list[tuple] = []

    async def fetch(self, sql, *args):
        return self.receipts

    async def fetchval(self, sql, *args):
        return False  # the 48 hour wait is not over

    async def fetchrow(self, sql, *args):
        if "approval_events" in sql:
            reason = self.failures.get(args[0])
            return None if reason is None else {"reason": reason}
        if "calendar_action_log" in sql:
            return self.commands.get(args[0])
        return self.cards.get(args[0])

    async def execute(self, sql, *args):
        self.inserted.append(args)


def _run(plan, pool, **overrides):
    kind = plan.kind
    compiled = CompiledPlan(plan, WINDOW, "UTC")
    parked: list[dict] = []
    responded: list[dict] = []

    async def park(pool_, **kw):
        parked.append(kw)
        card = uuid.uuid4()
        pool.cards[card] = {"status": "pending", "execution_result": None}
        return card, True

    async def respond(module, arguments, **kw):
        if "respond_override" in overrides:
            return overrides["respond_override"]
        card = uuid.uuid4()
        pool.cards[card] = {"status": "pending", "execution_result": None}
        responded.append(arguments)
        return {"status": "pending_approval", "command_id": str(card), "action_id": str(card)}

    async def go():
        with (
            patch(
                "butlers.modules.calendar.plan_apply.compile_for_window",
                AsyncMock(return_value=compiled),
            ),
            patch("butlers.modules.calendar.plan_apply.pool_of", return_value=pool),
            patch("butlers.modules.calendar.plan_apply.park_resolution_move", park),
            patch("butlers.modules.calendar.plan_apply.park_response", respond),
        ):
            return await apply_plan(
                SimpleNamespace(),
                "general",
                plan_id=overrides.get("plan_id", plan.plan_id),
                plan_digest=overrides.get("plan_digest", plan.plan_digest),
                kind=kind,
                window_start=WINDOW[0],
                window_end=WINDOW[1],
                shift_minutes=45 if kind == "shift_window" else None,
            )

    go.responded = responded
    return go, parked


async def test_a_changed_plan_applies_nothing():
    plan = _clear_plan([_ev("solo", "owner_organized", 10)])
    pool = _Pool()
    go, parked = _run(plan, pool, plan_digest="stale")

    result = await go()

    assert result["status"] == "refused" and result["reason"] == "plan_changed"
    assert parked == [] and pool.inserted == []


async def test_every_move_parks_a_card_bound_to_the_planned_etag_and_returns_pending():
    plan = _clear_plan(
        [
            _ev("block", "butlers_block", 9),
            _ev("solo", "owner_organized", 10),
            _ev("mtg", "invited", 11, owner_response="accepted"),
        ]
    )
    pool = _Pool()
    go, parked = _run(plan, pool)

    result = await go()

    assert result["status"] == "pending"
    assert [p["etag"] for p in parked] == ["etag-block", "etag-solo"]
    assert [p["authority"] for p in parked] == ["no_park_undo", "owner_submit"]
    assert all("request_id" not in p["call_args"] for p in parked)
    by_ref = {s["event_ref"]: s for s in result["steps"]}
    # the decline parked calendar_respond for the owner; nothing was sent
    assert by_ref["mtg"]["outcome"] == "pending" and by_ref["mtg"]["action"] == "decline"
    (sent,) = go.responded
    assert sent["entry_id"] == "mtg" and sent["response_status"] == "declined"
    assert sent["send_updates"] == "none"
    uuid.UUID(sent["request_id"])
    # one plan row and one receipt per step
    assert len(pool.inserted) == 1 + len(plan.steps)


async def test_a_dependent_step_carries_the_card_it_depends_on():
    plan = _shift_plan(
        [_ev("a", "owner_organized", 9), _ev("b", "owner_organized", 10)], minutes=60
    )
    go, parked = _run(plan, _Pool())

    await go()

    assert parked[0]["depends_on"] == []
    assert len(parked[1]["depends_on"]) == 1  # a lands on b's old slot, so a waits for b's card


async def test_reapplying_replays_receipts_and_parks_only_the_missing_steps():
    plan = _clear_plan([_ev("block", "butlers_block", 9), _ev("solo", "owner_organized", 10)])
    done = {
        "plan_id": plan.plan_id,
        "index": 0,
        "event_ref": "block",
        "action": "move",
        "action_id": str(uuid.uuid4()),
    }
    pool = _Pool(receipts=[done])
    go, parked = _run(plan, pool)

    result = await go()

    assert [p["etag"] for p in parked] == ["etag-solo"]
    assert result["steps"][0]["approval_id"] == done["action_id"]


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"status": "pending", "execution_result": None}, ("pending", None)),
        ({"status": "rejected", "execution_result": None}, ("declined", "card_rejected")),
        ({"status": "expired", "execution_result": None}, ("declined", "card_expired")),
        (
            {
                "status": "executed",
                "execution_result": {"success": True, "result": {"status": "updated"}},
            },
            ("applied", None),
        ),
        (
            {
                "status": "executed",
                "execution_result": {
                    "success": False,
                    "result": {"status": "resolution_declined", "reason": "changed_since_check"},
                },
            },
            ("declined", "changed_since_check"),
        ),
        (None, ("uncertain", "card_missing")),
        (
            {
                "status": "executed",
                "execution_result": {
                    "success": True,
                    "result": {"status": "conflict", "conflicts": [{}]},
                },
            },
            ("declined", "destination_busy"),
        ),
        (
            {
                "status": "executed",
                "execution_result": {"success": True, "result": {"status": "noop"}},
            },
            ("applied", None),
        ),
        (
            {
                "status": "executed",
                "execution_result": {
                    "success": True,
                    "result": {"status": "failed", "reason": "response_provider_rejected"},
                },
            },
            ("declined", "response_provider_rejected"),
        ),
        (
            {
                "status": "executed",
                "execution_result": {"success": False, "result": {"status": "uncertain"}},
            },
            ("uncertain", "execution_not_confirmed"),
        ),
        (
            {"status": "executed", "execution_result": {"success": True, "result": {}}},
            ("uncertain", "execution_not_confirmed"),
        ),
    ],
)
async def test_a_steps_outcome_is_read_off_its_card(row, expected):
    card = str(uuid.uuid4())
    pool = _Pool(cards={uuid.UUID(card): row} if row is not None else {})

    assert await step_outcome(pool, {"action_id": card}) == expected


async def test_dependency_state_separates_waiting_from_failed_and_fails_closed():
    ok = {
        "status": "executed",
        "execution_result": {"success": True, "result": {"status": "updated"}},
    }
    waiting = {"status": "pending", "execution_result": None}
    rejected = {"status": "rejected", "execution_result": None}
    declined = {
        "status": "executed",
        "execution_result": {"success": True, "result": {"status": "resolution_declined"}},
    }

    class P:
        def __init__(self, rows):
            self.rows = rows

        async def fetch(self, sql, ids):
            return self.rows

    one = [str(uuid.uuid4())]
    assert await dependency_state(P([ok]), one) == "applied"
    assert await dependency_state(P([waiting]), one) == "pending"
    assert await dependency_state(P([rejected]), one) == "failed"
    assert await dependency_state(P([declined]), one) == "failed"
    assert await dependency_state(P([]), one) == "failed"  # card missing
    assert await dependency_state(P([ok]), []) == "applied"
    two = one + [str(uuid.uuid4())]
    assert await dependency_state(P([ok, waiting]), two) == "pending"
    assert await dependency_state(P([waiting, rejected]), two) == "failed"


@pytest.mark.parametrize(
    ("state", "reason"),
    [("failed", "dependency_not_applied"), ("pending", "dependency_pending")],
)
async def test_an_approved_step_declines_when_its_dependency_is_not_applied(state, reason):
    import asyncio

    from butlers.modules.approvals.execution_context import (
        ApprovalExecutionContext,
        approval_tool_args_digest,
        reset_approval_execution_context,
        set_approval_execution_context,
    )

    args = {"event_id": "evt", "request_id": "resolve:abc"}
    dep = str(uuid.uuid4())
    offer = {"action_id": "x", "etag": "e1", "depends_on": [dep]}
    module = SimpleNamespace(_db=SimpleNamespace(pool=SimpleNamespace()))
    context = ApprovalExecutionContext(
        action_id=uuid.uuid4(),
        session_id=None,
        actor="owner",
        tool_name="calendar_update_event",
        tool_args_digest=approval_tool_args_digest(args),
        authorized_task=asyncio.current_task(),  # type: ignore[arg-type]
    )
    token = set_approval_execution_context(context)
    try:
        with (
            patch(
                "butlers.modules.calendar.resolution_move.offer_for_card",
                AsyncMock(return_value=offer),
            ),
            patch(
                "butlers.modules.calendar.resolution_move.dependency_state",
                AsyncMock(return_value=state),
            ),
        ):
            check = await check_resolution_binding(
                module,
                tool_name="calendar_update_event",
                tool_args=args,
                request_id="resolve:abc",
                event=SimpleNamespace(etag="e1"),  # type: ignore[arg-type]
            )
    finally:
        reset_approval_execution_context(token)

    assert check.refusal == reason and check.bound


async def test_a_response_that_cannot_be_parked_is_skipped_with_the_reason_not_dropped():
    plan = _clear_plan([_ev("mtg", "invited", 11, owner_response="accepted")])
    go, _parked = _run(plan, _Pool(), respond_override={"error": "response_capability_unavailable"})

    result = await go()

    assert result["steps"][0]["outcome"] == "skipped"
    assert result["steps"][0]["reason"] == "response_capability_unavailable"


def _approved_card(command):
    """The real shape of a response whose handler raised: card ``approved``, no execution result,
    and the coordinator's own command row holding how it ended."""
    card = uuid.uuid4()
    pool = _Pool(
        cards={card: {"status": "approved", "execution_result": None}},
        commands={card: command} if command is not None else {},
    )
    return card, pool


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            {
                "action_status": "failed",
                "action_result": {"status": "failed", "reason": "response_version_changed"},
                "phase": "terminal",
            },
            ("declined", "response_version_changed"),
        ),
        (
            {
                "action_status": "failed",
                "action_result": {"status": "failed", "reason": "response_provider_rejected"},
                "phase": "terminal",
            },
            ("declined", "response_provider_rejected"),
        ),
        (
            {"action_status": "noop", "action_result": {"status": "noop"}, "phase": "terminal"},
            ("applied", None),
        ),
        (
            {"action_status": "pending", "action_result": None, "phase": "uncertain"},
            ("uncertain", "response_uncertain"),
        ),
        (
            {"action_status": "pending", "action_result": None, "phase": "egress_started"},
            ("pending", None),
        ),
        (None, ("pending", None)),
    ],
)
async def test_an_approved_response_the_handler_never_finished_is_read_off_its_command_row(
    command, expected
):
    card, pool = _approved_card(command)

    assert await step_outcome(pool, {"action_id": str(card)}) == expected


async def test_a_dependency_on_a_failed_response_command_is_failed_not_pending():
    card, pool = _approved_card(
        {
            "action_status": "failed",
            "action_result": {"status": "failed", "reason": "response_version_changed"},
            "phase": "terminal",
        }
    )

    class P(_Pool):
        async def fetch(self, sql, *args):
            return [{"id": card, **self.cards[card]}]

    pool.__class__ = P
    assert await dependency_state(pool, [str(card)]) == "failed"


async def test_an_approved_move_whose_handler_errored_is_uncertain_not_pending_forever():
    # calendar_update_event returned an error dict: the executor leaves the card approved with no
    # result and audits action_execution_failed; a move has no coordinator command row.
    card = uuid.uuid4()
    pool = _Pool(cards={card: {"status": "approved", "execution_result": None}})
    pool.failures[card] = "calendar_update_event failed"

    assert await step_outcome(pool, {"action_id": str(card)}) == ("uncertain", "execution_failed")

    class P(_Pool):
        async def fetch(self, sql, *args):
            return [{"id": card, **self.cards[card]}]

    pool.__class__ = P
    assert await dependency_state(pool, [str(card)]) == "failed"


async def test_an_approved_move_with_no_failure_audit_stays_pending():
    card = uuid.uuid4()
    pool = _Pool(cards={card: {"status": "approved", "execution_result": None}})

    assert await step_outcome(pool, {"action_id": str(card)}) == ("pending", None)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (DEFERRED_ERROR, ("pending", None)),
        (
            RESOLUTION_DECLINED_TEXTS["dependency_not_applied"],
            ("declined", "dependency_not_applied"),
        ),
        (RESOLUTION_DECLINED_TEXTS["changed_since_check"], ("declined", "changed_since_check")),
        ("something unexpected", ("uncertain", "execution_failed")),
        ("", ("uncertain", "execution_failed")),
    ],
)
async def test_a_deferred_move_reads_pending_never_a_failure(reason, expected):
    card = uuid.uuid4()
    pool = _Pool(cards={card: {"status": "approved", "execution_result": None}})
    pool.failures[card] = reason

    assert await step_outcome(pool, {"action_id": str(card)}) == expected
