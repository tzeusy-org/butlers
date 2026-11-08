"""Shared transport selection and actual external Python caller integration."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from butlers.egress import http_transport
from butlers.egress.http_transport import GatewayConfig, GatewayTransport, external_async_client
from tests.config.test_egress_gateway import (  # noqa: F401
    HOST,
    Origin,
    running_gateway,
)
from tests.config.test_egress_gateway import (
    tls_contexts as tls_contexts,
)

pytestmark = pytest.mark.unit


class LoopbackHop(httpx.AsyncBaseTransport):
    """Only replace the owned hop dial address; use actual HTTPX framing and TCP."""

    def __init__(self, port):
        self.port = port
        self.inner = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request):
        loopback = httpx.Request(
            request.method,
            request.url.copy_with(host="127.0.0.1", port=self.port),
            headers=request.headers,
            stream=request.stream,
            extensions=request.extensions,
        )
        return await self.inner.handle_async_request(loopback)

    async def aclose(self):
        await self.inner.aclose()


@pytest.mark.parametrize(
    "values",
    [
        {"BUTLERS_EGRESS_MODE": "unknown"},
        {"BUTLERS_EGRESS_MODE": "request-v1"},
        {"BUTLERS_EGRESS_GATEWAY": "http://egress-gateway:3115"},
        {"BUTLERS_EGRESS_MODE": "request-v1", "BUTLERS_EGRESS_GATEWAY": "http://127.0.0.1:3115"},
        {
            "BUTLERS_EGRESS_MODE": "request-v1",
            "BUTLERS_EGRESS_GATEWAY": "http://egress-gateway:3115/path",
        },
        {
            "BUTLERS_EGRESS_MODE": "request-v1",
            "BUTLERS_EGRESS_GATEWAY": "https://egress-gateway:3115",
        },
    ],
)
def test_selected_configuration_never_falls_back(values):
    with pytest.raises(ValueError):
        GatewayConfig.from_environment(values)


def test_off_mode_keeps_constructor_kwargs_and_selected_owns_transport(monkeypatch):
    monkeypatch.delenv("BUTLERS_EGRESS_MODE", raising=False)
    monkeypatch.delenv("BUTLERS_EGRESS_GATEWAY", raising=False)
    seen = []

    def construct(**kwargs):
        seen.append(kwargs)
        return kwargs

    assert external_async_client(client_factory=construct, trust_env=True, timeout=7) == {
        "trust_env": True,
        "timeout": 7,
    }
    monkeypatch.setenv("BUTLERS_EGRESS_MODE", "request-v1")
    monkeypatch.setenv("BUTLERS_EGRESS_GATEWAY", "http://egress-gateway:3115")
    selected = external_async_client(client_factory=construct, timeout=7)
    assert selected["trust_env"] is False and isinstance(selected["transport"], GatewayTransport)
    with pytest.raises(ValueError):
        external_async_client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    assert (
        GatewayConfig.from_environment().base_url(HOST)
        == f"http://egress-gateway:3115/_butlers/egress/v1/https/{HOST}/443"
    )


async def test_actual_google_health_owned_client_streams_through_executor(
    tls_contexts, monkeypatch
):
    from butlers.connectors.google_health_client import GoogleHealthClient

    monkeypatch.setenv("BUTLERS_EGRESS_MODE", "request-v1")
    monkeypatch.setenv("BUTLERS_EGRESS_GATEWAY", "http://egress-gateway:3115")
    origin = Origin(chunks=(b'{"items":[1,2]}',))
    async with running_gateway(tls_contexts, origin) as (port, _):
        original = GatewayTransport
        monkeypatch.setattr(
            http_transport,
            "GatewayTransport",
            lambda config: original(config, hop_transport=LoopbackHop(port)),
        )

        async def synthetic_token():
            return "synthetic-python-caller-token"

        async with GoogleHealthClient(
            token_fetcher=synthetic_token, base_url=f"https://{HOST}"
        ) as caller:
            assert isinstance(caller._http._transport, original)
            result = await caller.get_json("/items", params={"page": "a/b"})
            assert result == {"items": [1, 2]}
    request, body = origin.requests[0]
    assert request.target == b"/items?page=a%2Fb" and body == b""
    assert dict(request.headers)[b"authorization"] == b"Bearer synthetic-python-caller-token"


async def test_transport_preserves_streams_and_refuses_foreign_host_without_dial(tls_contexts):
    origin = Origin(chunks=(b"data: one\n\n", b"data: two\n\n"), delay=0.03)
    async with running_gateway(tls_contexts, origin) as (port, _):
        async with httpx.AsyncClient(
            transport=GatewayTransport(GatewayConfig(3115), hop_transport=LoopbackHop(port))
        ) as caller:
            async with caller.stream(
                "POST", f"https://{HOST}/escaped%2Fpath?key=a%2Fb", content=b"stream-body"
            ) as response:
                assert response.status_code == 200
                assert [chunk async for chunk in response.aiter_bytes()] == [
                    b"data: one\n\n",
                    b"data: two\n\n",
                ]
                assert response.request.url.host == HOST
            for url, headers in [
                (f"https://{HOST}/", {"Host": "undeclared.example"}),
                ("http://127.0.0.1/", {}),
                (f"https://{HOST}/", [("Host", HOST), ("Host", HOST)]),
            ]:
                with pytest.raises((httpx.LocalProtocolError, httpx.UnsupportedProtocol)):
                    await caller.get(url, headers=headers)
    assert len(origin.dials) == 1
    assert origin.requests[0][0].target == b"/escaped%2Fpath?key=a%2Fb"
    assert origin.requests[0][1] == b"stream-body"


async def test_api_sdk_production_constructor_selects_gateway_and_keeps_injected_client(
    monkeypatch,
):
    from butlers.core.runtimes.api import ApiAdapter

    monkeypatch.setenv("BUTLERS_EGRESS_MODE", "request-v1")
    monkeypatch.setenv("BUTLERS_EGRESS_GATEWAY", "http://egress-gateway:3115")
    adapter = ApiAdapter()
    sdk = adapter._get_client("synthetic-api-key")
    assert isinstance(sdk._client._transport, GatewayTransport)
    await adapter.reset()
    supplied = object()
    adapter = ApiAdapter(client=supplied)
    assert adapter._get_client("synthetic-api-key") is supplied


async def test_real_httpx_receives_capacity_refusal_without_a_direct_fallback(tls_contexts):
    async with running_gateway(tls_contexts, global_slots=asyncio.Semaphore(0)) as (port, origin):
        async with httpx.AsyncClient(
            transport=GatewayTransport(GatewayConfig(3115), hop_transport=LoopbackHop(port))
        ) as caller:
            response = await caller.get(f"https://{HOST}/")
    assert response.status_code == 503 and response.headers["x-egress-refusal"] == "busy"
    assert not origin.dials and not origin.requests
