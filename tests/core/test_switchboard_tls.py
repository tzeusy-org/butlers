"""Switchboard trust preserves the configured receiver and protocol."""

import importlib
import ssl
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import certifi
import httpx
import pytest
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

from butlers.core import fact_authority
from butlers.core.control_plane_identity import ExpectedDaemon, ProbeOutcome
from butlers.core.service_plane.client import RelayUnavailable, call_relay
from butlers.core.switchboard_tls import switchboard_http_options, switchboard_mcp_transport
from butlers.tools.switchboard.registry.registry import ControlPlaneTargetDecision

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


async def test_fact_recovery_and_admission_use_switchboard_trust(
    switchboard_ca: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    client_type = httpx.AsyncClient

    def reply(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == fact_authority.RECOVERY_PATH:
            return httpx.Response(200, json={"capability": "synthetic"})
        return httpx.Response(200, json={"report": {"authority": "owner"}})

    def client(*, verify: ssl.SSLContext, trust_env: bool, timeout: int) -> httpx.AsyncClient:
        assert verify.verify_mode == ssl.CERT_REQUIRED
        assert trust_env is False
        return client_type(transport=httpx.MockTransport(reply), timeout=timeout)

    monkeypatch.setattr(fact_authority.httpx, "AsyncClient", client)
    registry = fact_authority.FactReceiverContextRegistry(None, "travel")
    context = await registry.recover_report(uuid.uuid4(), uuid.uuid4(), f"{ORIGIN}/sse")
    assert context.authority == "owner"
    assert seen == [
        f"{ORIGIN}{fact_authority.RECOVERY_PATH}",
        f"{ORIGIN}{fact_authority.VERIFIER_PATH}",
    ]


async def test_stale_switchboard_recheck_uses_its_ca(
    switchboard_ca: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = importlib.import_module("butlers.tools.switchboard.routing.route")
    expected = ExpectedDaemon("switchboard", 41100, "butlers-control", scheme="https")
    stale = ControlPlaneTargetDecision("stale", "stale_observation", expected)
    ready = ControlPlaneTargetDecision("ready", "ready", expected)
    resolver = AsyncMock(side_effect=[stale, ready])
    probe = AsyncMock(return_value=ProbeOutcome("healthy", True))
    client_type = httpx.AsyncClient

    def client(*, verify: ssl.SSLContext, trust_env: bool, timeout: float) -> httpx.AsyncClient:
        assert verify.verify_mode == ssl.CERT_REQUIRED
        assert trust_env is False
        return client_type(verify=verify, trust_env=trust_env, timeout=timeout)

    monkeypatch.setattr(route, "expected_route_target", lambda *args, **kwargs: expected)
    monkeypatch.setattr(route, "resolve_control_plane_target", resolver)
    monkeypatch.setattr(route, "probe_once", probe)
    monkeypatch.setattr(route.httpx, "AsyncClient", client)
    outcome = await route._resolve_receiver_route_target(
        object(), "switchboard", required_capability=None, route_contract_version=1
    )
    assert outcome == (ready, "ready", False)
    probe.assert_awaited_once()
    assert resolver.await_count == 2
