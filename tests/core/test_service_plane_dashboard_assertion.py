"""Switchboard forwards a dashboard call with a dashboard assertion (RFC 0038 D7).

The forwarded call reaches a real verifying domain: the domain's assertion
middleware checks the assertion against the raw arguments it received and binds
the owner's goalless lineage for exactly that call. Anything else is refused by
the domain before its handler runs.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastmcp import FastMCP

from butlers.capability_broker.assertions import ASSERTION_HEADER, AssertionSigner
from butlers.capability_broker.verification import AssertionVerificationMiddleware, DomainVerifier
from butlers.core import goal_lineage
from butlers.core.runtime_probe_control.keys import VerifierKey, VerifierKeyring
from butlers.core.service_plane import dashboard_route as dr
from butlers.mcp_wrappers import _SpanWrappingMCP

pytestmark = pytest.mark.unit

_ARGS = {"text": "note this"}
_URL = "http://relationship/mcp"


@pytest.fixture(autouse=True)
def _reset_enforcement():
    yield
    goal_lineage.set_enforcement(False)


@pytest.fixture
def signer() -> AssertionSigner:
    return AssertionSigner("broker-1", Ed25519PrivateKey.generate())


@asynccontextmanager
async def _domain(signer: AssertionSigner, seen: list):
    server = FastMCP("relationship")
    proxy = _SpanWrappingMCP(server, "relationship", module_name="relationship")

    @proxy.tool()
    async def relationship_note(text: str) -> dict[str, Any]:
        seen.append(goal_lineage.current_lineage())
        return {"noted": text}

    keyring = VerifierKeyring(
        VerifierKey(
            signer.kid,
            signer.ed25519.public_key().public_bytes_raw(),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    verifier = DomainVerifier("relationship", keyring, started_at=int(time.time()) - 5)
    server.middleware.insert(0, AssertionVerificationMiddleware(verifier))
    goal_lineage.set_enforcement(True)
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    with patch("butlers.mcp_wrappers.capture_tool_call"):
        async with app.lifespan(app):
            yield factory


async def test_the_domain_runs_the_call_as_the_owner_with_no_goal(signer) -> None:
    seen: list = []
    async with _domain(signer, seen) as factory:
        call = dr.assertion_call_fn(signer, "relationship", _ARGS, httpx_client_factory=factory)
        result = await call(_URL, "relationship_note", dict(_ARGS))

    assert result["isError"] is False
    assert result["structuredContent"] == {"noted": "note this"}
    (lineage,) = seen
    assert (lineage.principal, lineage.origin, lineage.goal_id) == ("dashboard", "owner", None)
    assert lineage.capability == "relationship_note"


async def test_the_assertion_covers_the_arguments_sent_not_the_injected_carrier(signer) -> None:
    seen: list = []
    async with _domain(signer, seen) as factory:
        call = dr.assertion_call_fn(signer, "relationship", _ARGS, httpx_client_factory=factory)
        injected = {**_ARGS, "trace_context": {"traceparent": "00-x"}}
        result = await call(_URL, "relationship_note", injected)

    assert result["isError"] is False and len(seen) == 1


async def test_each_forwarded_call_carries_a_fresh_assertion(signer) -> None:
    seen: list = []
    async with _domain(signer, seen) as factory:
        call = dr.assertion_call_fn(signer, "relationship", _ARGS, httpx_client_factory=factory)
        first = await call(_URL, "relationship_note", dict(_ARGS))
        second = await call(_URL, "relationship_note", dict(_ARGS))

    assert (first["isError"], second["isError"]) == (False, False)
    assert len({lineage.call_id for lineage in seen}) == 2


@pytest.mark.parametrize("audience", ["health", "switchboard"])
async def test_an_assertion_for_another_domain_is_refused_before_the_handler(
    signer, audience
) -> None:
    seen: list = []
    async with _domain(signer, seen) as factory:
        call = dr.assertion_call_fn(signer, audience, _ARGS, httpx_client_factory=factory)
        result = await call(_URL, "relationship_note", dict(_ARGS))

    assert result["isError"] is True and seen == []


async def test_an_assertion_from_another_key_is_refused_before_the_handler(signer) -> None:
    seen: list = []
    forged = AssertionSigner(signer.kid, Ed25519PrivateKey.generate())
    async with _domain(signer, seen) as factory:
        call = dr.assertion_call_fn(forged, "relationship", _ARGS, httpx_client_factory=factory)
        result = await call(_URL, "relationship_note", dict(_ARGS))

    assert result["isError"] is True and seen == []


async def test_without_an_assertion_the_verifying_domain_refuses(signer) -> None:
    """The 3b path forwards as Switchboard itself: a verifying domain refuses it."""
    from fastmcp.client.transports import StreamableHttpTransport

    from butlers.mcp_liveness import LivenessClient

    seen: list = []
    async with _domain(signer, seen) as factory:

        async def call_tool(endpoint: str, tool: str, args: dict) -> Any:
            transport = StreamableHttpTransport(endpoint, httpx_client_factory=factory)
            async with LivenessClient(transport) as client:
                return await client.call_tool(tool, args, raise_on_error=False)

        result = await dr.mcp_result_call_fn(call_tool, _ARGS)(
            _URL, "relationship_note", dict(_ARGS)
        )

    assert result["isError"] is True and seen == []


class _Routing:
    """Stands in for Switchboard's route(): records the call_fn it is handed."""

    def __init__(self) -> None:
        self.headers: list[dict[str, str]] = []

    async def route(self, pool, butler, tool, args, *, source_butler, call_fn):
        recorder = self

        class _Transport:
            def __init__(self, url, *, headers=None, httpx_client_factory=None):
                recorder.headers.append(dict(headers or {}))
                raise ConnectionError("stop before connecting")

        with patch.object(dr, "StreamableHttpTransport", _Transport):
            try:
                await call_fn(_URL, tool, args)
            except ConnectionError:
                pass
        return {"result": {"content": [], "isError": False}}

    async def _call_tool_with_router_client(self, endpoint, tool, args, *, resend_after_send):
        assert resend_after_send is False
        self.headers.append({})
        return type("R", (), {"content": [], "structured_content": None, "is_error": False})()


def _built_dispatch(**kwargs: Any) -> Any:
    captured: dict[str, Any] = {}

    def build(**route_kwargs: Any):
        captured.update(route_kwargs)
        return object()

    with patch.object(dr, "build_dashboard_call_route", side_effect=build):
        dr.build_switchboard_dashboard_call_route(object(), **kwargs)
    return captured["dispatch"]


async def _dispatch(routing: _Routing, **kwargs: Any) -> None:
    dispatch = _built_dispatch(**kwargs)
    with patch.object(dr.importlib, "import_module", return_value=routing):
        await dispatch("relationship", "relationship_note", dict(_ARGS))


async def test_with_a_signer_switchboard_forwards_with_one_assertion(signer) -> None:
    routing = _Routing()
    await _dispatch(routing, assertion_signer=signer)

    (headers,) = routing.headers
    assert list(headers) == [ASSERTION_HEADER]


async def test_a_signer_signs_even_with_the_unsigned_opt_in(signer) -> None:
    routing = _Routing()
    await _dispatch(routing, assertion_signer=signer, unsigned_until_broker_cutover=True)

    (headers,) = routing.headers
    assert list(headers) == [ASSERTION_HEADER]


async def test_the_unsigned_opt_in_forwards_as_switchboard_without_resend() -> None:
    """The _Routing fake asserts resend_after_send is False."""
    routing = _Routing()
    await _dispatch(routing, unsigned_until_broker_cutover=True)

    assert routing.headers == [{}]


def test_without_a_signer_or_the_opt_in_there_is_no_forwarding_path() -> None:
    assert _built_dispatch() is None
    assert dr.switchboard_dashboard_dispatch(object()) is None
