"""REQ-module-calendar-030: owner self-submissions cannot teach delegated autonomy.

Migrated PostgreSQL approval/history writes with a separate committed observer;
the final case also exercises the real executor with a synthetic provider.
No live OAuth or external provider execution is claimed.
"""

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from butlers.modules.approvals.operations import approve_action
from butlers.testing.db import open_pool

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
async def approval_databases(fresh_db):
    url = await asyncio.to_thread(fresh_db, "core", "approvals")
    async with AsyncExitStack() as cleanup:
        pools = []
        for _ in range(2):
            pool = await open_pool(url, max_size=2)
            cleanup.push_async_callback(pool.close)
            pools.append(pool)
        owner, observer = pools
        async with owner.acquire() as a, observer.acquire() as b:
            assert await a.fetchval("SELECT pg_backend_pid()") != await b.fetchval(
                "SELECT pg_backend_pid()"
            )
        yield owner, observer


@pytest.mark.parametrize("eligible", [False, True])
async def test_self_submission_has_no_promotion_history(approval_databases, eligible):
    owner, observer = approval_databases
    action_id = uuid4()
    now = datetime.now(UTC)
    await owner.execute(
        "INSERT INTO pending_actions (id,tool_name,tool_args,status,requested_at,expires_at) "
        "VALUES ($1,'calendar_create_event',$2,'pending',$3,$4)",
        action_id,
        {"title": "Synthetic meeting", "attendees": ["guest@example.test"]},
        now,
        now + timedelta(hours=1),
    )
    result = await approve_action(owner, str(action_id), promotion_eligible=eligible)
    assert result["status"] == "approved"
    assert (
        await observer.fetchval("SELECT status FROM pending_actions WHERE id=$1", action_id)
        == "approved"
    )
    count = await observer.fetchval(
        "SELECT count(*) FROM autonomy_approval_history WHERE action_id=$1", action_id
    )
    assert count == int(eligible)
    assert (
        await observer.fetchval(
            "SELECT count(*) FROM approval_rules WHERE created_from=$1", action_id
        )
        == 0
    )


@pytest.mark.parametrize("changed", [False, True])
async def test_calendar_owning_decision_locks_exact_call_and_excludes_history(
    approval_databases, monkeypatch, changed
):
    from fastapi import HTTPException

    from butlers.api.routers import approvals as routes

    owner, observer = approval_databases
    action_id = uuid4()
    now = datetime.now(UTC)
    expected = {
        "title": "Exact owner submission",
        "calendar_id": "owner-calendar",
        "attendees": ["guest@example.test"],
        "request_id": "native-create",
    }
    stored = {**expected, "title": "Unreviewed replacement"} if changed else expected
    await owner.execute(
        "INSERT INTO pending_actions (id,tool_name,tool_args,status,requested_at,expires_at) "
        "VALUES ($1,'calendar_create_event',$2,'pending',$3,$4)",
        action_id,
        stored,
        now,
        now + timedelta(hours=1),
    )
    from unittest.mock import AsyncMock

    dispatch = AsyncMock(return_value=routes._DispatchOutcome(kind="unreachable"))
    monkeypatch.setattr(routes, "_dispatch_approved_action_outcome", dispatch)

    async def decide():
        return await routes.approve_owning_action(
            parsed_id=action_id,
            actor_id="owner:test",
            edits=None,
            action_butler="general",
            target_pool=owner,
            db_mgr=object(),
            mcp_mgr=object(),
            expected_tool_args=expected,
            expected_tool_name="calendar_create_event",
            promotion_eligible=False,
        )

    if changed:
        with pytest.raises(HTTPException) as error:
            await decide()
        assert error.value.status_code == 409
        dispatch.assert_not_awaited()
    else:
        result = await decide()
        assert result.data.status == "approved"
        assert result.data.dispatched is False
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[5] == expected
    assert await observer.fetchval("SELECT status FROM pending_actions WHERE id=$1", action_id) == (
        "pending" if changed else "approved"
    )
    assert (
        await observer.fetchval("SELECT tool_args FROM pending_actions WHERE id=$1", action_id)
        == stored
    )
    assert (
        await observer.fetchval(
            "SELECT count(*) FROM autonomy_approval_history WHERE action_id=$1", action_id
        )
        == 0
    )


@pytest.mark.parametrize("request_identifier", [None, "native-party"])
async def test_producer_only_queue_decision_and_exact_executor(
    approval_databases, monkeypatch, request_identifier
):
    """Real SQL projection/queue/decision/executor; provider effect is synthetic."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastmcp import FastMCP

    from butlers.api.routers import approvals as routes
    from butlers.daemon import ButlerDaemon
    from butlers.modules.approvals import ApprovalsModule
    from butlers.modules.approvals.execution_context import get_approval_execution_context
    from butlers.modules.approvals.executor import execute_approved_action
    from butlers.modules.approvals.models import PendingAction
    from butlers.modules.calendar import CalendarModule
    from butlers.testing.mcp import registered_tools
    from tests.modules.test_module_calendar import _make_event, _ProviderDouble

    owner, observer = approval_databases
    module = CalendarModule()
    module._verified_response_account = AsyncMock(
        return_value={"entity_id": str(UUID(int=9)), "email": "owner@example.test"}
    )
    provider = _ProviderDouble(event=_make_event())
    module._provider = provider
    module._resolved_calendar_id = "synthetic-calendar"
    mcp = FastMCP("native-calendar-party-approval")
    await module.register_tools(
        mcp,
        {"provider": "google", "calendar_id": "synthetic-calendar"},
        SimpleNamespace(pool=owner),
        "general",
    )
    # Use real migrated projection receipts as well as queue/decision/executor SQL:
    # approved replay must recover its owning key even without a request ID.
    module._resolve_action_source_id = AsyncMock(return_value=None)
    module._project_provider_mutation = AsyncMock()
    module._emit_calendar_audit = AsyncMock()
    tool = (await registered_tools(mcp))["calendar_create_event"]
    approvals = ApprovalsModule()

    async def transport(name, arguments):
        assert name == "calendar_create_event"
        assert get_approval_execution_context(tool_name=name, tool_args=arguments) is not None
        return await tool(**arguments)

    approvals.set_tool_executor(transport)
    daemon = object.__new__(ButlerDaemon)
    daemon.config = SimpleNamespace(
        name="general", modules={"approvals": {"enabled": False, "domain_producers": ["calendar"]}}
    )
    daemon.db = SimpleNamespace(pool=owner)
    daemon._modules = [module, approvals]
    daemon._module_statuses = {}
    daemon._approval_push_runtime = None
    daemon._wire_calendar_approval_enqueuer()
    now = datetime(2026, 2, 20, 14, tzinfo=UTC)
    pending = await tool(
        title="Native guest meeting",
        start_at=now,
        end_at=now + timedelta(hours=1),
        attendees=["guest@example.test"],
        request_id=request_identifier,
    )
    assert pending["status"] == "approval_required"
    assert provider.create_calls == []
    action_id = UUID(pending["action_id"])
    parked = await observer.fetchrow(
        "SELECT tool_name,tool_args,status FROM pending_actions WHERE id=$1", action_id
    )
    assert parked["status"] == "pending"
    assert isinstance(parked["tool_args"], dict)
    assert parked["tool_args"]["calendar_id"] == "synthetic-calendar"
    assert parked["tool_args"]["attendees"] == ["guest@example.test"]
    receipt = await observer.fetchrow(
        "SELECT idempotency_key,action_result FROM calendar_action_log "
        "WHERE action_result->>'action_id'=$1",
        str(action_id),
    )
    assert receipt is not None
    assert receipt["action_result"]["party_audience_digest"]

    async def dispatch(_mgr, _db, pool, identifier, name, arguments, butler):
        assert pool is owner and butler == "general"
        outcome = await execute_approved_action(
            pool=pool,
            action_id=UUID(identifier),
            tool_name=name,
            tool_args=arguments,
            tool_fn=lambda **kw: transport(name, kw),
            origin_butler=butler,
        )
        assert outcome.success
        row = await observer.fetchrow("SELECT * FROM pending_actions WHERE id=$1", action_id)
        return routes._DispatchOutcome(
            kind="executed", action=PendingAction.from_row(row).to_dict()
        )

    monkeypatch.setattr(routes, "_dispatch_approved_action_outcome", dispatch)
    decision = await routes.approve_owning_action(
        parsed_id=action_id,
        actor_id="owner:test",
        edits=None,
        action_butler="general",
        target_pool=owner,
        db_mgr=object(),
        mcp_mgr=object(),
        expected_tool_args=parked["tool_args"],
        expected_tool_name="calendar_create_event",
        promotion_eligible=False,
    )
    assert decision.data.status == "executed"
    assert decision.data.dispatched is True
    assert (
        await observer.fetchval(
            "SELECT action_status FROM calendar_action_log WHERE idempotency_key=$1",
            receipt["idempotency_key"],
        )
        == "applied"
    )
    assert len(provider.create_calls) == 1
    assert (
        await observer.fetchval("SELECT status FROM pending_actions WHERE id=$1", action_id)
        == "executed"
    )
    assert (
        await observer.fetchval(
            "SELECT count(*) FROM autonomy_approval_history WHERE action_id=$1", action_id
        )
        == 0
    )

    repeated = await execute_approved_action(
        pool=owner,
        action_id=action_id,
        tool_name="calendar_create_event",
        tool_args=parked["tool_args"],
        tool_fn=lambda **kw: transport("calendar_create_event", kw),
        origin_butler="general",
    )
    assert repeated.success
    assert len(provider.create_calls) == 1
    assert (
        await observer.fetchval(
            "SELECT action_status FROM calendar_action_log WHERE idempotency_key=$1",
            receipt["idempotency_key"],
        )
        == "applied"
    )
