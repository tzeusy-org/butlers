"""Real-Postgres coverage for plan apply, receipts, resume and undo (bu-5ii4gj.11 P2a)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from butlers.core.temporal.change_plans import BusyInterval, compile_plan
from butlers.core.temporal.conflict_resolution import ResolutionEvent
from butlers.modules.calendar.plan_apply import apply_plan
from butlers.modules.calendar.plan_tools import CompiledPlan
from butlers.modules.calendar.plan_undo import undo_plan
from butlers.modules.calendar.resolution_move import dependency_state, record_applied_move

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not shutil.which("docker"), reason="Docker not available"),
]

DAY = datetime(2026, 12, 10, 0, 0, tzinfo=UTC)
NOW = DAY - timedelta(days=1)
WINDOW = (DAY, DAY + timedelta(days=1))
EXECUTED = json.dumps({"success": True, "result": {"status": "updated"}})


def _ev(entry, hour):
    start = DAY + timedelta(hours=hour)
    return ResolutionEvent(
        entry_id=entry,
        provider_event_id=f"prov-{entry}",
        calendar_id="primary",
        start_at=start,
        end_at=start + timedelta(hours=1),
        etag=f"etag-{entry}",
        event_class="owner_organized",
    )


def _shift_plan():
    events = [_ev("a", 9), _ev("b", 10)]  # a lands on b's old slot, so a depends on b
    return compile_plan(
        "shift_window",
        WINDOW,
        events,
        now=NOW,
        delta=timedelta(minutes=60),
        busy=[BusyInterval(e.entry_id, e.start_at, e.end_at) for e in events],
    )


async def _apply(pool, plan):
    compiled = CompiledPlan(plan, WINDOW, "UTC")
    with (
        patch(
            "butlers.modules.calendar.plan_apply.compile_for_window",
            AsyncMock(return_value=compiled),
        ),
        patch("butlers.modules.calendar.plan_apply.pool_of", return_value=pool),
    ):
        return await apply_plan(
            SimpleNamespace(),
            "general",
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            kind="shift_window",
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            shift_minutes=60,
        )


def _obj(value):
    return json.loads(value) if isinstance(value, str) else value


async def test_apply_writes_a_plan_row_a_step_row_and_a_card_per_move(db) -> None:
    pool = await db("core", "approvals")
    plan = _shift_plan()

    result = await _apply(pool, plan)

    assert result["status"] == "pending" and len(result["steps"]) == 2
    kinds = await pool.fetch(
        "SELECT action_type, count(*) AS n FROM calendar_action_log GROUP BY action_type"
    )
    counts = {r["action_type"]: r["n"] for r in kinds}
    assert counts["plan"] == 1 and counts["plan_step"] == 2 and counts["resolution_move"] == 2
    assert await pool.fetchval("SELECT count(*) FROM pending_actions") == 2
    receipts = await pool.fetch(
        "SELECT action_payload FROM calendar_action_log WHERE action_type = 'plan_step' "
        "ORDER BY (action_payload->>'index')::int"
    )
    first, second = (_obj(r["action_payload"]) for r in receipts)
    assert first["event_ref"] == "b" and first["depends_on"] == []
    assert second["event_ref"] == "a" and second["depends_on"] == [0]
    binding = _obj(
        await pool.fetchval(
            "SELECT action_result FROM calendar_action_log WHERE action_type = 'resolution_move' "
            "AND action_result->>'action_id' = $1",
            second["action_id"],
        )
    )
    assert binding["depends_on"] == [first["action_id"]]


async def test_reapplying_replays_the_receipts_and_parks_nothing_twice(db) -> None:
    pool = await db("core", "approvals")
    plan = _shift_plan()
    first = await _apply(pool, plan)

    again = await _apply(pool, plan)

    assert [s["approval_id"] for s in again["steps"]] == [s["approval_id"] for s in first["steps"]]
    assert await pool.fetchval("SELECT count(*) FROM pending_actions") == 2
    steps = await pool.fetchval(
        "SELECT count(*) FROM calendar_action_log WHERE action_type = 'plan_step'"
    )
    assert steps == 2


async def test_dependency_state_reads_real_card_statuses(db) -> None:
    pool = await db("core", "approvals")
    result = await _apply(pool, _shift_plan())
    dep = UUID(result["steps"][0]["approval_id"])

    assert await dependency_state(pool, [str(dep)]) == "pending"
    await pool.execute("UPDATE pending_actions SET status = 'rejected' WHERE id = $1", dep)
    assert await dependency_state(pool, [str(dep)]) == "failed"
    await pool.execute(
        "UPDATE pending_actions SET status = 'executed', execution_result = $2::jsonb "
        "WHERE id = $1",
        dep,
        EXECUTED,
    )
    assert await dependency_state(pool, [str(dep)]) == "applied"


async def test_a_move_the_executor_failed_on_reads_uncertain_from_the_real_audit_row(db) -> None:
    from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
    from butlers.modules.calendar.plan_apply import step_outcome

    pool = await db("core", "approvals")
    result = await _apply(pool, _shift_plan())
    card = UUID(result["steps"][0]["approval_id"])
    await pool.execute("UPDATE pending_actions SET status = 'approved' WHERE id = $1", card)
    receipt = {"action_id": str(card)}
    assert await step_outcome(pool, receipt) == ("pending", None)

    await record_approval_event(
        pool,
        ApprovalEventType.ACTION_EXECUTION_FAILED,
        actor="system:executor",
        action_id=card,
        reason="calendar_update_event failed",
        metadata={"tool_name": "calendar_update_event"},
    )

    assert await step_outcome(pool, receipt) == ("uncertain", "execution_failed")
    assert await dependency_state(pool, [str(card)]) == "failed"


async def test_undo_parks_one_bound_card_per_applied_step_only(db) -> None:
    pool = await db("core", "approvals")
    plan = _shift_plan()
    result = await _apply(pool, plan)
    applied = UUID(result["steps"][0]["approval_id"])
    await pool.execute(
        "UPDATE pending_actions SET status = 'executed', execution_result = $2::jsonb "
        "WHERE id = $1",
        applied,
        EXECUTED,
    )
    await record_applied_move(
        pool,
        applied,
        etag_after="etag-after",
        pre_start="2026-12-10T10:00:00+00:00",
        pre_end="2026-12-10T11:00:00+00:00",
    )

    with (
        patch("butlers.modules.calendar.plan_undo.pool_of", return_value=pool),
        patch(
            "butlers.modules.calendar.plan_undo.load_configured_general_timezone",
            AsyncMock(return_value="UTC"),
        ),
    ):
        undone = await undo_plan(SimpleNamespace(), "general", plan_id=plan.plan_id, now=NOW)
        again = await undo_plan(SimpleNamespace(), "general", plan_id=plan.plan_id, now=NOW)

    by_index = {r["index"]: r for r in undone["reversals"]}
    assert by_index[0]["approval_id"] and by_index[1]["reason"] == "step_pending"
    assert again["reversals"][0]["approval_id"] == by_index[0]["approval_id"]  # replay, not new
    binding = _obj(
        await pool.fetchval(
            "SELECT action_result FROM calendar_action_log WHERE action_type = 'resolution_move' "
            "AND action_result->>'action_id' = $1",
            by_index[0]["approval_id"],
        )
    )
    assert binding["etag"] == "etag-after" and binding["stale_reason"] == "changed_since_move"


# ---- deferral (bu-5ii4gj.11.1) ---------------------------------------------------------------


def _card_event(offer: dict, tool_args: dict):
    from butlers.modules.calendar.models import CalendarEvent

    return CalendarEvent(
        event_id=tool_args.get("event_id", "e"),
        title="t",
        start_at=DAY,
        end_at=DAY + timedelta(hours=1),
        timezone="UTC",
        etag=offer["etag"],
    )


async def _executor(pool):
    """The approvals executor around a stand-in for the calendar update handler.

    The handler runs the real move gate against the real card and bindings, then answers the way
    ``calendar_update_event`` does: a deferral and a decline are error dicts, a write is ``updated``.
    """
    from butlers.modules.approvals.executor import execute_approved_action
    from butlers.modules.calendar.resolution_move import (
        DEFERRED_ERROR,
        RESOLUTION_DECLINED_TEXTS,
        check_resolution_binding,
        offer_for_card,
    )

    module = SimpleNamespace(_db=SimpleNamespace(pool=pool))

    async def execute(card_id) -> bool:
        row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", card_id)
        if row is None or row["status"] != "approved" or row["execution_result"] is not None:
            return False
        args = _obj(row["tool_args"])
        offer = await offer_for_card(pool, card_id)

        async def tool(**kwargs):
            check = await check_resolution_binding(
                module,
                tool_name="calendar_update_event",
                tool_args=args,
                request_id=args.get("request_id"),
                event=_card_event(offer, args),
            )
            if check.refusal == "dependency_pending":
                return {"error": DEFERRED_ERROR}
            if check.refusal:
                return {"error": RESOLUTION_DECLINED_TEXTS[check.refusal]}
            return {"status": "updated"}

        await execute_approved_action(pool, card_id, row["tool_name"], args, tool)
        return True

    return execute


async def _approve(pool, card) -> None:
    await pool.execute("UPDATE pending_actions SET status = 'approved' WHERE id = $1", card)


async def _outcome(pool, card):
    from butlers.modules.calendar.plan_apply import step_outcome

    return await step_outcome(pool, {"action_id": str(card)})


async def test_approving_the_dependency_then_the_dependent_applies_both_with_no_reapply(db) -> None:
    from butlers.modules.calendar.resolution_move import release_deferred

    pool = await db("core", "approvals")
    result = await _apply(pool, _shift_plan())
    dep, dependent = (UUID(s["approval_id"]) for s in result["steps"])
    execute = await _executor(pool)

    # the owner approves the dependent first: it is deferred, not failed or declined
    await _approve(pool, dependent)
    await execute(dependent)
    assert await _outcome(pool, dependent) == ("pending", None)
    assert await release_deferred(pool, execute) == 0  # the dependency is still undecided

    await _approve(pool, dep)
    await execute(dep)
    assert (await _outcome(pool, dep))[0] == "applied"

    assert await release_deferred(pool, execute, after=str(dep)) == 1
    assert (await _outcome(pool, dependent))[0] == "applied"
    assert await release_deferred(pool, execute) == 0  # nothing left to release


async def test_a_rejected_dependency_declines_the_deferred_dependent(db) -> None:
    from butlers.modules.calendar.resolution_move import release_deferred

    pool = await db("core", "approvals")
    result = await _apply(pool, _shift_plan())
    dep, dependent = (UUID(s["approval_id"]) for s in result["steps"])
    execute = await _executor(pool)
    await _approve(pool, dependent)
    await execute(dependent)

    await pool.execute("UPDATE pending_actions SET status = 'rejected' WHERE id = $1", dep)
    assert await release_deferred(pool, execute) == 1

    assert await _outcome(pool, dependent) == ("declined", "dependency_not_applied")
    assert await release_deferred(pool, execute) == 0  # ended for good, never re-run


async def test_a_dependent_that_waited_its_whole_lifetime_declines(db) -> None:
    from butlers.modules.calendar.resolution_move import release_deferred

    pool = await db("core", "approvals")
    result = await _apply(pool, _shift_plan())
    dep, dependent = (UUID(s["approval_id"]) for s in result["steps"])
    execute = await _executor(pool)
    await _approve(pool, dependent)
    await execute(dependent)
    assert await release_deferred(pool, execute) == 0

    await pool.execute(
        "UPDATE pending_actions SET requested_at = now() - interval '49 hours' WHERE id = $1",
        dependent,
    )
    assert await release_deferred(pool, execute) == 1

    assert await _outcome(pool, dependent) == ("declined", "dependency_not_applied")


async def test_the_sweep_never_touches_a_card_the_owner_did_not_approve(db) -> None:
    from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
    from butlers.modules.calendar.resolution_move import DEFERRED_ERROR, release_deferred

    pool = await db("core", "approvals")
    result = await _apply(pool, _shift_plan())
    dep, dependent = (UUID(s["approval_id"]) for s in result["steps"])
    await pool.execute("UPDATE pending_actions SET status = 'executed' WHERE id = $1", dep)
    called: list = []

    async def execute(card_id) -> bool:
        called.append(card_id)
        return True

    # an approved card that was never deferred (no failure audit) is not this sweep's either
    await pool.execute("UPDATE pending_actions SET status = 'approved' WHERE id = $1", dependent)
    assert await release_deferred(pool, execute) == 0
    for status in ("rejected", "expired", "pending"):
        await pool.execute(
            "UPDATE pending_actions SET status = $2 WHERE id = $1", dependent, status
        )
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_EXECUTION_FAILED,
            actor="system:executor",
            action_id=dependent,
            reason=DEFERRED_ERROR,
            metadata={"tool_name": "calendar_update_event"},
        )
        assert await release_deferred(pool, execute) == 0
    assert called == []


async def test_execute_approved_card_runs_a_deferred_step_through_the_real_executor(db) -> None:
    from butlers.modules.approvals.module import ApprovalsModule
    from butlers.modules.calendar.resolution_move import (
        DEFERRED_ERROR,
        RESOLUTION_DECLINED_TEXTS,
        check_resolution_binding,
        offer_for_card,
        release_deferred,
    )

    pool = await db("core", "approvals")
    result = await _apply(pool, _shift_plan())
    dep, dependent = (UUID(s["approval_id"]) for s in result["steps"])
    module_stub = SimpleNamespace(_db=SimpleNamespace(pool=pool))

    # the approvals module's own executor callback: the stand-in calendar_update_event handler
    async def tool_executor(tool_name, args):
        card = await pool.fetchval(
            "SELECT id FROM pending_actions WHERE tool_name = $1 AND tool_args @> $2::jsonb "
            "AND status = 'approved' AND execution_result IS NULL ORDER BY requested_at LIMIT 1",
            tool_name,
            {k: args[k] for k in ("request_id",) if k in args},
        )
        offer = await offer_for_card(pool, card)
        check = await check_resolution_binding(
            module_stub,
            tool_name="calendar_update_event",
            tool_args=args,
            request_id=args.get("request_id"),
            event=_card_event(offer, args),
        )
        if check.refusal == "dependency_pending":
            return {"error": DEFERRED_ERROR}
        if check.refusal:
            return {"error": RESOLUTION_DECLINED_TEXTS[check.refusal]}
        return {"status": "updated"}

    approvals = ApprovalsModule()
    approvals._db = pool
    approvals._butler_name = "general"
    approvals.set_tool_executor(tool_executor)
    owned = frozenset({"calendar_update_event"})

    await _approve(pool, dependent)
    # a caller that does not own calendar_update_event cannot re-run it, and nothing runs
    assert (
        await approvals.execute_approved_card(dependent, tool_names=frozenset({"other"})) is False
    )
    assert await _outcome(pool, dependent) == ("pending", None)

    assert await approvals.execute_approved_card(dependent, tool_names=owned) is True
    assert await _outcome(pool, dependent) == ("pending", None)  # deferred, not failed

    await _approve(pool, dep)
    await approvals.execute_approved_card(dep, tool_names=owned)

    async def execute(card_id) -> bool:
        return await approvals.execute_approved_card(card_id, tool_names=owned)

    assert await release_deferred(pool, execute, after=str(dep)) == 1
    assert (await _outcome(pool, dependent))[0] == "applied"
