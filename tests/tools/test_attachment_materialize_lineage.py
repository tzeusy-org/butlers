"""bu-jv1moa.64 PR3: a broker-forwarded attachment_materialize call is authorized by its verified
assertion, through the real domain verification middleware.

The tool trusts only a ``VerifiedLineage``, which only the assertion verifier binds, after it has
checked the signature, audience, expiry, capability, replay and the digest over the raw wire
arguments. A lineage that does not cover exactly this tool and these arguments is refused, and
never falls back to a session routing context.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastmcp import FastMCP

from butlers.capability_broker.assertions import ASSERTION_HEADER, AssertionSigner
from butlers.capability_broker.verification import AssertionVerificationMiddleware, DomainVerifier
from butlers.core import goal_lineage
from butlers.core.capability_json import arguments_digest
from butlers.core.goal_lineage import VerifiedLineage, lineage_scope
from butlers.core.tool_call_capture import (
    clear_runtime_session_routing_context,
    reset_current_runtime_session_id,
    set_current_runtime_session_id,
    set_runtime_session_routing_context,
)
from butlers.mcp_wrappers import _SpanWrappingMCP
from butlers.tools.attachment_materialize import MAX_ID_LENGTH, materialize_routed_attachment
from tests.core.test_mcp_wrappers import _ACCEPT, _NOW, _Clock, _keyring

pytestmark = pytest.mark.unit

_FETCHED = {
    "fetched": True,
    "blob_ref": "s3://b/connectors/a.pdf",
    "fetch_error": None,
    "filename": "a.pdf",
    "media_type": "application/pdf",
    "size_bytes": 10,
}
_ARGS = {"message_id": "m1", "attachment_id": "a1"}


@pytest.fixture(autouse=True)
def _reset_enforcement():
    yield
    goal_lineage.set_enforcement(False)


def _payload(args: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    payload = {
        "aud": "switchboard",
        "principal": "goal",
        "goal_id": str(uuid4()),
        "invocation_id": str(uuid4()),
        "depth": 0,
        "origin": "third_party",
        "exposed": True,
        "capability": "attachment_materialize",
        "args_digest": arguments_digest(args),
        "call_id": str(uuid4()),
        "effect_id": None,
        "approval_ref": None,
        "exp": _NOW + 60,
    }
    payload.update(overrides)
    return payload


class _Domain:
    def __init__(self, client, signer, pool):
        self.client, self.signer, self.pool = client, signer, pool

    async def call(self, args: dict[str, Any], assertions: list[str]):
        headers = [("accept", _ACCEPT)] + [(ASSERTION_HEADER, a) for a in assertions]
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "attachment_materialize", "arguments": args},
        }
        return (await self.client.post("/", headers=headers, json=body)).json()["result"]


@asynccontextmanager
async def _domain():
    signer = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
    server = FastMCP("switchboard")
    proxy = _SpanWrappingMCP(server, "switchboard", module_name="switchboard")
    pool = AsyncMock()
    pool.fetchrow.return_value = _FETCHED

    @proxy.tool()
    async def attachment_materialize(message_id: str, attachment_id: str) -> dict[str, Any]:
        return await materialize_routed_attachment(pool, message_id, attachment_id, wait_s=0)

    verifier = DomainVerifier("switchboard", _keyring(signer), clock=_Clock(), started_at=_NOW - 30)
    server.middleware.insert(0, AssertionVerificationMiddleware(verifier))
    goal_lineage.set_enforcement(True)
    app = server.http_app(path="/", stateless_http=True, json_response=True)
    transport = httpx.ASGITransport(app=app)
    with patch("butlers.mcp_wrappers.capture_tool_call"):
        async with (
            app.lifespan(app),
            httpx.AsyncClient(transport=transport, base_url="http://d") as client,
        ):
            yield _Domain(client, signer, pool)


async def test_a_verified_assertion_materializes_the_exact_ref_with_no_session():
    async with _domain() as domain:
        result = await domain.call(_ARGS, [domain.signer.sign(_payload(_ARGS))])
        assert result["structuredContent"]["status"] == "ready"
        domain.pool.fetchrow.assert_awaited()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d, p: AssertionSigner("broker-1", Ed25519PrivateKey.generate()).sign(p),
        lambda d, p: d.signer.sign(
            {**p, "args_digest": arguments_digest({**_ARGS, "attachment_id": "a2"})}
        ),
        lambda d, p: d.signer.sign({**p, "capability": "attachment_read"}),
        lambda d, p: d.signer.sign({**p, "exp": _NOW}),
        lambda d, p: d.signer.sign({**p, "aud": "general"}),
    ],
    ids=["forged-signature", "digest-for-other-args", "other-capability", "expired", "wrong-aud"],
)
async def test_an_invalid_assertion_never_reaches_the_database(mutate):
    async with _domain() as domain:
        result = await domain.call(_ARGS, [mutate(domain, _payload(_ARGS))])
        assert result.get("isError") is True and "assertion_refused" in str(result["content"])
        assert domain.pool.mock_calls == []


async def test_a_missing_assertion_never_reaches_the_database():
    async with _domain() as domain:
        result = await domain.call(_ARGS, [])
        assert result.get("isError") is True
        assert domain.pool.mock_calls == []


async def test_a_replayed_assertion_materializes_once():
    async with _domain() as domain:
        assertion = domain.signer.sign(_payload(_ARGS))
        assert (await domain.call(_ARGS, [assertion]))["structuredContent"]["status"] == "ready"
        again = await domain.call(_ARGS, [assertion])
        assert again.get("isError") is True
        assert domain.pool.fetchrow.await_count == 1


def _lineage(args: dict[str, Any], capability="attachment_materialize") -> VerifiedLineage:
    return VerifiedLineage.from_payload(_payload(args, capability=capability))


async def _bound(lineage, message_id, attachment_id, *, session_context=None):
    pool = AsyncMock()
    pool.fetchrow.return_value = _FETCHED
    token = set_current_runtime_session_id("sw-lineage")
    set_runtime_session_routing_context("sw-lineage", session_context)
    try:
        with lineage_scope(lineage):
            result = await materialize_routed_attachment(pool, message_id, attachment_id, wait_s=0)
    finally:
        clear_runtime_session_routing_context("sw-lineage")
        reset_current_runtime_session_id(token)
    return result, pool


async def test_a_lineage_for_other_arguments_is_refused_and_never_falls_back_to_a_session():
    routed = {"attachments": [{"source_message_id": "m9", "source_attachment_id": "a9"}]}
    # Verified for (m1, a1) but asked for the session-routed (m9, a9): neither authorizes it.
    result, pool = await _bound(_lineage(_ARGS), "m9", "a9", session_context=routed)
    assert result["status"] == "refused" and result["reason"] == "attachment_not_in_request"
    assert pool.mock_calls == []


async def test_a_lineage_for_another_tool_is_refused():
    result, pool = await _bound(_lineage(_ARGS, "attachment_read"), "m1", "a1")
    assert result["reason"] == "attachment_not_in_request" and pool.mock_calls == []


async def test_a_covering_lineage_needs_no_session_context():
    result, pool = await _bound(_lineage(_ARGS), "m1", "a1")
    assert result["status"] == "ready" and pool.fetchrow.await_count >= 1


async def test_a_realistic_long_gmail_id_inside_the_admission_cap_is_materialized():
    long_id = "ANGjdJ" + "x" * 594
    args = {"message_id": "m1", "attachment_id": long_id}
    result, _ = await _bound(_lineage(args), "m1", long_id)
    assert result["status"] == "ready" and len(long_id) < MAX_ID_LENGTH
    too_long = "y" * (MAX_ID_LENGTH + 1)
    refused, _ = await _bound(_lineage({**args, "attachment_id": too_long}), "m1", too_long)
    assert refused == {"status": "refused", "reason": "invalid_reference"}


async def test_the_dashboard_principal_is_not_a_goal_call_and_is_refused():
    args = {"message_id": "m1", "attachment_id": "a1"}
    dashboard = VerifiedLineage.from_payload(
        _payload(
            args,
            principal="dashboard",
            goal_id=None,
            invocation_id=None,
            origin="owner",
            exposed=False,
        )
    )
    assert dashboard.covers("attachment_materialize", args)
    result, pool = await _bound(dashboard, "m1", "a1")
    assert result["reason"] == "attachment_not_in_request" and pool.mock_calls == []
