"""Real-Postgres coverage for approving an origin='prepared' pending action.

execute_approved_action is the universal executor seam every approval path
replays through (module.py's manual approve/dispatch, gate.py's auto-approve).
This exercises it directly against a real origin='prepared' row: approval must
replay the exact stored tool_args and land execution_result -- no different
from an ordinary gated action's approval, which is the point (prepared actions
need no bespoke execution path, only a bespoke, push-free park path).
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from butlers.core.attention_ledger import record_attention_event
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.park import park_prepared_action

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture
async def approvals_pool(db):
    """The production core + approvals migration chains on a fresh clone."""
    return await db("core", "approvals")


async def _park_and_approve(
    pool, *, action_id: uuid.UUID, tool_args: dict, tool_name: str = "notify"
) -> None:
    now = datetime.now(UTC)
    await park_prepared_action(
        pool,
        action_id=action_id,
        tool_name=tool_name,
        tool_args=tool_args,
        agent_summary="Prepared reach-out draft",
        requested_at=now,
        expires_at=now + timedelta(days=3),
        origin_butler="relationship",
        deduplication_key=f"relationship:prepared-reach-out:{action_id}",
    )
    await pool.execute(
        "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 WHERE id = $4",
        ActionStatus.APPROVED.value,
        "human:owner",
        now,
        action_id,
    )


async def test_approve_replays_exact_stored_args_and_writes_execution_result(
    approvals_pool,
) -> None:
    action_id = uuid.uuid4()
    entity_id = str(uuid.uuid4())
    tool_args = {"entity_id": entity_id, "message": "It's been a while!", "intent": "send"}
    await _park_and_approve(approvals_pool, action_id=action_id, tool_args=tool_args)

    tool_fn = AsyncMock(return_value={"status": "sent"})

    result = await execute_approved_action(
        pool=approvals_pool,
        action_id=action_id,
        tool_name="notify",
        tool_args=tool_args,
        tool_fn=tool_fn,
        origin_butler="relationship",
    )

    assert result.success is True
    tool_fn.assert_awaited_once_with(**tool_args)

    row = await approvals_pool.fetchrow(
        "SELECT status, execution_result FROM pending_actions WHERE id = $1", action_id
    )
    assert row["status"] == ActionStatus.EXECUTED.value
    assert row["execution_result"]["success"] is True
    assert row["execution_result"]["result"] == {"status": "sent"}


async def test_legacy_calendar_marker_retires_only_after_original_binding(approvals_pool) -> None:
    from butlers.modules.approvals.execution_context import get_approval_execution_context
    from butlers.modules.calendar_approval import calendar_approval_args

    tool_name = "calendar_toggle_butler_event"
    for extra, admitted in (
        ({"_approval_bypass": True}, True),
        ({"_approval_bypass": False}, False),
        ({"_approval_bypass": 1}, False),
        ({"_approval_bypass": "true"}, False),
        ({"_approval_bypass": None}, False),
        ({"_approval_bypass": True, "unknown": "value"}, False),
        ({"unknown": "value"}, False),
    ):
        action_id = uuid.uuid4()
        stored = {"event_id": "event", "enabled": False, **extra}
        await _park_and_approve(
            approvals_pool, action_id=action_id, tool_args=stored, tool_name=tool_name
        )
        calls = []

        async def handler(event_id, enabled, source_hint=None, request_id=None, calls=calls):
            args = calendar_approval_args(
                tool_name,
                event_id=event_id,
                enabled=enabled,
                source_hint=source_hint,
                request_id=request_id,
            )
            assert get_approval_execution_context(tool_name=tool_name, tool_args=args) is not None
            calls.append(args)
            return {"status": "updated"}

        if admitted:
            altered = await execute_approved_action(
                approvals_pool, action_id, tool_name, {**stored, "enabled": True}, handler
            )
            assert not altered.success and calls == []
        outcome = await execute_approved_action(
            approvals_pool, action_id, tool_name, stored, handler
        )
        assert outcome.success is admitted
        if admitted:
            repeated = await execute_approved_action(
                approvals_pool, action_id, tool_name, stored, handler
            )
            assert repeated.to_dict() == outcome.to_dict()
            assert len(calls) == 1 and "_approval_bypass" not in calls[0]
        else:
            assert calls == []
        row = await approvals_pool.fetchrow(
            "SELECT status, tool_args FROM pending_actions WHERE id=$1", action_id
        )
        assert row["status"] == ("executed" if admitted else "approved")
        assert row["tool_args"] == stored


async def test_execution_failure_on_a_prepared_action_records_attention_ledger_row(
    approvals_pool,
) -> None:
    """A prepared action has no approval_rule_id, so demotion never fires --
    the attention-ledger row is the analogous failure hook (bu-2jtfw.11)."""
    action_id = uuid.uuid4()
    tool_args = {"entity_id": str(uuid.uuid4()), "message": "hi", "intent": "send"}
    await _park_and_approve(approvals_pool, action_id=action_id, tool_args=tool_args)

    async def _failing_tool(**_kwargs: object) -> dict:
        raise RuntimeError("delivery channel unavailable")

    result = await execute_approved_action(
        pool=approvals_pool,
        action_id=action_id,
        tool_name="notify",
        tool_args=tool_args,
        tool_fn=_failing_tool,
        origin_butler="relationship",
    )

    assert result.success is False

    ledger_row = await approvals_pool.fetchrow(
        "SELECT source, outcome, origin_butler, notification_ref, reason "
        "FROM public.attention_ledger WHERE notification_ref = $1",
        str(action_id),
    )
    assert ledger_row is not None
    assert ledger_row["source"] == "approvals"
    assert ledger_row["outcome"] == "failed"
    assert ledger_row["origin_butler"] == "relationship"
    # An outbound send's failure is recorded content-free: its closed code, never the raw
    # exception text (bu-dhloi1.184). An unclassified failure is the generic code.
    assert ledger_row["reason"] == "send_failed"

    # Sanity: record_attention_event itself accepts the new source value
    # (guards against a future VALID_SOURCES regression silently dropping rows).
    written_id = await record_attention_event(
        approvals_pool, origin_butler="relationship", source="approvals", outcome="failed"
    )
    assert written_id is not None
