"""Calendar D13 migrated role/commit/race controls; AUTHORED, NOT RUN locally.

REQ-capability-authorization-004; REQ-module-calendar-029. Provider reads and
writes are controlled, as is transport delivery to the real fixed owning
operation. Neither collaborator supplies SQL authority or a committed effect.
"""

from __future__ import annotations

import asyncio
import copy
from contextlib import AsyncExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastmcp import FastMCP

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db, get_mcp_manager
from butlers.capability_broker.assertions import AssertionSigner
from butlers.capability_broker.catalog import PublishedAuthorities
from butlers.capability_broker.contracts import BrokerRefused
from butlers.capability_broker.owning import PrivateApprovals
from butlers.capability_broker.service import Broker
from butlers.capability_broker.transport.client import PrivateClient
from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
from butlers.capability_broker.transport.protocol import request as owning_request
from butlers.core.capability_json import arguments_digest
from butlers.core.catalog_publication import CatalogPublisher
from butlers.core.goal_admission import (
    Evidence,
    GoalAdmission,
    Grant,
    SourceRef,
    digest,
    encode_grants,
)
from butlers.core.goal_stop import GoalStop
from butlers.db import register_jsonb_codec
from butlers.modules.approvals.goal_dispatch import GoalDispatch
from butlers.modules.calendar import CalendarModule, _google_event_to_calendar_event
from butlers.modules.calendar_response import reserve_response_inverse
from butlers.testing.migration import create_migrated_test_db, migration_db_name
from tests.api.auth_helpers import create_authenticated_domain_app
from tests.contracts.test_tool_authorization_catalog import _roster_catalog
from tests.integration import test_catalog_publication as publication
from tests.modules.test_calendar_response import _invitation
from tests.switchboard.goal_admission_fixtures import Sources, limits
from tests.switchboard.goal_ledger_fixtures import configure

publication_url = publication.publication_url
fleet = publication.fleet
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def calendar_approvals_url(postgres_container):
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        ["core", "approvals"],
        schemas={"core": "relationship", "approvals": "relationship"},
    )


async def _close(pool):
    try:
        await asyncio.wait_for(pool.close(), timeout=5)
    except BaseException:
        pool.terminate()
        raise


@pytest.mark.parametrize(
    "mode", ["healthy", "audit_rollback", "competing_edit", "stop", "resource"]
)
async def test_real_calendar_root_revision_fresh_decision_and_one_winner(
    publication_url, fleet, calendar_approvals_url, monkeypatch, mode
):
    """Real full-fleet admission, separate owning DB, runtime roles and readbacks.

    The two competing edits rendezvous after actual candidate COMMIT. Exactly
    one may attach the old parent. Other modes retain the same healthy setup.
    """
    monkeypatch.setattr("butlers.db.Database.connect", AsyncMock())
    catalog = await _roster_catalog("relationship")
    await publication.publish_all(publication_url)
    async with publication.runtime_pool(publication_url, "relationship") as publisher:
        await CatalogPublisher(publisher, "relationship").publish(catalog)
    await configure(fleet, limits())

    async def role(conn):
        await conn.execute("SET ROLE butler_relationship_rw")
        await conn.execute("SET search_path TO relationship,public")

    async with AsyncExitStack() as cleanup:
        admin = await asyncpg.create_pool(
            calendar_approvals_url,
            init=register_jsonb_codec,
            server_settings={"search_path": "relationship,public"},
            min_size=1,
            max_size=3,
        )
        cleanup.push_async_callback(_close, admin)
        owning = await asyncpg.create_pool(
            calendar_approvals_url,
            init=register_jsonb_codec,
            setup=role,
            min_size=1,
            max_size=6,
            command_timeout=5,
        )
        cleanup.push_async_callback(_close, owning)
        runtime = await cleanup.enter_async_context(
            publication.runtime_pool(publication_url, "switchboard")
        )
        assert await owning.fetchval("SELECT current_user") == "butler_relationship_rw"
        module = CalendarModule()
        mcp = FastMCP("real-calendar-owner-edit")
        await module.register_tools(
            mcp,
            {"provider": "google", "response_enabled": True},
            SimpleNamespace(pool=owning),
            "relationship",
        )
        module.set_response_approval_ready(True)
        module._verified_response_account = AsyncMock(
            return_value={"entity_id": str(uuid4()), "email": "owner@example.test"}
        )
        resource = _invitation()
        writes = []

        async def write(**kwargs):
            writes.append(kwargs["response_status"])
            resource["etag"] = '"after-one-write"'
            resource["attendees"][0]["responseStatus"] = kwargs["response_status"]
            return copy.deepcopy(resource)

        module._provider = SimpleNamespace(
            get_response_calendar=AsyncMock(
                return_value={"id": "owner@example.test", "accessRole": "writer"}
            ),
            get_response_event=AsyncMock(
                side_effect=lambda **kw: {**copy.deepcopy(resource), "id": kw["event_id"]}
            ),
            prepare_response_write=AsyncMock(return_value="controlled-not-a-credential"),
            respond_to_invitation=write,
        )
        source = await owning.fetchval(
            "INSERT INTO calendar_sources(source_key,source_kind,lane,provider,calendar_id,writable,metadata) "
            "VALUES($1,'provider_event','user','google','owner@example.test',true,$2) RETURNING id",
            "edit-contract:" + str(uuid4()),
            {"account_email": "owner@example.test"},
        )
        projected = _google_event_to_calendar_event(resource, fallback_timezone="UTC")
        assert projected is not None
        await module._project_provider_changes(
            source_id=source,
            provider_name="google",
            calendar_id="owner@example.test",
            updated_events=[projected],
            cancelled_ids=[],
        )
        entry = await owning.fetchval(
            "SELECT id FROM calendar_event_instances WHERE source_id=$1", source
        )
        assert entry is not None
        admitted = await GoalAdmission(
            runtime, Sources(Evidence("message", "controlled-owner", owner=True))
        ).admit_submission(SourceRef("message", str(uuid4())), uuid4())
        assert admitted.result == "admitted"
        goal, invocation = admitted.goal_id, uuid4()
        await fleet.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", goal)
        grants = encode_grants(
            (
                Grant(
                    "relationship__calendar_respond", (("entry_id", (str(entry),)),), park_only=True
                ),
            )
        )
        await fleet.execute(
            "INSERT INTO switchboard.invocations(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
            "VALUES($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500)",
            invocation,
            goal,
            grants,
            digest(grants),
        )
        signer = AssertionSigner("calendar-contract", Ed25519PrivateKey.generate())
        original = (await mcp.get_tool("calendar_respond")).fn
        operation = GoalDispatch(
            owning,
            "relationship",
            catalog,
            {"calendar_respond": original},
            {signer.kid: signer.private_key.public_key()},
            preparers={"calendar_respond": module._response_coordinator},
        )
        exchanges = []

        async def exchange(self, value, *, assertion=None):
            exchanges.append(value)
            return await operation(
                owning_request(value, "relationship"),
                SimpleNamespace(role="broker-control", domain="relationship"),
            )

        monkeypatch.setattr(PrivateClient, "call", exchange)
        private = PrivateClient(
            TransportConfig(
                "https://localhost:4200/mcp",
                PeerPolicy("owning-service", "relationship", frozenset({"DNS:localhost"})),
            ),
            None,
        )
        cleanup.push_async_callback(private.aclose)

        class NoDirectForward:
            async def dispatch(self, *args, **kwargs):
                raise AssertionError("calendar_requires_per_event_owner_decision")

        broker = Broker(
            runtime,
            PublishedAuthorities(),
            NoDirectForward(),
            signer,
            approvals=PrivateApprovals({"relationship": private}),
        )
        token = await broker.issue_invocation_token(goal, invocation)
        await fleet.execute(
            "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
        )
        public_args = {
            "entry_id": str(entry),
            "request_id": str(uuid4()),
            "response_status": "accepted",
            "send_updates": "none",
        }
        result = await broker.call(goal, token, "relationship__calendar_respond", public_args)
        assert result.status == "parked" and writes == []
        root = result.effect_id
        root_before = dict(
            await admin.fetchrow("SELECT * FROM calendar_action_log WHERE id=$1", root)
        )
        pending_before = dict(
            await admin.fetchrow("SELECT * FROM pending_actions WHERE id=$1", root)
        )
        assert pending_before["goal_args_digest"] == arguments_digest(public_args)
        assert pending_before["tool_args"]["_command_id"] == str(root)
        assert (
            await fleet.fetchval(
                "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
            )
            == 1
        )
        assert (
            await admin.fetchval("SELECT phase FROM approval_goal_effects WHERE effect_id=$1", root)
            == "pending"
        )
        db = DatabaseManager()
        db._pools = {"relationship": owning, "switchboard": runtime}
        db._butler_modules = {
            "relationship": frozenset({"calendar", "approvals"}),
            "switchboard": frozenset(),
        }
        db.register_configured_modules("relationship", frozenset({"calendar", "approvals"}))
        app = create_authenticated_domain_app()
        app.dependency_overrides[get_db] = lambda: db
        # ASGITransport deliberately skips application lifespan. Supply the
        # existing required dependency, but fail if the stale card dispatches.
        mcp_manager = SimpleNamespace(
            get_client=AsyncMock(side_effect=AssertionError("premature_calendar_dispatch"))
        )
        app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
        arrived, release = asyncio.Event(), asyncio.Event()
        prepared_count = 0

        async def prepare(value):
            nonlocal prepared_count
            candidate = await module._response_coordinator.prepare_owner_candidate(value)
            if mode == "stop":
                await GoalStop(runtime).stop_goal(goal, uuid4(), "a" * 64)
            if mode == "competing_edit":
                prepared_count += 1
                if prepared_count == 2:
                    arrived.set()
                await asyncio.wait_for(release.wait(), timeout=5)
            return candidate

        app.state.calendar_preparation_clients = SimpleNamespace(
            clients={"relationship": SimpleNamespace(prepare=prepare)}
        )
        if mode == "audit_rollback":
            await admin.execute(
                "CREATE FUNCTION relationship.calendar_edit_audit_refuse() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN IF NEW.action='approval.calendar_reprepare' THEN RAISE EXCEPTION 'controlled_audit_refusal' USING ERRCODE='23514'; END IF; RETURN NEW; END $$; "
                "CREATE TRIGGER calendar_edit_audit_refuse BEFORE INSERT ON public.audit_log FOR EACH ROW EXECUTE FUNCTION relationship.calendar_edit_audit_refuse()"
            )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as http:
                dossier = await http.get(f"/api/approvals/{root}")
                assert dossier.status_code == 200 and dossier.json()["data"]["call_preview"]
                before_card = dossier.json()["data"]["call_preview"]["displayed_call"]
                body = {"displayed_call": before_card, "response_status": "declined"}
                if mode == "resource":
                    # Real second provider projection prepares successfully, but
                    # the current signed Grant still restricts entry_id to entry.
                    other_resource = {**copy.deepcopy(resource), "id": "instance-two"}
                    other = _google_event_to_calendar_event(other_resource, fallback_timezone="UTC")
                    assert other is not None
                    await module._project_provider_changes(
                        source_id=source,
                        provider_name="google",
                        calendar_id="owner@example.test",
                        updated_events=[other],
                        cancelled_ids=[],
                    )
                    second = await owning.fetchval(
                        "SELECT i.id FROM calendar_event_instances i JOIN calendar_events e ON e.id=i.event_id "
                        "WHERE i.source_id=$1 AND e.origin_ref='instance-two'",
                        source,
                    )
                    assert second is not None and second != entry
                    body["entry_id"] = str(second)
                if mode == "competing_edit":
                    tasks = [
                        asyncio.create_task(
                            http.post(f"/api/approvals/{root}/calendar-reprepare", json=body)
                        )
                        for _ in range(2)
                    ]
                    try:
                        await asyncio.wait_for(arrived.wait(), timeout=5)
                        release.set()
                        responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
                    finally:
                        release.set()
                        for task in tasks:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    assert sorted(item.status_code for item in responses) == [200, 409]
                    edited = next(item for item in responses if item.status_code == 200)
                else:
                    edited = await http.post(f"/api/approvals/{root}/calendar-reprepare", json=body)
                assert writes == []
                after = dict(
                    await admin.fetchrow("SELECT * FROM pending_actions WHERE id=$1", root)
                )
                assert (
                    dict(
                        await admin.fetchrow("SELECT * FROM calendar_action_log WHERE id=$1", root)
                    )
                    == root_before
                )
                assert after["goal_args_digest"] == pending_before["goal_args_digest"]
                if mode in {"audit_rollback", "stop", "resource"}:
                    assert (
                        edited.status_code >= 400
                        and after["tool_args"] == pending_before["tool_args"]
                    )
                    assert after["status"] == "pending" and writes == []
                    return
                assert (
                    edited.status_code == 200 and edited.json()["data"]["review_required"] is True
                )
                selected = UUID(after["tool_args"]["_command_id"])
                assert selected != root and after["status"] == "pending"
                candidate = await admin.fetchrow(
                    "SELECT * FROM calendar_action_log WHERE id=$1", selected
                )
                assert candidate["request_id"] == root_before["request_id"]
                assert candidate["action_payload"]["parent_id"] == str(root)
                assert (
                    candidate["action_payload"]["command"]["arguments"]["response_status"]
                    == "declined"
                )
                stale = await http.post(
                    f"/api/approvals/{root}/approve", json={"displayed_call": before_card}
                )
                assert stale.status_code == 409 and writes == []
                mcp_manager.get_client.assert_not_awaited()
                fresh = edited.json()["data"]["call_preview"]["displayed_call"]
                assert (
                    fresh["args_digest"]
                    == arguments_digest(after["tool_args"])
                    != before_card["args_digest"]
                )
            # Real owning gate decision uses the exact prepared revision and a
            # NEW explicit displayed digest. Dispatch below is central replay.
            # Suppress only automatic forwarding: the actual decision/audit TX
            # remains real, and the central replay consumer is exercised next.
            import butlers.api.routers.approvals as routes
            from butlers.api.models.approval import DisplayedApprovalCall
            from butlers.api.routers.approvals import approve_owning_action

            async def no_dispatch(*args, **kwargs):
                return SimpleNamespace(kind="not_dispatched")

            with monkeypatch.context() as control:
                control.setattr(routes, "_dispatch_approved_action_outcome", no_dispatch)
                await approve_owning_action(
                    parsed_id=root,
                    actor_id="human:dashboard:rest-api",
                    edits=None,
                    action_butler="relationship",
                    target_pool=owning,
                    db_mgr=db,
                    mcp_mgr=None,
                    displayed_call=DisplayedApprovalCall(**fresh),
                    require_displayed_call=True,
                )
            assert (
                await admin.fetchval("SELECT status FROM pending_actions WHERE id=$1", root)
                == "approved"
            )
            assert writes == []
            # Controlled peer fence transport; its SQL claim remains genuine.
            # The existing two-owner Calendar integration proves fleet fencing.
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def local_claim(target):
                async with owning.acquire() as conn, conn.transaction():
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended($1,0))", target
                    )
                    yield conn, {"target_key": target}

            monkeypatch.setattr(module._response_fence, "claim", local_claim)
            monkeypatch.setattr(module._response_fence, "check_peers", AsyncMock())
            await fleet.execute(
                "UPDATE switchboard.invocations SET status='returned' WHERE id=$1", invocation
            )
            outcomes = await asyncio.gather(
                *(broker.replay_approved(goal, root) for _ in range(2)), return_exceptions=True
            )
            assert sum(isinstance(value, BrokerRefused) for value in outcomes) == 1
            assert writes == ["declined"]
            assert await admin.fetchval(
                "SELECT dispatch_consumed_at IS NOT NULL FROM approval_goal_effects WHERE effect_id=$1",
                root,
            )
            applied = await admin.fetchrow(
                "SELECT action_status,action_result FROM calendar_action_log WHERE id=$1", selected
            )
            assert (
                applied["action_status"] == "applied"
                and applied["action_result"]["after"]["response_status"] == "declined"
            )
            inverse = await reserve_response_inverse(owning, root)
            inverse_row = await admin.fetchrow(
                "SELECT action_payload FROM calendar_action_log WHERE request_id=$1",
                inverse["request_id"],
            )
            assert inverse_row["action_payload"]["inverse_of"] == str(selected)
            assert (
                await fleet.fetchval(
                    "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                )
                == 1
            )
        finally:
            if mode == "audit_rollback":
                await admin.execute(
                    "DROP TRIGGER calendar_edit_audit_refuse ON public.audit_log; DROP FUNCTION relationship.calendar_edit_audit_refuse()"
                )
