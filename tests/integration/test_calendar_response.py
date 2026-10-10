# Spec: REQ-module-calendar-029, REQ-dashboard-api-069, REQ-dashboard-api-070
"""Owning migrated approval/command transactions with controlled provider HTTP.

This test uses the production core/approvals chains and runtime role on every
pool acquisition. Software fixtures cannot prove these locks or the independent
commit of a write-start marker when the executor transaction is cancelled.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import httpx
import pytest
from fastmcp import Client, FastMCP

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_mcp_manager
from butlers.api.routers import calendar_workspace as workspace
from butlers.core.approvals_hooks import DecisionDossier
from butlers.daemon import ButlerDaemon
from butlers.db import register_jsonb_codec
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.module import ApprovalsModule
from butlers.modules.calendar import (
    CalendarModule,
    CalendarRequestError,
    _google_event_to_calendar_event,
)
from butlers.modules.calendar_response import CalendarResponseError, reserve_response_inverse
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
async def response_pool(postgres_container):
    db_url = await asyncio.to_thread(
        create_migrated_test_db,
        postgres_container,
        migration_db_name(),
        ["core", "approvals"],
        {"core": "messenger", "approvals": "messenger"},
    )

    async def setup(connection):
        await connection.execute('SET ROLE "butler_messenger_rw"')
        await connection.execute('SET search_path TO "messenger", public')
        identity = await connection.fetchrow(
            "SELECT current_user AS role,current_schema() AS schema"
        )
        assert identity["role"] == "butler_messenger_rw" and identity["schema"] == "messenger"

    pool = await asyncpg.create_pool(
        db_url, min_size=1, max_size=6, init=register_jsonb_codec, setup=setup
    )
    try:
        yield pool
    finally:
        await pool.close()


async def test_migrated_response_atomic_park_committed_attempt_and_guarded_inverse(
    response_pool, monkeypatch
):
    pool = response_pool
    module = CalendarModule()
    mcp = FastMCP("migrated-calendar-response")
    await module.register_tools(
        mcp,
        {"provider": "google", "response_enabled": True},
        SimpleNamespace(pool=pool),
        "messenger",
    )
    module.set_response_approval_ready(True)
    account = {"entity_id": str(uuid.uuid4()), "email": "owner@example.test"}
    module._verified_response_account = AsyncMock(return_value=account)
    source_id = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key,source_kind,lane,provider,calendar_id,writable,metadata) "
        "VALUES ('provider:google:response','provider_event','user','google','owner@example.test',true,$1) RETURNING id",
        {"account_email": "owner@example.test"},
    )
    resource = {
        "id": "exact-occurrence",
        "status": "confirmed",
        "etag": '"before"',
        "summary": "Controlled invitation",
        "start": {"dateTime": "2026-10-11T10:00:00Z"},
        "end": {"dateTime": "2026-10-11T11:00:00Z"},
        "recurringEventId": "exact-series",
        "originalStartTime": {"dateTime": "2026-10-11T10:00:00Z"},
        "organizer": {"email": "organizer@example.test"},
        "attendees": [
            {"email": "owner@example.test", "self": True, "responseStatus": "needsAction"},
            {"email": "other@example.test", "responseStatus": "accepted"},
        ],
    }
    projected = _google_event_to_calendar_event(resource, fallback_timezone="UTC")
    assert projected is not None
    await module._project_provider_changes(
        source_id=source_id,
        provider_name="google",
        calendar_id="owner@example.test",
        updated_events=[projected],
        cancelled_ids=[],
    )
    entry_id = await pool.fetchval(
        "SELECT id FROM calendar_event_instances WHERE source_id=$1", source_id
    )
    started = asyncio.Event()
    hold = False
    rejection = False
    writes = []

    async def write(**kwargs):
        nonlocal resource
        writes.append(kwargs)
        if rejection:
            raise CalendarRequestError(status_code=412, message="controlled version conflict")
        assert kwargs["before"].event_id == resource["id"]
        assert kwargs["calendar_id"] == "owner@example.test" and kwargs["send_updates"] == "none"
        started.set()
        if hold:
            await asyncio.Event().wait()
        updated = copy.deepcopy(resource)
        updated["etag"] = f'"after-{len(writes)}"'
        updated["attendees"][0]["responseStatus"] = kwargs["response_status"]
        # The controlled provider response retains all other event fields.
        assert updated["attendees"][1:] == resource["attendees"][1:]
        assert updated["organizer"] == resource["organizer"]
        resource = updated
        return copy.deepcopy(resource)

    module._provider = SimpleNamespace(
        get_response_calendar=AsyncMock(
            return_value={"id": "owner@example.test", "accessRole": "writer"}
        ),
        get_response_event=AsyncMock(side_effect=lambda **kwargs: copy.deepcopy(resource)),
        prepare_response_write=AsyncMock(return_value="controlled-not-a-credential"),
        respond_to_invitation=write,
    )
    approvals = ApprovalsModule()
    await approvals.register_tools(mcp, {}, pool, "messenger")
    from pathlib import Path

    daemon = ButlerDaemon(
        Path(__file__).resolve().parents[2] / "roster" / "messenger", db=SimpleNamespace(pool=pool)
    )
    daemon.config = SimpleNamespace(
        name="messenger",
        modules={"approvals": {"enabled": True, "gated_tools": {"calendar_respond": {}}}},
    )
    daemon.mcp = mcp
    daemon._modules = [module, approvals]
    monkeypatch.setattr(daemon, "_build_approval_push_runtime", lambda: None)
    originals = await daemon._apply_approval_gates()
    assert module._response_ready is True

    class OwningMCPClient:
        async def call_tool(self, name, arguments):
            async with Client(mcp) as client:
                return await client.call_tool(name, arguments)

    facade = OwningMCPClient()
    manager = SimpleNamespace(get_client=AsyncMock(return_value=facade))
    database = DatabaseManager()
    database._pools = {"messenger": pool}
    database._butler_modules = {"messenger": frozenset({"calendar", "approvals"})}
    database.register_configured_modules("messenger", frozenset({"calendar", "approvals"}))
    app = create_authenticated_domain_app()
    app.dependency_overrides[workspace._get_db_manager] = lambda: database
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    coordinator = module._response_coordinator
    now = datetime.now(UTC)
    dossier = DecisionDossier("Owner selected this exact occurrence", [], "self", "compensable")

    async def prepare(arguments):
        return await coordinator.prepare_and_park(
            tool_args=arguments,
            requested_at=now,
            expires_at=now + timedelta(hours=1),
            dossier=dossier,
        )

    request = dict(
        entry_id=str(entry_id),
        request_id=str(uuid.uuid4()),
        response_status="accepted",
        send_updates="none",
    )
    # The real park writes first, then a planted failure must roll back BOTH
    # command and pending action. A separate acquisition proves the absence.
    import butlers.modules.calendar_response as response_module

    real_park = response_module.park_pending_action

    async def fail_after_park(*args, **kwargs):
        await real_park(*args, **kwargs)
        raise RuntimeError("controlled post-park failure")

    with monkeypatch.context() as control:
        control.setattr(response_module, "park_pending_action", fail_after_park)
        assert "error" in await prepare(request)
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM calendar_action_log WHERE request_id=$1", request["request_id"]
        )
        == 0
    )
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM pending_actions WHERE tool_name='calendar_respond'"
        )
        == 0
    )
    admitted = await asyncio.gather(prepare(request), prepare(request))
    assert admitted[0] == admitted[1] and admitted[0]["status"] == "pending_approval"
    command_id = uuid.UUID(admitted[0]["command_id"])
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM calendar_action_log WHERE request_id=$1", request["request_id"]
        )
        == 1
    )
    assert await pool.fetchval("SELECT count(*) FROM pending_actions WHERE id=$1", command_id) == 1
    assert "error" in await prepare({**request, "response_status": "declined"})

    async def execute(identifier):
        await pool.execute(
            "UPDATE pending_actions SET status='approved',decided_by='human:dashboard:rest-api',decided_at=now() WHERE id=$1",
            identifier,
        )
        arguments = await pool.fetchval(
            "SELECT tool_args FROM pending_actions WHERE id=$1", identifier
        )
        return await execute_approved_action(
            pool,
            identifier,
            "calendar_respond",
            arguments,
            originals["calendar_respond"],
            origin_butler="messenger",
        )

    # Actual authenticated HTTP -> registered public gate -> existing verified
    # decision/audit -> registered dispatch -> captured executor -> command.
    # No mock of approve_owning_action or dispatch supplies the applied result.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        result = await client.post("/api/calendar/workspace/respond", json=request)
        assert result.status_code == 200
        assert result.json()["data"]["status"] == "applied" and len(writes) == 1
        durable = await pool.fetchrow(
            "SELECT action_status,action_result FROM calendar_action_log WHERE id=$1", command_id
        )
        assert (
            durable["action_status"] == "applied"
            and durable["action_result"]["before"]["response_status"] == "needsAction"
        )
        assert (
            await pool.fetchval("SELECT decided_by FROM pending_actions WHERE id=$1", command_id)
            == "human:dashboard:rest-api"
        )
        assert (
            await pool.fetchval(
                "SELECT count(*) FROM approval_rules WHERE tool_name='calendar_respond'"
            )
            == 0
        )
        replay = await client.post("/api/calendar/workspace/respond", json=request)
        assert replay.json()["data"]["status"] == "applied" and len(writes) == 1
        noop = await client.post(
            "/api/calendar/workspace/respond", json={**request, "request_id": str(uuid.uuid4())}
        )
        assert noop.status_code == 200 and noop.json()["data"]["status"] == "noop"
        assert len(writes) == 1 and noop.json()["data"]["undo_available"] is False
        concurrent_undo = await asyncio.gather(
            client.post(f"/api/calendar/workspace/undo/{command_id}"),
            client.post(f"/api/calendar/workspace/undo/{command_id}"),
        )
        assert sorted(result.status_code for result in concurrent_undo) == [200, 409]
        undone = next(result for result in concurrent_undo if result.status_code == 200)
        assert undone.status_code == 200 and undone.json()["data"]["undone"] is True
        inverse_id = uuid.UUID(undone.json()["data"]["result"]["command_id"])
        inverse_args = await pool.fetchval(
            "SELECT tool_args FROM pending_actions WHERE id=$1", inverse_id
        )
        assert inverse_args["response_status"] == "needsAction" and len(writes) == 2
        assert (await client.post(f"/api/calendar/workspace/undo/{command_id}")).status_code == 409
        with pytest.raises(CalendarResponseError, match="response_inverse_used"):
            await reserve_response_inverse(pool, command_id)

    # A real committed rejection receipt earns neither success nor inverse.
    # The explicit provider status is controlled; action-log SQL is actual.
    rejection = True
    rejected_id = uuid.UUID(
        (await prepare({**request, "request_id": str(uuid.uuid4())}))["command_id"]
    )
    rejected_outcome = await execute(rejected_id)
    assert rejected_outcome.success is False
    assert (
        await pool.fetchval("SELECT status FROM pending_actions WHERE id=$1", rejected_id)
        == "approved"
    )
    rejected_row = await pool.fetchrow(
        "SELECT action_status,action_result FROM calendar_action_log WHERE id=$1", rejected_id
    )
    assert rejected_row["action_status"] == "failed"
    assert rejected_row["action_result"]["status"] == "failed"
    assert resource["attendees"][0]["responseStatus"] == "needsAction"
    with pytest.raises(CalendarResponseError, match="response_inverse_unearned"):
        await reserve_response_inverse(pool, rejected_id)
    rejection = False

    # An attributable provider effect remains applied when the actual projection
    # seam raises; a separate acquisition observes durable before/after receipt.
    projection_id = uuid.UUID(
        (await prepare({**request, "request_id": str(uuid.uuid4())}))["command_id"]
    )
    with monkeypatch.context() as control:
        control.setattr(
            module,
            "_project_provider_changes",
            AsyncMock(side_effect=RuntimeError("controlled projection refusal")),
        )
        assert (await execute(projection_id)).success is True
    projection_row = await pool.fetchrow(
        "SELECT action_status,action_result FROM calendar_action_log WHERE id=$1", projection_id
    )
    assert projection_row["action_status"] == "applied"
    assert projection_row["action_result"]["projection_available"] is False
    assert projection_row["action_result"]["before"]["response_status"] == "needsAction"
    assert projection_row["action_result"]["after"]["response_status"] == "accepted"
    # A real successor provider version refuses the original guarded inverse.
    resource["etag"] = '"controlled-successor"'
    stale_arguments = await reserve_response_inverse(pool, projection_id)
    before_stale = len(writes)
    assert (await prepare(stale_arguments))["error"] == "response_inverse_stale"
    assert len(writes) == before_stale

    # A controlled network timeout reaches the real separately committed claim.
    # It must persist uncertainty, earn no inverse and never call again on replay.
    timeout_request = {**request, "request_id": str(uuid.uuid4()), "response_status": "declined"}
    timeout_id = uuid.UUID((await prepare(timeout_request))["command_id"])
    from butlers.modules.calendar_response import CalendarResponseUncertainError

    timeout_write = AsyncMock(
        side_effect=CalendarResponseUncertainError("response_transport_uncertain")
    )
    with monkeypatch.context() as control:
        control.setattr(module._provider, "respond_to_invitation", timeout_write)
        assert (await execute(timeout_id)).success is False
        assert (await execute(timeout_id)).success is False
    timeout_write.assert_awaited_once()
    timeout_row = await pool.fetchrow(
        "SELECT action_status,action_payload,action_result FROM calendar_action_log WHERE id=$1",
        timeout_id,
    )
    assert timeout_row["action_status"] == "pending"
    assert timeout_row["action_payload"]["phase"] == "uncertain"
    assert timeout_row["action_result"]["status"] == "uncertain"
    assert (
        await pool.fetchval("SELECT status FROM pending_actions WHERE id=$1", timeout_id)
        == "approved"
    )
    with pytest.raises(CalendarResponseError, match="response_inverse_unearned"):
        await reserve_response_inverse(pool, timeout_id)

    # The independent cancellation case uses another actual projected provider
    # instance, so the earlier timeout fence cannot make its absence vacuous.
    resource["id"] = "exact-occurrence-cancel"
    resource["etag"] = '"cancel-before"'
    cancel_event = _google_event_to_calendar_event(resource, fallback_timezone="UTC")
    assert cancel_event is not None
    await module._project_provider_changes(
        source_id=source_id,
        provider_name="google",
        calendar_id="owner@example.test",
        updated_events=[cancel_event],
        cancelled_ids=[],
    )
    cancel_entry = await pool.fetchval(
        "SELECT i.id FROM calendar_event_instances i JOIN calendar_events e ON e.id=i.event_id "
        "WHERE i.source_id=$1 AND e.origin_ref=$2",
        source_id,
        resource["id"],
    )
    assert cancel_entry is not None and cancel_entry != entry_id

    # Executor cancellation rolls its transaction back. The separately
    # committed attempt survives; neither replay nor a new key may resend.
    held_request = {
        **request,
        "entry_id": str(cancel_entry),
        "request_id": str(uuid.uuid4()),
        "response_status": "declined",
    }
    before_cancel = len(writes)
    held_id = uuid.UUID((await prepare(held_request))["command_id"])
    hold = True
    started.clear()
    task = asyncio.create_task(execute(held_id))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = await pool.fetchrow(
        "SELECT action_payload,action_result FROM calendar_action_log WHERE id=$1", held_id
    )
    assert row["action_payload"]["phase"] == "egress_started" and not row["action_result"]
    assert (
        await pool.fetchval("SELECT status FROM pending_actions WHERE id=$1", held_id) == "approved"
    )
    assert (await prepare(held_request))["status"] == "uncertain"
    newer = {**held_request, "request_id": str(uuid.uuid4())}
    newer_id = uuid.UUID((await prepare(newer))["command_id"])
    hold = False
    refused = await execute(newer_id)
    assert refused.success is False and refused.error == "response_target_uncertain"
    assert len(writes) == before_cancel + 1
    # Removing the target does not remove the immutable action-log/fence row.
    await pool.execute("DELETE FROM calendar_sources WHERE id=$1", source_id)
    retained = await pool.fetchrow(
        "SELECT action_payload,source_id FROM calendar_action_log WHERE id=$1", held_id
    )
    assert retained["source_id"] is None and retained["action_payload"]["phase"] == "egress_started"
    # Reinstalling the same physical provider occurrence under a fresh source
    # row and entry UUID cannot evade the old unresolved attempt after pruning.
    await pool.execute("DELETE FROM pending_actions WHERE id=$1", held_id)
    replacement_source = await pool.fetchval(
        "INSERT INTO calendar_sources (source_key,source_kind,lane,provider,calendar_id,writable,metadata) "
        "VALUES ('provider:google:replacement','provider_event','user','google','owner@example.test',true,$1) RETURNING id",
        {"account_email": "owner@example.test"},
    )
    replacement_event = _google_event_to_calendar_event(resource, fallback_timezone="UTC")
    assert replacement_event is not None
    await module._project_provider_changes(
        source_id=replacement_source,
        provider_name="google",
        calendar_id="owner@example.test",
        updated_events=[replacement_event],
        cancelled_ids=[],
    )
    replacement_entry = await pool.fetchval(
        "SELECT id FROM calendar_event_instances WHERE source_id=$1", replacement_source
    )
    replacement = {
        **held_request,
        "entry_id": str(replacement_entry),
        "request_id": str(uuid.uuid4()),
    }
    replacement_id = uuid.UUID((await prepare(replacement))["command_id"])
    refused_again = await execute(replacement_id)
    assert refused_again.success is False and refused_again.error == "response_target_uncertain"
    assert len(writes) == before_cancel + 1
    restarted = CalendarModule()
    await restarted.register_tools(
        FastMCP("restarted-response"),
        {"provider": "google", "response_enabled": True},
        SimpleNamespace(pool=pool),
        "messenger",
    )
    restarted.set_response_approval_ready(True)
    restarted._provider = module._provider
    restarted._verified_response_account = module._verified_response_account
    assert (
        await restarted._response_coordinator.prepare_and_park(
            tool_args=held_request,
            requested_at=now,
            expires_at=now + timedelta(hours=1),
            dossier=dossier,
        )
    )["status"] == "uncertain"
