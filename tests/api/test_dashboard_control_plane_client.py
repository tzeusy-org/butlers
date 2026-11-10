"""Dashboard tool calls through the control plane (RFC 0038 D7, mechanism A).

End to end with real pieces: the dashboard's ``DashboardButlerClient``, the
signed service-call client, Switchboard's route over ASGI and a real FastMCP
target. Two target instances count their own calls, so each test can prove
which path a call took --- direct to the butler, or through Switchboard.
"""

from __future__ import annotations

import functools
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.applications import Starlette

from butlers.api.deps import ButlerUnreachableError, DashboardButlerClient, control_plane_carries
from butlers.core.owner_creation import META_KEY
from butlers.core.runtime_probe_control.keys import (
    SignerSnapshot,
    VerifierSnapshot,
    parse_signer_document,
    parse_verifier_keyring_document,
)
from butlers.core.runtime_probe_control.service_call import verify_deployed_service_call
from butlers.core.service_plane import dashboard_client as dclient
from butlers.core.service_plane import dashboard_route as dr
from butlers.core.tool_presentation_inventory import TOOL_PRESENTATION_BY_NAME
from butlers.testing.runtime_probe_control import (
    current_entry,
    keyring_document,
    signer_document,
    synthetic_keypair,
)

pytestmark = pytest.mark.unit

_KID = "probe-2026-11a"
_SERVICE_ONLY = "status"
assert not TOOL_PRESENTATION_BY_NAME[_SERVICE_ONLY].llm_presentable
assert TOOL_PRESENTATION_BY_NAME["state_set"].llm_presentable
assert TOOL_PRESENTATION_BY_NAME["schedule_create"].llm_presentable


def _target(calls: list[str]) -> FastMCP:
    server = FastMCP("health")

    @server.tool
    def state_set(key: str, value: int) -> dict:
        calls.append("state_set")
        if value < 0:
            raise ValueError("value must be non-negative")
        return {"key": key, "value": value}

    @server.tool
    def status() -> dict:
        calls.append("status")
        return {"ok": True}

    @server.tool
    def schedule_create(name: str) -> dict:
        calls.append("schedule_create")
        return {"name": name}

    return server


#: One pinned instant: the client signs at it and the route verifies at it.
_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)


class _Stack:
    def __init__(self, monkeypatch, *, signer: bool = True, outcome: Any = None) -> None:
        now = _NOW
        seed, public_key = synthetic_keypair()
        encode = lambda d: json.dumps(d).encode()  # noqa: E731
        signer_key = parse_signer_document(
            encode(signer_document(seed, kid=_KID, sign_from=now - timedelta(days=1)))
        )
        keyring = parse_verifier_keyring_document(
            encode(
                keyring_document(
                    current_entry(public_key, kid=_KID, sign_from=now - timedelta(days=1))
                )
            )
        )
        self.direct: list[str] = []
        self.routed: list[str] = []
        self.routed_to: list[tuple[str, str]] = []
        routed_server = _target(self.routed)

        async def call_tool(endpoint: str, tool: str, args: dict) -> Any:
            from fastmcp import Client

            async with Client(routed_server) as client:
                return await client.call_tool(tool, args, raise_on_error=False)

        async def dispatch(butler: str, tool: str, args: dict) -> dict:
            self.routed_to.append((butler, tool))
            if outcome is not None:
                return outcome
            return {"result": await dr.mcp_result_call_fn(call_tool, args)("http://t", tool, args)}

        claimed: set[bytes] = set()

        class _Receipts:
            async def claim(self, *, nonce: bytes, kid: str, expires_at: datetime) -> bool:
                if nonce in claimed:
                    return False
                claimed.add(nonce)
                return True

        route = dr.build_dashboard_call_route(
            enabled=lambda: True,
            receipts=_Receipts(),
            dispatch=dispatch,
            verify=lambda c, now: verify_deployed_service_call(
                c, now=now, verifier=lambda: VerifierSnapshot(keyring=keyring)
            ),
            clock=lambda: _NOW,
        )
        monkeypatch.setenv("BUTLERS_SERVICE_PLANE_AUTH", "1")
        monkeypatch.setattr(
            dclient,
            "call_via_control_plane",
            functools.partial(
                dclient.call_via_control_plane,
                signer=lambda: SignerSnapshot(
                    signer=signer_key if signer else None,
                    keyring=keyring,
                    matched=keyring.current,
                ),
                base_url=lambda: "http://switchboard:41100",
                now=_NOW,
                transport=httpx.ASGITransport(app=Starlette(routes=[route])),
            ),
        )
        self.client = DashboardButlerClient(_target(self.direct), butler_name="health")


async def test_a_presentable_tool_goes_through_switchboard_not_the_butler(monkeypatch) -> None:
    stack = _Stack(monkeypatch)
    async with stack.client as client:
        result = await client.call_tool("state_set", {"key": "k", "value": 3})

    assert result.data == {"key": "k", "value": 3}
    assert result.structured_content == {"key": "k", "value": 3}
    assert stack.routed == ["state_set"] and stack.direct == []
    assert stack.routed_to == [("health", "state_set")]


async def test_a_routed_tool_error_raises_tool_error_as_a_direct_call_would(monkeypatch) -> None:
    stack = _Stack(monkeypatch)
    async with stack.client as client:
        with pytest.raises(ToolError, match="non-negative"):
            await client.call_tool("state_set", {"key": "k", "value": -1})
        raw = await client.call_tool("state_set", {"key": "k", "value": -1}, raise_on_error=False)

    assert raw.is_error is True
    assert stack.direct == []


async def test_a_service_only_tool_stays_direct(monkeypatch) -> None:
    stack = _Stack(monkeypatch)
    async with stack.client as client:
        result = await client.call_tool(_SERVICE_ONLY, {})

    assert result.data == {"ok": True}
    assert stack.direct == [_SERVICE_ONLY] and stack.routed == []


async def test_switch_off_every_tool_stays_direct(monkeypatch) -> None:
    stack = _Stack(monkeypatch)
    monkeypatch.delenv("BUTLERS_SERVICE_PLANE_AUTH")
    async with stack.client as client:
        await client.call_tool("state_set", {"key": "k", "value": 1})

    assert stack.direct == ["state_set"] and stack.routed == []


async def test_owner_creation_stays_direct_only_with_exactly_its_claim(monkeypatch) -> None:
    stack = _Stack(monkeypatch)
    claim = {META_KEY: {"nonce": "n"}}
    assert not control_plane_carries("schedule_create", claim)
    assert control_plane_carries("schedule_create", None)
    assert control_plane_carries("state_set", claim)

    async with stack.client as client:
        await client.call_tool("schedule_create", {"name": "x"})
        assert stack.routed == ["schedule_create"] and stack.direct == []
        with pytest.raises(ToolError, match="no request meta"):
            await client.call_tool("schedule_create", {"name": "x"}, meta={**claim, "other": 1})
        with pytest.raises(ToolError, match="no request meta"):
            await client.call_tool("state_set", {"key": "k", "value": 1}, meta=claim)

    assert stack.direct == [] and stack.routed == ["schedule_create"]


async def test_no_signer_fails_closed_as_unreachable_and_sends_nothing(monkeypatch) -> None:
    stack = _Stack(monkeypatch, signer=False)
    async with stack.client as client:
        with pytest.raises(ButlerUnreachableError) as raised:
            await client.call_tool("state_set", {"key": "k", "value": 1})

    assert isinstance(raised.value.cause, dclient.ServiceCallUnavailable)
    assert stack.direct == [] and stack.routed_to == []


async def test_an_unreachable_target_is_unreachable_not_a_tool_error(monkeypatch) -> None:
    stack = _Stack(monkeypatch, outcome={"error": "stale", "retryable": True})
    async with stack.client as client:
        with pytest.raises(ButlerUnreachableError, match="503, target_unavailable"):
            await client.call_tool("state_set", {"key": "k", "value": 1})

    assert stack.direct == []


async def test_an_unreachable_switchboard_is_unreachable(monkeypatch) -> None:
    stack = _Stack(monkeypatch)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(
        dclient,
        "call_via_control_plane",
        functools.partial(
            dclient.call_via_control_plane.func,
            **{
                **dclient.call_via_control_plane.keywords,
                "transport": httpx.MockTransport(refuse),
            },
        ),
    )
    async with stack.client as client:
        with pytest.raises(ButlerUnreachableError, match="could not reach"):
            await client.call_tool("state_set", {"key": "k", "value": 1})

    assert stack.direct == []


async def test_non_json_arguments_are_normalised_before_the_digest(monkeypatch) -> None:
    """A UUID argument is sent as MCP would send it, so the digest still binds."""
    import uuid

    stack = _Stack(monkeypatch)
    async with stack.client as client:
        with pytest.raises(ToolError):
            # The tool wants an int; the call still reaches the routed target.
            await client.call_tool("state_set", {"key": uuid.uuid4(), "value": "x"})

    assert stack.routed_to == [("health", "state_set")]
