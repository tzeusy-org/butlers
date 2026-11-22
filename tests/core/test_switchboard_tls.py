"""Switchboard trust preserves the configured receiver and protocol."""

import ssl
from pathlib import Path

import certifi
import httpx
import pytest
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

from butlers.core.service_plane.client import RelayUnavailable, call_relay
from butlers.core.switchboard_tls import switchboard_http_options, switchboard_mcp_transport

ORIGIN = "https://butlers-control:41100"


@pytest.fixture
def switchboard_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUTLERS_SWITCHBOARD_CA_FILE", certifi.where())
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", f"{ORIGIN}/sse")


def test_ca_is_scoped_to_the_configured_origin(switchboard_ca: None) -> None:
    options = switchboard_http_options(f"{ORIGIN}/mcp")
    context = options["verify"]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert not context.hostname_checks_common_name
    assert context.minimum_version == ssl.TLSVersion.TLSv1_3
    assert options["trust_env"] is False
    assert switchboard_http_options("http://butlers-up:41101/mcp") == {}
    assert list(switchboard_http_options()["mounts"]) == [ORIGIN]


@pytest.mark.parametrize(
    "url",
    [
        "http://butlers-control:41100/mcp",
        "https://butlers-control:41101/mcp",
        "https://other-service:41100/mcp",
    ],
)
async def test_relay_rejects_other_origins_before_loading_token(
    switchboard_ca: None, tmp_path: Path, url: str
) -> None:
    # The empty directory proves the origin check runs before the token read.
    with pytest.raises(RelayUnavailable, match="RequestError"):
        await call_relay(
            "medication.travel_snapshot",
            {},
            daemon="travel",
            switchboard_url=url,
            token_dir=tmp_path,
        )


def test_missing_ca_is_refused(
    switchboard_ca: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BUTLERS_SWITCHBOARD_CA_FILE", str(tmp_path / "missing-ca.crt"))
    with pytest.raises(httpx.RequestError, match="Switchboard TLS configuration"):
        switchboard_http_options(f"{ORIGIN}/health")


@pytest.mark.parametrize(
    ("path", "transport_type"), [("/sse", SSETransport), ("/mcp", StreamableHttpTransport)]
)
async def test_mcp_preserves_protocol_and_disables_redirects(
    switchboard_ca: None, path: str, transport_type: type
) -> None:
    transport = switchboard_mcp_transport(f"{ORIGIN}{path}")
    assert isinstance(transport, transport_type)
    async with transport.httpx_client_factory(follow_redirects=True) as client:
        assert not client.follow_redirects
        assert not client.trust_env


def test_unconfigured_clients_keep_their_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUTLERS_SWITCHBOARD_CA_FILE", raising=False)
    url = "http://butlers-up:41100/sse"
    assert switchboard_http_options(url, require_origin=True) == {}
    assert switchboard_mcp_transport(url) == url
