"""Actual migrated SQL -> production runtime -> authenticated TCP MCP.

Authored for HOSTED execution, not run locally. Admin-created launching rows and
controlled submission evidence are NOT a real runner/creator authority producer.
The native read is real; no effect or owning approval commitment is claimed.
REQ-capability-authorization-002; REQ-capability-authorization-008.
"""

from uuid import uuid4

import httpx
import pytest
from anyio import Path as AsyncPath

from butlers.capability_broker.contracts import BrokerRefused, token_hash
from butlers.capability_broker.runtime import RuntimeConfig, build_runtime
from butlers.core.goal_admission import (
    Evidence,
    GoalAdmission,
    Grant,
    SourceRef,
    digest,
    encode_grants,
)
from tests.capability_broker.test_control_runtime import NativeListener, key_config
from tests.contracts.test_catalog_publication import registered_catalog
from tests.integration import test_catalog_publication as publication_fixtures
from tests.integration.test_broker_published_catalog import (
    health_approvals_url as health_approvals_url,
)
from tests.polling import poll_until
from tests.switchboard.goal_admission_fixtures import Sources, limits
from tests.switchboard.goal_ledger_fixtures import configure

fleet = publication_fixtures.fleet
publication_url = publication_fixtures.publication_url
pytestmark = pytest.mark.integration


async def test_real_runtime_role_hash_commit_current_catalog_read_and_revocation(
    publication_url, fleet, tmp_path
):
    await publication_fixtures.publish_all(publication_url)
    await configure(fleet, limits())
    mcp, _ = await registered_catalog("health")
    domain = NativeListener(mcp.http_app(path="/mcp", stateless_http=False, json_response=False))
    config = key_config(tmp_path, "http://127.0.0.1:9/mcp")
    owner = None
    try:
        await domain.start()
        config["domain_endpoints"] = dict.fromkeys(
            config["domain_endpoints"], f"http://127.0.0.1:{domain.port}/mcp"
        )
        async with publication_fixtures.runtime_pool(publication_url, "switchboard") as runtime:
            try:
                admitted = await GoalAdmission(
                    runtime, Sources(Evidence("message", "controlled-owner", owner=True))
                ).admit_submission(SourceRef("message", str(uuid4())), uuid4())
                assert admitted.result == "admitted"
                goal, invocation = admitted.goal_id, uuid4()
                grants = encode_grants((Grant("health__lookup"),))
                await fleet.execute(
                    "UPDATE switchboard.goals SET status='running' WHERE id=$1", goal
                )
                await fleet.execute(
                    "INSERT INTO switchboard.invocations "
                    "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
                    "VALUES ($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500)",
                    invocation,
                    goal,
                    grants,
                    digest(grants),
                )
                owner = await build_runtime(
                    runtime, RuntimeConfig.parse(config), host="127.0.0.1", port=0
                )
                token = await owner.broker.issue_invocation_token(goal, invocation)
                # The admin observer is a distinct acquisition from the runtime pool.
                async with fleet.acquire() as observer:
                    assert await observer.fetchval(
                        "SELECT token_hash FROM switchboard.invocations WHERE id=$1", invocation
                    ) == token_hash(token)
                await fleet.execute(
                    "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
                )
                await owner.start()
                async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                    url = f"http://127.0.0.1:{owner.listener.port}/goals/{goal}/mcp/"
                    headers = {
                        "Authorization": "Bearer " + token,
                        "Accept": "application/json,text/event-stream",
                    }
                    response = await client.post(
                        url,
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "health__lookup",
                                "arguments": {"query": "runtime read"},
                            },
                        },
                    )
                    result = response.json()["result"]["structuredContent"]
                    assert result["status"] == "returned" and result["effect_id"] is None
                    assert result["value"]["content"][0]["text"] == "runtime read"
                    async with fleet.acquire() as observer:
                        assert (
                            await observer.fetchval(
                                "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                            )
                            == 1
                        )
                        assert (
                            await observer.fetchval(
                                "SELECT exposed FROM switchboard.goals WHERE id=$1", goal
                            )
                            is True
                        )
                    # The private completion hook cannot revoke a still-running
                    # invocation and thereby open a fresh launching-token cycle.
                    with pytest.raises(BrokerRefused, match="authentication_refused"):
                        await owner.broker.revoke_invocation_token(goal, invocation)
                    assert await fleet.fetchval(
                        "SELECT token_hash FROM switchboard.invocations WHERE id=$1", invocation
                    ) == token_hash(token)
                    await fleet.execute(
                        "UPDATE switchboard.invocations SET status='returned' WHERE id=$1",
                        invocation,
                    )
                    await owner.broker.revoke_invocation_token(goal, invocation)
                    denied = await client.post(
                        url,
                        headers=headers,
                        json={
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/list",
                            "params": {},
                        },
                    )
                    assert denied.status_code == 401
                    async with fleet.acquire() as observer:
                        assert (
                            await observer.fetchval(
                                "SELECT token_hash IS NULL FROM switchboard.invocations WHERE id=$1",
                                invocation,
                            )
                            is True
                        )
            finally:
                if owner is not None:
                    await owner.close()
                    assert owner.listener.cleanup_complete and owner.listener.task.done()
    finally:
        try:
            await domain.close()
        finally:
            await AsyncPath(config["key_file"]).unlink()
    assert domain.cleanup_complete and domain.task.done()


@pytest.mark.parametrize(
    "mode", ["pending", "approved", "late_park", "race", "ack_loss", "rollback"]
)
async def test_constructed_runtime_Stop_commits_owning_fence_and_exact_central_ACK(
    publication_url, fleet, health_approvals_url, tmp_path, monkeypatch, mode
):
    """AUTHORED/COLLECT ONLY: actual migrations, roles, locks and committed observers.

    The registered Health approval-enabled configuration and private exchange
    are controlled test inputs. This proves neither live activation nor a real
    runner source/owner authentication producer or external effect commitment.
    """
    import asyncio
    from contextlib import AsyncExitStack
    from types import SimpleNamespace

    import asyncpg

    from butlers.capability_broker.cancel_worker import CancellationWorker
    from butlers.capability_broker.transport.client import PrivateClient
    from butlers.capability_broker.transport.protocol import identity, request
    from butlers.core.catalog_publication import CatalogPublisher
    from butlers.core.goal_stop import GoalStop
    from butlers.db import register_jsonb_codec
    from butlers.modules.approvals.goal_dispatch import GoalDispatch
    from butlers.modules.approvals.models import ActionStatus
    from tests.capability_broker.test_published_catalog import registered_health_record

    catalog, originals, facts = await registered_health_record(monkeypatch, approvals_enabled=True)
    await publication_fixtures.publish_all(publication_url)
    async with publication_fixtures.runtime_pool(publication_url, "health") as publisher:
        await CatalogPublisher(publisher, "health").publish(catalog)
    await configure(fleet, limits())

    async def role(conn):
        await conn.execute("SET ROLE butler_health_rw")
        await conn.execute("SET search_path TO health,public")

    async def close(pool):
        try:
            await asyncio.wait_for(pool.close(), timeout=10)
        except BaseException:
            pool.terminate()
            raise

    config = key_config(tmp_path, "http://127.0.0.1:9/mcp")
    config["approval_transports"] = {
        "health": {
            "enabled": True,
            "role": "broker-control",
            "domain": "health",
            "endpoint": "https://localhost:4200/mcp",
            "peer": {
                "role": "owning-service",
                "domain": "health",
                "sans": ["DNS:localhost"],
                "client_certificate": False,
            },
            "material": {
                "ca_path": "/software-only/ca",
                "certificate_path": "/software-only/cert",
                "private_key_path": "/software-only/key",
            },
        }
    }
    monkeypatch.setattr(
        "butlers.capability_broker.transport.client.build_client",
        lambda settings, context: PrivateClient(settings, None),
    )
    park_entered, park_release, cancellation_finished = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    calls, owner_runtime = [], None
    pending_call = contender = None
    try:
        async with AsyncExitStack() as cleanup:
            observer = await asyncpg.create_pool(
                health_approvals_url,
                init=register_jsonb_codec,
                server_settings={"search_path": "health,public"},
                min_size=1,
                max_size=2,
            )
            cleanup.push_async_callback(close, observer)
            owning = await asyncpg.create_pool(
                health_approvals_url,
                init=register_jsonb_codec,
                setup=role,
                min_size=1,
                max_size=3,
                command_timeout=5,
            )
            cleanup.push_async_callback(close, owning)
            central = await cleanup.enter_async_context(
                publication_fixtures.runtime_pool(publication_url, "switchboard")
            )
            admitted = await GoalAdmission(
                central, Sources(Evidence("message", "controlled-owner", owner=True))
            ).admit_submission(SourceRef("message", str(uuid4())), uuid4())
            assert admitted.result == "admitted"
            goal, invocation = admitted.goal_id, uuid4()
            await fleet.execute("UPDATE switchboard.goals SET status='running' WHERE id=$1", goal)
            grants = encode_grants((Grant("health__measurement_log", park_only=True),))
            await fleet.execute(
                "INSERT INTO switchboard.invocations "
                "(id,goal_id,depth,role,lead_epoch,status,grant_snapshot,grant_digest,max_turns,max_tokens) "
                "VALUES($1,$2,0,'lead',0,'launching',$3::jsonb,$4,5,500)",
                invocation,
                goal,
                grants,
                digest(grants),
            )
            owner_runtime = await build_runtime(
                central, RuntimeConfig.parse(config), host="127.0.0.1", port=0
            )
            cleanup.push_async_callback(owner_runtime.close)
            op = GoalDispatch(
                owning,
                "health",
                catalog,
                originals,
                {
                    owner_runtime.broker.signer.kid: owner_runtime.broker.signer.private_key.public_key()
                },
            )
            peer = SimpleNamespace(role="broker-control", domain="health")

            async def exchange(client, value, *, assertion=None):
                assert client is owner_runtime.broker.approvals.clients["health"]
                assert assertion is None
                calls.append(value)
                if value["operation"] == "PARK" and mode == "late_park":
                    park_entered.set()
                    await park_release.wait()
                try:
                    return await op(request(value, "health"), peer)
                finally:
                    if value["operation"] == "CANCEL":
                        cancellation_finished.set()

            monkeypatch.setattr(PrivateClient, "call", exchange)
            token = await owner_runtime.broker.issue_invocation_token(goal, invocation)
            await fleet.execute(
                "UPDATE switchboard.invocations SET status='running' WHERE id=$1", invocation
            )
            args = {"type": "weight", "value": 72.5}
            if mode == "late_park":
                pending_call = asyncio.create_task(
                    owner_runtime.broker.call(goal, token, "health__measurement_log", args)
                )
                async with asyncio.timeout(5):
                    await park_entered.wait()
                target = identity(calls[0]["identity"])
            else:
                parked = await owner_runtime.broker.call(
                    goal, token, "health__measurement_log", args
                )
                assert parked.status == "parked"
                target = identity(calls[0]["identity"])
            if mode == "approved":
                await op.owner.transition(
                    target,
                    target_status=ActionStatus.APPROVED,
                    decided_by="human:owner",
                    event_actor="owner",
                    event_reason="controlled",
                )
            if mode == "rollback":
                # Fault only the fixture-owned receipt insertion. Production
                # guards, RLS, runtime grants and complete019 schema stay intact.
                await observer.execute(
                    "CREATE FUNCTION health.stop_contract_refuse() RETURNS trigger "
                    "LANGUAGE plpgsql AS $$ BEGIN IF NEW.outcome='cancelled' THEN "
                    "RAISE EXCEPTION 'controlled_cancel_receipt_refusal' USING ERRCODE='23514'; "
                    "END IF; RETURN NEW; END $$; CREATE TRIGGER stop_contract_refuse "
                    "BEFORE INSERT ON health.approval_goal_receipts FOR EACH ROW "
                    "EXECUTE FUNCTION health.stop_contract_refuse()"
                )
            if mode == "ack_loss":
                acknowledge = GoalStop.ack_cancel_work

                async def lost_ack(stop, *args):
                    await acknowledge(stop, *args)
                    raise ConnectionError("controlled_ACK_commit_reply_loss")

                monkeypatch.setattr(GoalStop, "ack_cancel_work", lost_ack)
            try:
                stopped = await GoalStop(central).stop_goal(goal, uuid4(), "b" * 64)
                assert stopped.cancellation_work == 1 and stopped.owning_cancellation_pending
                assert await fleet.fetchval(
                    "SELECT token_hash IS NULL FROM switchboard.invocations WHERE id=$1", invocation
                )
                await owner_runtime.start()
                if mode == "race":
                    contender = asyncio.create_task(
                        CancellationWorker(
                            GoalStop(central), owner_runtime.broker.approvals.clients
                        ).run_batch()
                    )
                if mode == "rollback":
                    async with asyncio.timeout(5):
                        await cancellation_finished.wait()
                    await owner_runtime.close()
                else:

                    async def acknowledged():
                        return (
                            await fleet.fetchval(
                                "SELECT state FROM switchboard.goal_approval_cancel_work WHERE effect_id=$1",
                                target.effect_id,
                            )
                            == "acknowledged"
                        )

                    async with asyncio.timeout(5):
                        await poll_until(acknowledged, interval_s=0.01)
                if contender is not None:
                    await contender
                # Distinct acquisitions prove actual COMMIT, not local result objects.
                async with fleet.acquire() as connection:
                    central_row = await connection.fetchrow(
                        "SELECT w.*,e.status AS effect_status FROM "
                        "switchboard.goal_approval_cancel_work w JOIN switchboard.effects e "
                        "ON (e.goal_id,e.id)=(w.goal_id,w.effect_id) WHERE w.effect_id=$1",
                        target.effect_id,
                    )
                async with observer.acquire() as connection:
                    gate = await connection.fetchrow(
                        "SELECT * FROM approval_goal_effects WHERE effect_id=$1", target.effect_id
                    )
                    action = await connection.fetchrow(
                        "SELECT * FROM pending_actions WHERE id=$1", target.effect_id
                    )
                    receipt = await connection.fetchrow(
                        "SELECT * FROM approval_goal_receipts WHERE effect_id=$1 "
                        "AND outcome IN ('cancelled','fenced_absent')",
                        target.effect_id,
                    )
                if mode == "rollback":
                    assert central_row["state"] == "claimed"
                    assert central_row["effect_status"] == "parked"
                    assert action["status"] == "pending" and not gate["stop_fenced"]
                    assert receipt is None and central_row["receipt_digest"] is None
                else:
                    assert central_row["state"] == "acknowledged"
                    assert central_row["effect_status"] == "cancelled" and gate["stop_fenced"]
                    assert central_row["receipt_digest"] == receipt["digest"]
                    assert central_row["outcome"] == receipt["outcome"]
                    assert gate["dispatch_intent_id"] is None
                    assert (
                        (action is None) if mode == "late_park" else action["status"] == "cancelled"
                    )
                    if mode == "late_park":
                        park_release.set()
                        await asyncio.gather(pending_call, return_exceptions=True)
                        assert (
                            await observer.fetchval(
                                "SELECT count(*) FROM pending_actions WHERE id=$1", target.effect_id
                            )
                            == 0
                        )
                assert not facts  # No original handler or external undo claim.
                assert sum(v["operation"] == "CANCEL" for v in calls) == 1
                assert (
                    await fleet.fetchval(
                        "SELECT used_calls FROM switchboard.budgets WHERE goal_id=$1", goal
                    )
                    == 1
                )
            finally:
                park_release.set()
                if pending_call is not None:
                    await asyncio.gather(pending_call, return_exceptions=True)
                if contender is not None:
                    await asyncio.gather(contender, return_exceptions=True)
                await owner_runtime.close()
                if mode == "rollback":
                    await observer.execute(
                        "DROP TRIGGER stop_contract_refuse ON health.approval_goal_receipts; "
                        "DROP FUNCTION health.stop_contract_refuse()"
                    )
    finally:
        await AsyncPath(config["key_file"]).unlink()
