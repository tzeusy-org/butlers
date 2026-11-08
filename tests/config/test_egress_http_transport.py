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


@pytest.mark.parametrize("ready_fragment", [False, True])
async def test_default_selected_httpx_reads_early_refusal_before_upload_finishes(
    tls_contexts, monkeypatch, ready_fragment
):
    import socket

    from butlers.egress import gateway

    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(gateway, "_HEAD_TIMEOUT_S", 0.3)
    origin = Origin(
        302, chunks=(), early=True, headers=[(b"Location", b"https://undeclared.example/private")]
    )
    release, closed = asyncio.Event(), asyncio.Event()

    async def body():
        try:
            yield b"first"
            await origin.headers_seen.wait()
            if ready_fragment:
                yield b"racing-fragment"
            await release.wait()
            yield b"last"
        finally:
            closed.set()

    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        return original(
            "127.0.0.1" if host in {"egress-gateway", b"egress-gateway"} else host,
            port,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    async with running_gateway(tls_contexts, origin, bound_port=True) as (port, _):
        monkeypatch.setenv("BUTLERS_EGRESS_MODE", "request-v1")
        monkeypatch.setenv("BUTLERS_EGRESS_GATEWAY", f"http://egress-gateway:{port}")
        async with external_async_client(timeout=1) as caller:
            request = asyncio.create_task(caller.post(f"https://{HOST}/", content=body()))
            try:
                await asyncio.wait_for(origin.headers_seen.wait(), 1)
                await asyncio.wait({request}, timeout=0.2)
                assert request.done(), (
                    "default production HTTPX transport still waits for upload after origin final response"
                )
                response = request.result()
                assert (
                    response.status_code == 403
                    and response.headers["x-egress-refusal"] == "redirect_refused"
                ), response.headers.get("x-egress-refusal", "missing")
                assert closed.is_set() and not release.is_set()
                assert len(origin.dials) == 1
            finally:
                release.set()
                await asyncio.gather(request, return_exceptions=True)


def map_owned_hop(monkeypatch):
    import socket

    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        return original(
            "127.0.0.1" if host in {"egress-gateway", b"egress-gateway"} else host,
            port,
            *args,
            **kwargs,
        )

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


async def test_default_hop_progressing_upload_stream_and_aclose(tls_contexts, monkeypatch):
    from butlers.egress import gateway

    map_owned_hop(monkeypatch)
    monkeypatch.setattr(gateway, "_HEAD_TIMEOUT_S", 0.04)
    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.2)
    origin = Origin(chunks=(b"data: one\n\n", b"data: two\n\n"), delay=0.02)

    async def body():
        for _ in range(5):
            await asyncio.sleep(0.03)
            yield b"part"

    async with running_gateway(tls_contexts, origin, bound_port=True) as (port, _):
        transport = GatewayTransport(GatewayConfig(port))
        async with httpx.AsyncClient(
            transport=transport, timeout=httpx.Timeout(connect=0.2, write=0.2, read=0.04, pool=0.2)
        ) as caller:
            async with caller.stream("POST", f"https://{HOST}/", content=body()) as response:
                assert response.status_code == 200
                assert [chunk async for chunk in response.aiter_bytes()] == [
                    b"data: one\n\n",
                    b"data: two\n\n",
                ]
            assert not transport._hop._streams
        assert transport._hop._closed
    assert origin.requests[0][1] == b"part" * 5


@pytest.mark.parametrize(
    ("case", "category"),
    [
        ("connect-timeout", httpx.ConnectTimeout),
        ("connect-error", httpx.ConnectError),
        ("write-timeout", httpx.WriteTimeout),
        ("read-timeout", httpx.ReadTimeout),
        ("malformed-response", httpx.RemoteProtocolError),
    ],
)
async def test_default_hop_keeps_timeouts_and_httpx_errors(monkeypatch, case, category):
    owned = set()
    gate = asyncio.Event()

    async def peer(reader, writer):
        task = asyncio.current_task()
        owned.add(task)
        try:
            await reader.readuntil(b"\r\n\r\n")
            if case == "malformed-response":
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: bad\r\n\r\n")
                await writer.drain()
            else:
                await gate.wait()
        finally:
            writer.close()
            await writer.wait_closed()
            owned.discard(task)

    class ParkedDrain:
        def __init__(self, writer):
            self.writer = writer

        def __getattr__(self, name):
            return getattr(self.writer, name)

        async def drain(self):
            await gate.wait()

    async with await asyncio.start_server(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def open_hop(config):
            assert config.port == 3115
            if case == "connect-timeout":
                await gate.wait()
            if case == "connect-error":
                raise OSError("synthetic-nonpublic-error-text")
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            return reader, ParkedDrain(writer) if case == "write-timeout" else writer

        monkeypatch.setattr(http_transport, "_open_hop", open_hop)
        transport = GatewayTransport(GatewayConfig(3115))
        try:
            async with httpx.AsyncClient(transport=transport, timeout=0.03) as caller:
                with pytest.raises(category) as failure:
                    await caller.get(f"https://{HOST}/private-path?token=synthetic")
                assert isinstance(failure.value, httpx.RequestError)
                assert failure.value.request.url.host == HOST
                assert "synthetic-nonpublic-error-text" not in str(failure.value)
                assert not transport._hop._streams
        finally:
            gate.set()
            if owned:
                await asyncio.gather(*list(owned), return_exceptions=True)
            assert not owned


async def test_default_hop_caller_cancellation_closes_its_stream(tls_contexts, monkeypatch):
    map_owned_hop(monkeypatch)
    origin = Origin(chunks=(b"first", b"later"), delay=0.05)
    async with running_gateway(tls_contexts, origin, bound_port=True) as (port, _):
        transport = GatewayTransport(GatewayConfig(port))
        async with httpx.AsyncClient(transport=transport) as caller:
            async with caller.stream("GET", f"https://{HOST}/") as response:
                iterator = response.aiter_bytes()
                assert await anext(iterator) == b"first"
                assert transport._hop._streams
                await response.aclose()
                await iterator.aclose()
                assert not transport._hop._streams
            origin.headers_seen.clear()
            task = asyncio.create_task(caller.get(f"https://{HOST}/"))
            await asyncio.wait_for(origin.headers_seen.wait(), 1)
            assert transport._hop._streams
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert not transport._hop._streams


async def test_default_hop_rejects_sync_only_stream_before_any_dial(monkeypatch):
    dials = []

    class SyncOnly(httpx.SyncByteStream):
        def __iter__(self):
            yield b"synthetic"

    async def forbidden_open(config):
        dials.append(config.port)
        raise OSError("owned dial sentinel")

    monkeypatch.setattr(http_transport, "_open_hop", forbidden_open)
    transport = GatewayTransport(GatewayConfig(3115))
    request = httpx.Request("POST", f"https://{HOST}/", headers={"Host": HOST}, stream=SyncOnly())
    try:
        with pytest.raises(httpx.LocalProtocolError) as failure:
            await transport.handle_async_request(request)
        assert failure.value.request is request
        assert "asynchronous request stream" in str(failure.value)
        assert not dials and not transport._hop._streams
    finally:
        await transport.aclose()
