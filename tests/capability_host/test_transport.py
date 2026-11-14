"""Hop bearer, request shape and exempt methods (REQ-public-mcp-authorization-001 to -003)."""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.capability_host.helpers import (
    BEARER,
    NEXT_BEARER,
    Env,
    call_body,
    error_code,
    rpc,
)

pytestmark = pytest.mark.unit

UNAUTHORIZED = {"code": -32001, "message": "Unauthorized", "data": {"reason": "unauthorized"}}


def _refused(response) -> bool:
    return response.status == 200 and json.loads(response.body).get("error") == UNAUTHORIZED


# -- REQ-public-mcp-authorization-001 hop bearer --


async def test_missing_bearer_is_refused_before_the_body_is_read(env: Env):
    response = await env.send(b"{not json", bearer=None, assertion=None)
    assert response.status == 401 and response.body == b""
    assert env.body_reads == 0
    assert env.host.metrics.get("bearer_refusals") == 1
    assert env.lines == []  # a bearer refusal never reaches the audit line


async def test_wrong_bearer_is_refused_and_runs_no_tool(env: Env):
    response = await env.send(call_body("entity_lookup"), bearer="guessed-bearer")
    assert response.status == 401
    assert env.body_reads == 0 and env.calls == [] and env.projection.acquired == 0
    assert env.host.metrics.get("bearer_refusals") == 1
    assert all("guessed-bearer" not in line for line in env.lines)


@pytest.mark.parametrize(
    "header",
    [[("Authorization", "Bearer ")], [("Authorization", "bearer " + BEARER)]],
)
async def test_malformed_bearer_headers_are_refused(env: Env, header):
    response = await env.send(rpc("ping"), bearer=None, assertion=None, headers=header)
    assert response.status == 401


async def test_two_authorization_headers_are_refused(env: Env):
    extra = [("authorization", f"Bearer {BEARER}")]
    response = await env.send(rpc("ping"), assertion=None, headers=extra)
    assert response.status == 401


async def test_bearer_rotation_accepts_both_digests_then_drops_the_old(env: Env):
    env.write("bearer/digest_next", hashlib.sha256(NEXT_BEARER.encode()).hexdigest())
    env.reload()
    assert (await env.send(rpc("ping"), assertion=None)).status == 200
    assert (await env.send(rpc("ping"), bearer=NEXT_BEARER, assertion=None)).status == 200
    env.write("bearer/digest", hashlib.sha256(NEXT_BEARER.encode()).hexdigest())
    (env.root / "bearer/digest_next").unlink()
    env.reload()
    assert (await env.send(rpc("ping"), assertion=None)).status == 401
    assert (await env.send(rpc("ping"), bearer=NEXT_BEARER, assertion=None)).status == 200


async def test_an_unreadable_digest_admits_nobody(env: Env):
    env.write("bearer/digest", "SHA256:" + "A" * 64)
    env.reload()
    assert (await env.send(rpc("ping"), assertion=None)).status == 401


async def test_bearer_alone_cannot_call_a_tool(env: Env):
    response = await env.send(call_body("entity_lookup", {"q": "x"}), assertion=None)
    assert _refused(response)
    assert env.calls == [] and env.projection.acquired == 0


# -- REQ-public-mcp-authorization-002 request shape --


async def test_oversized_body_is_refused(env: Env):
    body = call_body("entity_lookup", {"q": "x" * (1024 * 1024)})
    response = await env.send(body)
    assert _refused(response) and env.calls == []
    declared = await env.send(rpc("ping"), headers=[("Content-Length", str(2 * 1024 * 1024))])
    assert _refused(declared) and env.body_reads == 0


async def test_batch_request_is_refused(env: Env):
    body = b"[" + call_body("entity_lookup") + b"," + call_body("trips") + b"]"
    assert _refused(await env.send(body, assertion=None))
    assert env.calls == []


async def test_duplicate_keys_are_refused(env: Env):
    body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"trips","name":"entity_lookup","arguments":{}}}'
    assert _refused(await env.send(body, mth="tools/call", tool="trips"))
    assert env.calls == []


@pytest.mark.parametrize(
    "body",
    [
        b'{"jsonrpc":"2.0","id":1,"method":"ping","x":NaN}',
        b'{"jsonrpc":"2.0","id":1,"method":"ping","extra":1}',
        b'{"jsonrpc":"1.0","id":1,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":true,"method":"ping"}',
        b'{"jsonrpc":"2.0","id":1,"method":"ping","params":[]}',
        b"\xff\xfe",
    ],
)
async def test_non_standard_bodies_are_refused(env: Env, body: bytes):
    assert _refused(await env.send(body, assertion=None))


async def test_no_sse_get_stream(env: Env):
    response = await env.send(b"", method="GET", assertion=None)
    assert response.status == 405 and env.body_reads == 0


# -- REQ-public-mcp-authorization-003 assertion for data methods --


async def test_handshake_works_without_an_assertion_and_opens_no_connection(tmp_path):
    static = {
        "initialize": lambda params: {"capabilities": {"tools": {}}},
        "tools/list": lambda params: {"tools": []},
    }
    env = Env(tmp_path, static=static)
    init = json.loads((await env.send(rpc("initialize", {}), assertion=None)).body)
    listed = json.loads((await env.send(rpc("tools/list"), assertion=None)).body)
    assert init["result"] == {"capabilities": {"tools": {}}}
    assert listed["result"] == {"tools": []}
    note = await env.send(rpc("notifications/initialized", rid=None), assertion=None)
    assert note.status == 202
    assert env.projection.acquired == 0


async def test_tool_call_without_assertion_is_refused_with_no_query(env: Env):
    response = await env.send(call_body("entity_lookup", {"q": "a"}), assertion=None)
    assert _refused(response)
    assert env.projection.queries == [] and env.projection.acquired == 0


async def test_a_bad_assertion_on_an_exempt_method_is_refused(env: Env):
    good = await env.send(rpc("tools/list"))
    assert json.loads(good.body)["error"]["code"] == -32601  # verified, then not registered
    from tests.capability_host.helpers import claims, sign

    body = rpc("ping")
    forged = sign(claims(body), seed=bytes(32))
    assert _refused(await env.send(body, assertion=forged))


async def test_unknown_methods_need_an_assertion_and_are_then_not_found(env: Env):
    assert _refused(await env.send(rpc("resources/list"), assertion=None))
    found = json.loads((await env.send(rpc("resources/list"))).body)
    assert found["error"]["code"] == -32601


async def test_a_served_call_returns_structured_and_text_content(env: Env):
    message = await env.tool("entity_lookup", {"q": "Avery"})
    result = message["result"]
    assert error_code(message) is None and result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
