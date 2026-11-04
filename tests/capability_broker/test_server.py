"""The goal endpoint over MCP streamable HTTP, against a recording broker."""

from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest

from butlers.capability_broker.contracts import BrokerRefused, BrokerResult, Reason, mint_token
from butlers.capability_broker.server import BrokerSwitch, create_goal_endpoint, session_app
from butlers.control import session_refusal_app
from tests.capability_broker.helpers import capability

pytestmark = pytest.mark.unit

_INIT = {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "goal-session", "version": "1"},
}


class _Broker:
    """Authenticates one token and grants one capability."""

    def __init__(self, goal: UUID, token: str):
        self.goal, self.token, self.calls = goal, token, []

    async def authenticate(self, goal_id, token):
        if (goal_id, token) != (self.goal, self.token):
            raise BrokerRefused(Reason.AUTHENTICATION)

    async def list_tools(self, goal_id, token):
        await self.authenticate(goal_id, token)
        return (capability(name="search"),)

    async def call(self, goal_id, token, name, arguments):
        await self.authenticate(goal_id, token)
        self.calls.append((name, arguments))
        return BrokerResult("returned", value={"hits": 1})


class _Session:
    def __init__(self, client, broker, url, headers):
        self.client, self.broker, self.url, self.headers = client, broker, url, headers

    async def rpc(self, method, params=None, *, url=None, headers=None):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        return await self.client.post(
            url or self.url, headers=self.headers if headers is None else headers, json=body
        )


@asynccontextmanager
async def _endpoint():
    # Entered inside each test: the MCP task group must exit in the task it entered.
    goal, (token, _) = uuid4(), mint_token()
    broker = _Broker(goal, token)
    app = create_goal_endpoint(broker)
    headers = {"authorization": f"Bearer {token}", "accept": "application/json, text/event-stream"}
    transport = httpx.ASGITransport(app=app)
    async with app.lifespan(), httpx.AsyncClient(transport=transport, base_url="http://b") as c:
        yield _Session(c, broker, f"/goals/{goal}/mcp", headers)


async def test_listing_is_the_grant_and_calls_reach_the_broker():
    async with _endpoint() as s:
        assert (await s.rpc("initialize", _INIT)).status_code == 200
        listed = await s.rpc("tools/list")
        assert [t["name"] for t in listed.json()["result"]["tools"]] == ["general__search"]
        called = await s.rpc("tools/call", {"name": "general__search", "arguments": {"q": "x"}})
        assert called.json()["result"]["structuredContent"]["value"] == {"hits": 1}
        assert s.broker.calls == [("general__search", {"q": "x"})]


async def test_authentication_precedes_every_mcp_operation():
    async with _endpoint() as s:
        other, _ = mint_token()
        for bad in ({}, {"authorization": f"Bearer {other}"}, {"authorization": "Basic x"}):
            headers = {**s.headers, **bad} if bad else {"accept": s.headers["accept"]}
            response = await s.rpc("initialize", _INIT, headers=headers)
            assert response.status_code == 401
            assert response.json() == {"error": "authentication_refused"}
        assert (await s.rpc("tools/list", url=f"/goals/{uuid4()}/mcp")).status_code == 401
        assert (await s.rpc("tools/list", url="/goals/not-a-goal/mcp")).status_code == 404
        assert s.broker.calls == []


async def test_duplicate_argument_names_are_refused_before_mcp():
    async with _endpoint() as s:
        body = (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":'
            b'{"name":"general__search","arguments":{"q":"a","q":"b"}}}'
        )
        headers = {**s.headers, "content-type": "application/json"}
        response = await s.client.post(s.url, headers=headers, content=body)
        assert response.status_code == 400 and s.broker.calls == []


def test_session_app_is_the_refusal_app_until_switched_on():
    broker = _Broker(uuid4(), mint_token()[0])
    assert BrokerSwitch().enabled is False
    assert session_app(BrokerSwitch(), broker) is session_refusal_app
    assert session_app(BrokerSwitch(enabled=True), None) is session_refusal_app
    assert session_app(BrokerSwitch(enabled=True), broker) is not session_refusal_app
