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
