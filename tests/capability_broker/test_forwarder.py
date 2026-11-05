"""The HTTP forwarder carries the assertion to a verifying domain (bu-pkuny2)."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastmcp import FastMCP

from butlers.capability_broker.assertions import LIFETIME_SECONDS, AssertionSigner
from butlers.capability_broker.contracts import Decision, Reservation
from butlers.capability_broker.forwarder import HttpForwarder
from butlers.capability_broker.verification import (
    AssertionVerificationMiddleware,
    DomainVerifier,
)
from butlers.core import goal_lineage
from butlers.core.capability_json import arguments_digest
from butlers.core.runtime_probe_control.keys import VerifierKey, VerifierKeyring
from butlers.mcp_wrappers import _SpanWrappingMCP
from tests.capability_broker.helpers import capability, principal

pytestmark = pytest.mark.unit

_ARGS = {"text": "note this"}


@pytest.fixture(autouse=True)
def _reset_enforcement():
    yield
    goal_lineage.set_enforcement(False)


def _reservation() -> Reservation:
    cap = capability("relationship_note", "write", "relationship")
    return Reservation(
        principal(exposed=True, origin="third_party"),
        cap,
        Decision("forward"),
        uuid4(),
        uuid4(),
        arguments_digest(_ARGS),
    )


def _assertion(signer: AssertionSigner, reserved: Reservation) -> str:
    p = reserved.principal
    return signer.sign(
        {
            "aud": reserved.capability.domain,
            "goal_id": str(p.goal_id),
            "invocation_id": str(p.invocation_id),
            "depth": p.depth,
            "origin": p.origin,
            "exposed": p.exposed,
            "capability": reserved.capability.name,
            "args_digest": reserved.args_digest,
            "call_id": str(reserved.call_id),
            "effect_id": str(reserved.effect_id),
            "approval_ref": None,
            "exp": int(time.time()) + LIFETIME_SECONDS,
        }
    )


@asynccontextmanager
async def _domain(signer: AssertionSigner, seen: list, *, middleware: bool = True):
    server = FastMCP("relationship")
    proxy = _SpanWrappingMCP(server, "relationship", module_name="relationship")

    @proxy.tool()
    async def relationship_note(text: str) -> dict[str, Any]:
        seen.append(goal_lineage.taint_stamp())
        return {"noted": text}

    keyring = VerifierKeyring(
        VerifierKey(
            signer.kid,
            signer.ed25519.public_key().public_bytes_raw(),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    verifier = DomainVerifier("relationship", keyring, started_at=int(time.time()) - 5)
    if middleware:
        server.middleware.insert(0, AssertionVerificationMiddleware(verifier))
    goal_lineage.set_enforcement(True)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    with patch("butlers.mcp_wrappers.capture_tool_call"):
        async with app.lifespan(app):
            yield HttpForwarder({"relationship": "http://domain/mcp"}, httpx_client_factory=factory)


async def test_forwarded_call_is_verified_and_runs_with_exposed_lineage():
    signer = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
    seen: list = []
    reserved = _reservation()
    async with _domain(signer, seen) as forwarder:
        receipt = await forwarder.dispatch(
            reserved, _ARGS, _assertion(signer, reserved), idempotency_key=None
        )
        assert receipt.status == "committed"
        assert receipt.value == {"noted": "note this"}
        assert seen == [(reserved.principal.goal_id, reserved.principal.invocation_id)]

        replay = await forwarder.dispatch(
            reserved, _ARGS, _assertion(signer, reserved), idempotency_key=None
        )
        assert replay.status == "failed" and len(seen) == 1  # Same call_id: refused.

        forged = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
        other = _reservation()
        refused = await forwarder.dispatch(
            other, _ARGS, _assertion(forged, other), idempotency_key=None
        )
        assert refused.status == "failed" and len(seen) == 1


async def test_a_wrapper_refusal_is_a_failed_effect_never_a_committed_one():
    """Enforced but unverified (no lineage bound), the tool wrapper refuses the call.

    The refusal must reach the broker as an MCP error, so the effect is recorded
    ``failed``; a plain refusal payload would read as a committed result.
    """
    signer = AssertionSigner("broker-1", Ed25519PrivateKey.generate())
    seen: list = []
    reserved = _reservation()
    async with _domain(signer, seen, middleware=False) as forwarder:
        receipt = await forwarder.dispatch(
            reserved, _ARGS, _assertion(signer, reserved), idempotency_key=None
        )
    assert receipt.status == "failed" and receipt.value is None
    assert seen == []


async def test_unreachable_domain_is_not_received():
    def refuse(**kwargs):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    forwarder = HttpForwarder({"relationship": "http://domain/mcp"}, httpx_client_factory=refuse)
    receipt = await forwarder.dispatch(_reservation(), _ARGS, "a.b.c", idempotency_key="k")
    assert receipt.status == "not_received"
