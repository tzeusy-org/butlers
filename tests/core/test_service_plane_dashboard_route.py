"""Switchboard's dashboard service-call route (RFC 0038 D7, mechanism A).

Every refusal must happen before the target is contacted, so each negative
case asserts the dispatch never ran. The happy path exists to prove the
negatives refuse something that would otherwise have been routed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastmcp import Client, FastMCP
from starlette.applications import Starlette

from butlers.core.runtime_probe_control.keys import (
    VerifierSnapshot,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.core.runtime_probe_control.service_call import (
    sign_service_call,
    verify_deployed_service_call,
)
from butlers.core.service_plane import dashboard_route as dr
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
_KID = "probe-2026-11a"
_PRESENTABLE = "state_set"
_SERVICE_ONLY = next(n for n, p in TOOL_PRESENTATION_BY_NAME.items() if not p.llm_presentable)
_ARGS = {"key": "k", "value": 1}


def _encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


@pytest.fixture
def keys():
    seed, public_key = synthetic_keypair()
    signer = parse_signer_document(
        _encode(signer_document(seed, kid=_KID, sign_from=_NOW - timedelta(days=1)))
    )
    keyring = parse_verifier_keyring_document(
        _encode(
            keyring_document(
                current_entry(public_key, kid=_KID, sign_from=_NOW - timedelta(days=1))
            )
        )
    )
    return signer, keyring


class _Receipts:
    def __init__(self) -> None:
        self.claimed: set[bytes] = set()

    async def claim(self, *, nonce: bytes, kid: str, expires_at: datetime) -> bool:
        if nonce in self.claimed:
            return False
        self.claimed.add(nonce)
        return True


class _Harness:
    def __init__(self, keyring, *, enabled: bool = True, outcome: Any = None) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.receipts = _Receipts()
        self.enabled = enabled
        self.keyring = keyring
        self.outcome = outcome or {
            "result": {"content": [{"type": "text", "text": '{"ok": true}'}], "isError": False}
        }

        async def dispatch(butler: str, tool: str, args: dict) -> dict:
            self.calls.append((butler, tool, args))
            return self.outcome

        route = dr.build_dashboard_call_route(
            enabled=lambda: self.enabled,
            receipts=self.receipts,
            dispatch=dispatch,
            verify=lambda c, now: verify_deployed_service_call(
                c, now=now, verifier=lambda: VerifierSnapshot(keyring=self.keyring)
            ),
            clock=lambda: _NOW,
        )
        self.app = Starlette(routes=[route])

    async def post(self, *, headers: dict | None = None, body: Any = None, url: str = "") -> Any:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://sb") as client:
            return await client.post(
                dr.SERVICE_CALL_PATH + url,
                headers=headers or {},
                content=body if isinstance(body, bytes) else json.dumps(body).encode(),
            )


def _capability(signer, *, butler="health", tool=_PRESENTABLE, args=_ARGS) -> dict:
    compact = sign_service_call(signer, butler=butler, tool=tool, args=args, now=_NOW)
    return {"Authorization": f"Bearer {compact}"}


def _body(butler="health", tool=_PRESENTABLE, args=_ARGS) -> dict:
    return {"butler": butler, "tool": tool, "args": args}


async def test_a_bound_call_is_routed_once_and_returns_the_whole_result(keys) -> None:
    signer, keyring = keys
    harness = _Harness(keyring)

    response = await harness.post(headers=_capability(signer), body=_body())

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "result": harness.outcome["result"]}
    assert harness.calls == [("health", _PRESENTABLE, _ARGS)]


async def test_the_same_capability_twice_is_a_replay(keys) -> None:
    signer, keyring = keys
    harness = _Harness(keyring)
    headers = _capability(signer)

    await harness.post(headers=headers, body=_body())
    second = await harness.post(headers=headers, body=_body())

    assert (second.status_code, second.json()["code"]) == (409, "replay")
    assert len(harness.calls) == 1


async def test_switch_off_is_not_found(keys) -> None:
    signer, keyring = keys
    harness = _Harness(keyring, enabled=False)

    response = await harness.post(headers=_capability(signer), body=_body())

    assert (response.status_code, response.json()["code"]) == (404, "not_found")
    assert harness.calls == [] and harness.receipts.claimed == set()


@pytest.mark.parametrize(
    "body",
    [
        {"butler": "health", "tool": _PRESENTABLE},
        {**_body(), "extra": 1},
        _body(args=[1]),
        _body(butler=7),
        _body(args={**_ARGS, "__switchboard_route_context": {"request_id": "x"}}),
        b"not json",
    ],
)
async def test_a_malformed_body_is_refused(keys, body) -> None:
    signer, keyring = keys
    harness = _Harness(keyring)

    response = await harness.post(headers=_capability(signer), body=body)

    assert (response.status_code, response.json()["code"]) == (400, "args_invalid")
    assert harness.calls == []


@pytest.mark.parametrize(
    ("headers", "url"),
    [
        ({}, ""),
        ({"Authorization": "Basic abc"}, ""),
        ({"Authorization": "Bearer not.a.capability"}, ""),
        ("cookie", ""),
        ("valid", "?x=1"),
    ],
)
async def test_a_missing_or_badly_carried_capability_is_unauthorized(keys, headers, url) -> None:
    signer, keyring = keys
    harness = _Harness(keyring)
    if headers == "cookie":
        headers = {**_capability(signer), "Cookie": "session=1"}
    elif headers == "valid":
        headers = _capability(signer)

    response = await harness.post(headers=headers, body=_body(), url=url)

    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    assert harness.calls == [] and harness.receipts.claimed == set()


@pytest.mark.parametrize(
    "body",
    [
        _body(butler="finance"),
        _body(tool="state_delete"),
        _body(args={"key": "k", "value": 2}),
        _body(args={**_ARGS, "trace_context": {}}),
    ],
)
async def test_a_capability_for_another_call_is_unauthorized(keys, body) -> None:
    signer, keyring = keys
    harness = _Harness(keyring)

    response = await harness.post(headers=_capability(signer), body=body)

    assert (response.status_code, response.json()["code"]) == (401, "unauthorized")
    assert harness.calls == [] and harness.receipts.claimed == set()


async def test_no_keyring_is_retryable_auth_unavailable(keys) -> None:
    signer, _ = keys
    harness = _Harness(None)

    response = await harness.post(headers=_capability(signer), body=_body())

    assert (response.status_code, response.json()["code"]) == (503, "auth_unavailable")
    assert harness.calls == []


async def test_a_service_only_tool_does_not_take_this_path(keys) -> None:
    signer, keyring = keys
    harness = _Harness(keyring)

    response = await harness.post(
        headers=_capability(signer, tool=_SERVICE_ONLY), body=_body(tool=_SERVICE_ONLY)
    )

    assert (response.status_code, response.json()["code"]) == (403, "not_presentable")
    assert harness.calls == [] and harness.receipts.claimed == set()


@pytest.mark.parametrize("retryable", [True, False])
async def test_an_unreachable_target_carries_its_retryable_flag(keys, retryable) -> None:
    signer, keyring = keys
    harness = _Harness(
        keyring, outcome={"error": "Butler 'health' is stale", "retryable": retryable}
    )

    response = await harness.post(headers=_capability(signer), body=_body())

    assert response.status_code == 503
    assert response.json() == {
        "status": "refused",
        "code": "target_unavailable",
        "retryable": retryable,
    }
    assert "stale" not in response.text


# ---------------------------------------------------------------------------
# The route() call_fn: the target's whole result, against a real FastMCP server
# ---------------------------------------------------------------------------


def _server() -> FastMCP:
    server = FastMCP("target")

    @server.tool
    def state_set(key: str, value: int) -> dict:
        if value < 0:
            raise ValueError("value must be non-negative")
        return {"key": key, "value": value}

    @server.tool
    def echo_trace(trace_context: dict) -> dict:
        return trace_context

    return server


async def _call_fn(original_args: dict):
    server = _server()

    async def call_tool(endpoint: str, tool: str, args: dict) -> Any:
        async with Client(server) as client:
            return await client.call_tool(tool, args, raise_on_error=False)

    return dr.mcp_result_call_fn(call_tool, original_args)


async def test_the_call_fn_drops_the_injected_trace_carrier() -> None:
    call = await _call_fn(_ARGS)

    result = await call("http://t", "state_set", {**_ARGS, "trace_context": {"traceparent": "x"}})

    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == _ARGS
    assert result["structuredContent"] == _ARGS


async def test_the_call_fn_keeps_a_trace_context_the_dashboard_sent() -> None:
    args = {"trace_context": {"mine": "1"}}
    call = await _call_fn(args)

    result = await call("http://t", "echo_trace", dict(args))

    assert result["structuredContent"] == {"mine": "1"}


async def test_the_call_fn_carries_a_tool_error_as_a_result_not_a_failure() -> None:
    call = await _call_fn({"key": "k", "value": -1})

    result = await call("http://t", "state_set", {"key": "k", "value": -1})

    assert result["isError"] is True
    assert "non-negative" in result["content"][0]["text"]


# ---------------------------------------------------------------------------
# A dashboard write is never resent once it may have reached the target
# ---------------------------------------------------------------------------


class _Peer:
    """The cached router client: each call_tool fails with ``failure`` after the request went out."""

    def __init__(self, failure: Exception) -> None:
        self.failure = failure
        self.calls = 0

    async def call_tool(self, tool: str, args: dict, *, raise_on_error: bool) -> Any:
        self.calls += 1
        raise self.failure


@pytest.fixture
def routing(monkeypatch):
    import importlib
    from unittest.mock import AsyncMock

    module = importlib.import_module("butlers.tools.switchboard.routing.route")
    monkeypatch.delenv("BUTLERS_RECEIVER_DERIVED_ROUTE_CUTOVER", raising=False)
    monkeypatch.setattr(
        module,
        "resolve_routing_target",
        AsyncMock(return_value=({"endpoint_url": "http://localhost:41104/mcp"}, None)),
    )
    monkeypatch.setattr(module, "_log_routing", AsyncMock())
    monkeypatch.setattr(module, "_record_confirmed_route", AsyncMock())
    return module


def _connect(monkeypatch, routing, peer: _Peer) -> None:
    async def cached(endpoint_url: str, *, reconnect: bool) -> _Peer:
        return peer

    monkeypatch.setattr(routing, "_get_cached_router_client", cached)


async def test_a_timeout_after_send_is_503_and_the_write_is_sent_once(
    monkeypatch, routing, keys
) -> None:
    signer, keyring = keys
    peer = _Peer(httpx.ReadTimeout("timed out after the request was sent"))
    _connect(monkeypatch, routing, peer)
    route = dr.build_dashboard_call_route(
        enabled=lambda: True,
        receipts=_Receipts(),
        dispatch=dr.switchboard_dashboard_dispatch("POOL"),
        verify=lambda c, now: verify_deployed_service_call(
            c, now=now, verifier=lambda: VerifierSnapshot(keyring=keyring)
        ),
        clock=lambda: _NOW,
    )
    transport = httpx.ASGITransport(app=Starlette(routes=[route]))
    async with httpx.AsyncClient(transport=transport, base_url="http://sb") as client:
        response = await client.post(
            dr.SERVICE_CALL_PATH, headers=_capability(signer), json=_body()
        )

    assert response.status_code == 503
    assert response.json()["code"] == "target_unavailable"
    assert peer.calls == 1
    routing._record_confirmed_route.assert_not_awaited()


async def test_other_route_callers_still_resend_once(monkeypatch, routing) -> None:
    """Every other route() caller keeps the existing reconnect-and-resend policy."""
    peer = _Peer(httpx.ReadTimeout("timed out after the request was sent"))
    _connect(monkeypatch, routing, peer)

    outcome = await routing.route("POOL", "health", _PRESENTABLE, dict(_ARGS))

    assert "error" in outcome and peer.calls == 2


async def test_a_failure_before_send_still_reconnects_once(monkeypatch, routing) -> None:
    peer = _Peer(httpx.ConnectError("refused before the request was sent"))
    _connect(monkeypatch, routing, peer)

    outcome = await dr.switchboard_dashboard_dispatch("POOL")("health", _PRESENTABLE, dict(_ARGS))

    assert "result" not in outcome and peer.calls == 2
