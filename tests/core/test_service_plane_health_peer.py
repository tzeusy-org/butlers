"""The Health peer verifies a workload before reading a medication snapshot."""

from pathlib import Path
from unittest.mock import AsyncMock

import certifi
import httpx
import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams
from starlette.requests import Request

from butlers.core.service_plane import health_peer
from butlers.core.service_plane.workload_identity import DAEMON_HEADER, RELAY_AUDIENCE, TOKEN_HEADER
from tests.core.test_service_plane_workload_identity import _claims, _token, _verifier

pytestmark = pytest.mark.unit

HEALTH_ORIGIN = "https://butlers-up:41103"


@pytest.mark.parametrize(
    ("scheme", "listener", "sender", "audience", "admitted"),
    [
        ("http", "127.0.0.1", None, None, True),
        ("https", "192.0.2.81", "switchboard", health_peer.HEALTH_PEER_AUDIENCE, True),
        ("https", "192.0.2.81", None, None, False),
        ("https", "192.0.2.81", "switchboard", RELAY_AUDIENCE, False),
        ("https", "192.0.2.81", "health", health_peer.HEALTH_PEER_AUDIENCE, False),
        ("http", "192.0.2.81", "switchboard", health_peer.HEALTH_PEER_AUDIENCE, False),
    ],
)
async def test_health_peer_checks_socket_and_token_before_handler(
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    listener: str,
    sender: str | None,
    audience: str | None,
    admitted: bool,
) -> None:
    headers: list[tuple[bytes, bytes]] = []
    if sender is not None:
        headers.extend(
            [
                (DAEMON_HEADER.encode(), sender.encode()),
                (TOKEN_HEADER.encode(), _token(_claims(aud=[audience])).encode()),
            ]
        )
    request = Request(
        {"type": "http", "scheme": scheme, "server": (listener, 41103), "headers": headers}
    )
    monkeypatch.setattr(health_peer, "get_http_request", lambda: request)
    verifier = _verifier(health_peer.HEALTH_PEER_AUDIENCE)
    monkeypatch.setattr(health_peer, "cached_verifier", lambda _: verifier)
    admission = health_peer.HealthMedicationPeerAdmission()
    context = MiddlewareContext(message=CallToolRequestParams(name=health_peer.MEDICATION_TOOL))
    expected = ToolResult(content="fixture snapshot")
    handler = AsyncMock(return_value=expected)

    if admitted:
        assert await admission.on_call_tool(context, handler) is expected
        handler.assert_awaited_once_with(context)
    else:
        with pytest.raises(ToolError, match="health_medication_peer_refused"):
            await admission.on_call_tool(context, handler)
        handler.assert_not_awaited()


def test_peer_transport_scopes_credential_before_opening_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEALTH_MCP_URL", f"{HEALTH_ORIGIN}/sse")
    monkeypatch.setenv("BUTLERS_HEALTH_CA_FILE", certifi.where())
    with pytest.raises(httpx.RequestError, match="Health TLS configuration"):
        health_peer.health_medication_transport(
            "https://butlers-up:41102/mcp",
            health_peer.MEDICATION_TOOL,
            "switchboard",
            token_dir=tmp_path,
        )
    with pytest.raises(httpx.RequestError, match="MCP endpoint"):
        health_peer.health_medication_transport(
            f"{HEALTH_ORIGIN}/health",
            health_peer.MEDICATION_TOOL,
            "switchboard",
            token_dir=tmp_path,
        )
    token_file = tmp_path / health_peer.HEALTH_PEER_AUDIENCE
    token_file.write_text("synthetic-projection", encoding="utf-8")
    transport = health_peer.health_medication_transport(
        f"{HEALTH_ORIGIN}/sse", health_peer.MEDICATION_TOOL, "switchboard", token_dir=tmp_path
    )
    assert transport is not None
    assert str(transport.url) == f"{HEALTH_ORIGIN}/mcp"
    assert transport.headers[TOKEN_HEADER] == "synthetic-projection"
    assert transport.headers[DAEMON_HEADER] == "switchboard"
    assert (
        health_peer.health_medication_transport(
            f"{HEALTH_ORIGIN}/mcp", "other_tool", "switchboard", token_dir=tmp_path
        )
        is None
    )
