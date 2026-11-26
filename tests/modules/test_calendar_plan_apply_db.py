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
        undone = await undo_plan(SimpleNamespace(), "general", plan_id=plan.plan_id)
        again = await undo_plan(SimpleNamespace(), "general", plan_id=plan.plan_id)

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
