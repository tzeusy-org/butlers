"""Production request executor controls over genuine owned loopback TLS."""

from __future__ import annotations

import asyncio
import datetime
import json
import socket
import ssl
from contextlib import asynccontextmanager

import h11
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from butlers.egress import gateway
from butlers.egress.gateway import (
    AUDIT_LOGGER,
    HEALTH_PORT,
    Route,
    admissible_address,
    routes_from_rendered,
    system_resolver,
)
from butlers.egress.profiles import RENDERED_PATH, load_profiles

pytestmark = pytest.mark.unit
HOST = "gmail.googleapis.com"
ROUTE = Route("connector-gmail", 3115, frozenset({(HOST, 443)}))
PUBLIC = "93.184.216.34"
_PUBLIC = PUBLIC


def test_routes_come_from_the_rendered_profiles_and_skip_held_or_internal_only_workloads():
    routes = routes_from_rendered(json.loads(RENDERED_PATH.read_text(encoding="utf-8")))
    profiles = load_profiles().workloads
    expected = {p.gateway_port: name for name, p in profiles.items() if p.gateway_port}
    assert {port: r.workload for port, r in routes.items()} == expected
    assert HEALTH_PORT not in routes
    for route in routes.values():
        declared = {(e.host, e.port) for e in profiles[route.workload].external}
        assert route.destinations == declared


@pytest.mark.parametrize(
    ("address", "admitted"),
    [
        ("10.0.0.1", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("100.64.0.1", False),  # CGNAT, which the tailnet uses
        ("169.254.169.254", False),  # cloud metadata
        ("127.0.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("192.0.2.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:10.0.0.1", False),
        ("64:ff9b::a00:1", False),  # NAT64 of 10.0.0.1
        ("64:ff9b::a9fe:a9fe", False),  # NAT64 of the metadata address
        ("::a00:1", False),  # IPv4-compatible 10.0.0.1
        ("fec0::1", False),  # deprecated site-local
        ("2606:4700:4700::1111", False),  # IPv6 at all: the gateway policy is IPv4-only
        (_PUBLIC, True),
        (f"::ffff:{_PUBLIC}", True),
    ],
)
def test_only_globally_routable_unicast_addresses_are_admissible(address, admitted):
    assert admissible_address(address) is admitted


async def test_the_system_resolver_asks_for_the_absolute_name_over_ipv4(monkeypatch):
    calls = []

    async def fake_getaddrinfo(host, port, **kwargs):
        calls.append((host, port, kwargs.get("family")))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    assert await system_resolver("gmail.googleapis.com", 443) == [_PUBLIC]
    assert calls == [("gmail.googleapis.com.", 443, socket.AF_INET)]


@pytest.fixture(scope="module")
def tls_contexts(tmp_path_factory):
    directory = tmp_path_factory.mktemp("egress-synthetic-tls")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), False)
        .sign(key, hashes.SHA256())
    )
    cert, private = directory / "certificate.pem", directory / "private.pem"
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert, private)
    return server, ssl.create_default_context(cafile=str(cert))


class Origin:
    def __init__(
        self, status=200, chunks=(b"healthy",), delay=0, headers=(), raw=None, early=False
    ):
        self.status, self.chunks, self.delay, self.headers = status, chunks, delay, headers
        self.raw = raw
        self.early = early
        self.headers_seen = asyncio.Event()
        self.requests = []
        self.dials = []
        self.tasks = set()
        self.sni = []

    async def respond(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            connection = h11.Connection(h11.SERVER)
            request = body = None
            content = bytearray()
            while True:
                event = connection.next_event()
                if event is h11.NEED_DATA:
                    data = await asyncio.wait_for(reader.read(16384), 3)
                    connection.receive_data(data)
                elif isinstance(event, h11.Request):
                    request = event
                    self.headers_seen.set()
                    if self.early:
                        body = b""
                        break
                elif isinstance(event, h11.Data):
                    content.extend(event.data)
                elif isinstance(event, h11.EndOfMessage):
                    body = bytes(content)
                    break
                else:
                    return
            self.requests.append((request, body))
            if self.raw is not None:
                writer.write(self.raw)
                await writer.drain()
                return
            headers = [(b"content-type", b"text/event-stream"), *self.headers]
            writer.write(connection.send(h11.Response(status_code=self.status, headers=headers)))
            await writer.drain()
            for chunk in self.chunks:
                if self.delay:
                    await asyncio.sleep(self.delay)
                writer.write(connection.send(h11.Data(data=chunk)))
                await writer.drain()
            writer.write(connection.send(h11.EndOfMessage()))
            await writer.drain()
        except (ConnectionError, h11.ProtocolError, TimeoutError):
            pass  # Expected when a refusal/cancellation closes the owned connection.
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.tasks.discard(task)


@asynccontextmanager
async def running_gateway(
    tls_contexts,
    origin=None,
    *,
    answers=None,
    slots=None,
    context=None,
    global_slots=None,
    bound_port=False,
):
    origin = Origin() if origin is None else origin
    server_context, client_context = tls_contexts
    server_context.set_servername_callback(lambda _ssl, name, _ctx: origin.sni.append(name))
    owned = set()
    origin.gateway_tasks = owned
    async with await asyncio.start_server(
        origin.respond, "127.0.0.1", 0, ssl=server_context
    ) as server:
        port = server.sockets[0].getsockname()[1]

        async def resolve(host, target_port):
            assert (host, target_port) == (HOST, 443)
            if isinstance(answers, Exception):
                raise answers
            return [PUBLIC] if answers is None else answers

        async def dial(address, target_port, host, tls):
            origin.dials.append((address, target_port, host))
            return await asyncio.open_connection(
                "127.0.0.1", port, ssl=tls, server_hostname=host, limit=16384
            )

        async def execute(reader, writer):
            task = asyncio.current_task()
            owned.add(task)
            try:
                await gateway.handle_request(
                    reader,
                    writer,
                    Route(ROUTE.workload, proxy.sockets[0].getsockname()[1], ROUTE.destinations)
                    if bound_port
                    else ROUTE,
                    resolve=resolve,
                    open_upstream=dial,
                    tls_context=context or client_context,
                    slots=slots,
                    global_slots=global_slots,
                )
            finally:
                owned.discard(task)

        async with await asyncio.start_server(execute, "127.0.0.1", 0, limit=8192) as proxy:
            try:
                yield proxy.sockets[0].getsockname()[1], origin
            finally:
                tasks = list(owned | origin.tasks)
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                assert not owned and not origin.tasks


def wire(*, host=HOST, target=b"/test", headers=(), body=b"", method=b"POST"):
    return (
        method
        + b" /_butlers/egress/v1/https/"
        + host.encode()
        + b"/443"
        + target
        + b" HTTP/1.1\r\nHost: egress-gateway:3115\r\nConnection: close\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n"
        + b"".join(k + b": " + v + b"\r\n" for k, v in headers)
        + b"\r\n"
        + body
    )


async def exchange(port, payload):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(payload)
        await writer.drain()
        response = bytearray()
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(16384), 5)
            except ConnectionResetError:
                break  # Preserve actual received refusal bytes before an unread-input reset.
            if not chunk:
                break
            response.extend(chunk)
        return bytes(response)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def test_request_execution_binds_verified_tls_host_and_preserves_content(
    tls_contexts, caplog
):
    caplog.set_level("INFO", logger=AUDIT_LOGGER)
    tokens = (b"path-secret", b"query-secret", b"header-secret", b"body-secret")
    origin = Origin(headers=[(b"X-Egress-Refusal", b"forged-origin-receipt")])
    async with running_gateway(tls_contexts, origin) as (port, _):
        response = await exchange(
            port,
            wire(
                target=b"/path-secret?key=query-secret",
                headers=[(b"authorization", b"header-secret")],
                body=b"body-secret",
            ),
        )
    assert response.startswith(b"HTTP/1.1 200") and b"healthy" in response
    assert b"forged-origin-receipt" not in response
    request, body = origin.requests[0]
    assert origin.sni == [HOST] and origin.dials == [(PUBLIC, 443, HOST)]
    assert request.target == b"/path-secret?key=query-secret" and body == tokens[3]
    assert dict(request.headers)[b"host"] == HOST.encode()
    assert dict(request.headers)[b"authorization"] == tokens[2]
    assert not any(token.decode() in caplog.text for token in tokens)
    audit = json.loads(next(r.message for r in caplog.records if r.name == AUDIT_LOGGER))
    assert audit["verdict"] == "allowed" and audit["bytes_up"] == len(body)
    assert audit["host"] == HOST and audit["bytes_down"] == len(b"healthy")


@pytest.mark.parametrize("status", [300, 301, 302, 303, 304, 305, 307, 308, 399])
async def test_every_redirect_refuses_before_headers_body_or_next_dial(tls_contexts, status):
    origin = Origin(
        status,
        chunks=() if status == 304 else (b"redirect-secret",),
        headers=[(b"location", b"https://undeclared.example/secret")],
    )
    async with running_gateway(tls_contexts, origin) as (port, _):
        response = await exchange(port, wire(headers=[(b"authorization", b"synthetic-token")]))
    assert response.startswith(b"HTTP/1.1 403") and b"redirect_refused" in response
    assert b"Location:" not in response and b"redirect-secret" not in response
    assert b"undeclared.example" not in response and len(origin.dials) == 1
    assert dict(origin.requests[0][0].headers)[b"authorization"] == b"synthetic-token"


@pytest.mark.parametrize(
    "mutation",
    [
        "connect",
        "absolute",
        "foreign",
        "userinfo",
        "ip",
        "zeroport",
        "wrongport",
        "uppercase",
        "trailing-dot",
        "old-version",
        "duplicate-host",
        "host-mismatch",
        "duplicate-length",
        "length-transfer",
        "forwarded",
        "identity",
        "upgrade",
    ],
)
async def test_untrusted_identity_and_framing_never_dial(tls_contexts, mutation):
    request = wire()
    changes = {
        "connect": b"CONNECT gmail.googleapis.com:443 HTTP/1.1\r\nHost: gmail.googleapis.com\r\n\r\n",
        "absolute": request.replace(b"/_butlers/", b"https://gmail.googleapis.com/_butlers/", 1),
        "foreign": wire(host="oauth2.googleapis.com"),
        "userinfo": wire(host="user@gmail.googleapis.com"),
        "ip": wire(host="127.0.0.1"),
        "zeroport": request.replace(b"/443/", b"/0443/"),
        "wrongport": request.replace(b"/443/", b"/444/"),
        "uppercase": wire(host=HOST.upper()),
        "trailing-dot": wire(host=HOST + "."),
        "old-version": request.replace(b"HTTP/1.1", b"HTTP/1.0"),
        "duplicate-host": wire(headers=[(b"Host", b"egress-gateway:3115")]),
        "host-mismatch": request.replace(b"egress-gateway:3115", b"foreign.example"),
        "duplicate-length": wire(headers=[(b"Content-Length", b"0")]),
        "length-transfer": wire(headers=[(b"Transfer-Encoding", b"chunked")]),
        "forwarded": wire(headers=[(b"X-Forwarded-Host", b"foreign.example")]),
        "identity": wire(headers=[(b"X-Butlers-Workload", b"another-workload")]),
        "upgrade": wire(headers=[(b"Upgrade", b"websocket")]),
    }
    async with running_gateway(tls_contexts) as (port, origin):
        response = await exchange(port, changes[mutation])
    assert response.startswith((b"HTTP/1.1 400", b"HTTP/1.1 403", b"HTTP/1.1 405"))
    assert not origin.dials and not origin.requests


@pytest.mark.parametrize(
    "answers", [[], ["127.0.0.1"], ["169.254.169.254"], [PUBLIC, "10.0.0.1"], ["100.64.0.1"]]
)
async def test_nonpublic_or_mixed_resolution_never_dials(tls_contexts, answers):
    async with running_gateway(tls_contexts, answers=answers) as (port, origin):
        response = await exchange(port, wire())
    assert b"non_public_address" in response and not origin.dials


async def test_outage_foreign_certificate_and_busy_are_closed(tls_contexts, caplog):
    async with running_gateway(tls_contexts, answers=OSError("header-secret")) as (port, origin):
        response = await exchange(port, wire())
    assert b"dns_failed" in response and b"header-secret" not in response and not origin.dials
    async with running_gateway(tls_contexts, context=ssl.create_default_context()) as (
        port,
        origin,
    ):
        response = await exchange(port, wire())
    assert b"upstream_unreachable" in response and not origin.requests
    async with running_gateway(tls_contexts, answers=RuntimeError("exception-secret")) as (
        port,
        origin,
    ):
        response = await exchange(port, wire())
    assert b"gateway_error" in response and not origin.dials
    assert "exception-secret" not in caplog.text and "header-secret" not in caplog.text
    async with running_gateway(tls_contexts, slots=asyncio.Semaphore(0)) as (port, origin):
        response = await exchange(port, wire())
    assert b"503" in response and b"busy" in response and not origin.dials


async def test_live_sse_survives_while_idle_and_cancelled_exchanges_close(
    tls_contexts, monkeypatch
):
    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.15)
    origin = Origin(chunks=(b"data: one\n\n",) * 4, delay=0.07)
    async with running_gateway(tls_contexts, origin) as (port, _):
        response = await exchange(port, wire())
    assert response.count(b"data: one") == 4
    origin = Origin(chunks=(b"late",), delay=1)
    async with running_gateway(tls_contexts, origin) as (port, _):
        response = await exchange(port, wire())
    assert b"late" not in response
    # A client cancellation releases the slot; the context joins the owned tasks.
    slots = asyncio.Semaphore(1)
    async with running_gateway(tls_contexts, Origin(delay=1), slots=slots) as (port, origin):
        task = asyncio.create_task(exchange(port, wire()))
        await asyncio.sleep(0.03)
        handlers = list(origin.gateway_tasks)
        assert len(handlers) == 1 and slots.locked()
        for handler in handlers:
            handler.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)
        await asyncio.gather(task, return_exceptions=True)
        assert slots._value == 1 and not origin.gateway_tasks
    assert slots._value == 1


async def test_stream_byte_limits_and_nonredirect_errors(tls_contexts, monkeypatch):
    monkeypatch.setattr(gateway, "_UPLOAD_LIMIT", 3)
    async with running_gateway(tls_contexts) as (port, origin):
        response = await exchange(port, wire(body=b"four"))
    assert b"upload_limit" in response and not origin.requests
    monkeypatch.setattr(gateway, "_DOWNLOAD_LIMIT", 3)
    async with running_gateway(tls_contexts, Origin(429, chunks=(b"123",))) as (port, _):
        response = await exchange(port, wire())
    assert response.startswith(b"HTTP/1.1 429") and b"123" in response
    async with running_gateway(tls_contexts, Origin(chunks=(b"too long",))) as (port, _):
        response = await exchange(port, wire())
    assert b"too long" not in response


@pytest.mark.parametrize(
    "raw",
    [
        b"HTTP/1.1 101 Switching Protocols\r\nConnection: upgrade\r\nUpgrade: websocket\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * 8192 + b"\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: bad\r\n\r\nsecret-body",
    ],
)
async def test_malformed_or_upgrade_origin_headers_are_not_exposed(tls_contexts, raw):
    async with running_gateway(tls_contexts, Origin(raw=raw)) as (port, _):
        response = await exchange(port, wire())
    assert response.startswith((b"HTTP/1.1 400", b"HTTP/1.1 502"))
    assert b"secret-body" not in response and b"X-Large" not in response


async def test_global_budget_and_head_response_are_real_boundaries(tls_contexts):
    async with running_gateway(tls_contexts, global_slots=asyncio.Semaphore(0)) as (port, origin):
        response = await exchange(port, wire())
    assert b"503" in response and not origin.dials
    async with running_gateway(tls_contexts, Origin(chunks=())) as (port, origin):
        response = await exchange(port, wire(method=b"HEAD"))
    assert response.startswith(b"HTTP/1.1 200") and response.endswith(b"\r\n\r\n")
    assert origin.requests[0][0].method == b"HEAD"


@pytest.mark.parametrize(
    ("early", "status"), [(False, 302), (True, 302), (False, 413), (True, 413)]
)
async def test_final_origin_response_does_not_wait_for_outstanding_upload(
    tls_contexts, monkeypatch, early, status
):
    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.25)
    monkeypatch.setattr(gateway, "_HEAD_TIMEOUT_S", 0.25)
    origin = Origin(
        status,
        chunks=(b"provider-error",),
        early=early,
        headers=[(b"Location", b"https://undeclared.example/private")] if status == 302 else (),
    )
    payload = wire(body=b"x" * 100)
    if early:
        payload = payload[:-100]
    async with running_gateway(tls_contexts, origin) as (port, _):
        response = await exchange(port, payload)
    assert origin.headers_seen.is_set() and len(origin.requests) == 1
    if status == 302:
        assert response.startswith(b"HTTP/1.1 403") and b"redirect_refused" in response
        assert b"Location" not in response and b"provider-error" not in response
    else:
        assert response.startswith(b"HTTP/1.1 413") and b"provider-error" in response
    assert origin.requests[0][1] == (b"" if early else b"x" * 100)


async def test_progressing_upload_outlasts_header_budget_without_premature_timeout(
    tls_contexts, monkeypatch
):
    monkeypatch.setattr(gateway, "_HEAD_TIMEOUT_S", 0.04)
    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.2)
    async with running_gateway(tls_contexts) as (port, origin):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            payload = wire(body=b"x" * 20)
            writer.write(payload[:-20])
            await writer.drain()
            await asyncio.wait_for(origin.headers_seen.wait(), 1)
            for _ in range(5):
                await asyncio.sleep(0.03)
                writer.write(b"x" * 4)
                await writer.drain()
            response = await asyncio.wait_for(reader.read(), 1)
            assert response.startswith(b"HTTP/1.1 200") and b"healthy" in response
            assert origin.requests[0][1] == b"x" * 20
        finally:
            writer.close()
            await writer.wait_closed()


@pytest.mark.parametrize(
    "case",
    [
        "fixed",
        "chunked",
        "repeated-end-to-end",
        "informational",
        "too-many-informational",
        "length-transfer",
        "duplicate-length",
    ],
)
async def test_raw_origin_framing_is_checked_before_success_exposure(tls_contexts, case):
    messages = {
        "fixed": b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood",
        "chunked": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\ngood\r\n0\r\n\r\n",
        "repeated-end-to-end": b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nSet-Cookie: first\r\nSet-Cookie: second\r\n\r\ngood",
        "informational": b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 103 Early Hints\r\nLink: ignored\r\n\r\nHTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood",
        "too-many-informational": b"HTTP/1.1 100 Continue\r\n\r\n" * 6
        + b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ngood",
        "length-transfer": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 99\r\n\r\n4\r\ngood\r\n0\r\n\r\n",
        "duplicate-length": b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nContent-Length: 4\r\n\r\ngood",
    }
    async with running_gateway(tls_contexts, Origin(raw=messages[case])) as (port, _):
        response = await exchange(port, wire())
    if case == "too-many-informational":
        assert response.startswith(b"HTTP/1.1 502") and b"unsupported_response" in response
        assert b"good" not in response
    elif case in {"length-transfer", "duplicate-length"}:
        assert response.startswith(b"HTTP/1.1 502") and b"ambiguous_response_framing" in response
        assert b"good" not in response
    else:
        assert response.startswith(b"HTTP/1.1 200") and b"good" in response
        if case == "repeated-end-to-end":
            assert response.lower().count(b"set-cookie:") == 2


class CloseBarrierWriter:
    """Instrument only close completion/failure; not native TLS timing evidence."""

    def __init__(self, release, *, failed=False):
        self.release, self.failed = release, failed
        self.started = asyncio.Event()
        self.data = bytearray()
        self.close_calls = 0
        self.aborted = False
        self.transport = self

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        self.close_calls += 1
        self.started.set()

    def abort(self):
        self.aborted = True
        self.release.set()

    async def wait_closed(self):
        await self.release.wait()
        if self.failed:
            raise RuntimeError("synthetic-close-failure")


@pytest.mark.parametrize(
    "ending",
    [
        "complete",
        "failed-close",
        "cancelled",
        "timeout",
        "wss-complete",
        "wss-failed-close",
        "wss-cancelled",
        "wss-timeout",
    ],
)
async def test_capacity_owns_teardown_until_both_writers_settle(monkeypatch, ending):
    is_websocket = ending.startswith("wss-")
    ending = ending.removeprefix("wss-")
    if ending == "timeout":
        monkeypatch.setattr(gateway, "_CONNECT_TIMEOUT_S", 0.05)
    slots, global_slots = asyncio.Semaphore(1), asyncio.Semaphore(1)
    release, ready = asyncio.Event(), asyncio.Event()
    ready.set()
    upstreams, clients = [], []
    dial_started = asyncio.Event()

    async def resolve(_host, _port):
        return [PUBLIC]

    async def dial(_address, _port, _host, _context):
        reader = asyncio.StreamReader()
        reader.feed_data(
            b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n\x88\x02\x03\xe8"
            if is_websocket
            else b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
        )
        reader.feed_eof()
        writer = CloseBarrierWriter(
            release if not upstreams else ready, failed=not upstreams and ending == "failed-close"
        )
        upstreams.append(writer)
        dial_started.set()
        return reader, writer

    async def run():
        reader = asyncio.StreamReader()
        reader.feed_data(
            ws_wire() + b"\x88\x82\x00\x00\x00\x00\x03\xe8" if is_websocket else wire()
        )
        reader.feed_eof()
        writer = CloseBarrierWriter(ready)
        clients.append(writer)
        await gateway.handle_request(
            reader,
            writer,
            ROUTE,
            resolve=resolve,
            open_upstream=dial,
            slots=slots,
            global_slots=global_slots,
        )
        return writer

    first = asyncio.create_task(run())
    try:
        await asyncio.wait_for(dial_started.wait(), 1)
        await asyncio.wait_for(upstreams[0].started.wait(), 1)
        assert not first.done() and slots.locked() and global_slots.locked()
        second = await asyncio.wait_for(run(), 1)
        assert b"503" in second.data and len(upstreams) == 1
        if ending == "cancelled":
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done() and slots.locked() and global_slots.locked()
            first.cancel()
            await asyncio.sleep(0)
            assert not first.done() and slots.locked() and global_slots.locked()
        if ending != "timeout":
            release.set()
        outcome = await asyncio.gather(first, return_exceptions=True)
        if ending == "cancelled":
            assert isinstance(outcome[0], asyncio.CancelledError)
        else:
            assert outcome == [clients[0]]
        assert upstreams[0].close_calls == 1 and clients[0].close_calls == 1
        if ending in {"failed-close", "timeout"}:
            assert upstreams[0].aborted
        assert slots._value == global_slots._value == 1
        third = await asyncio.wait_for(run(), 1)
        assert (b"101" if is_websocket else b"200") in third.data and len(upstreams) == 2
        assert slots._value == global_slots._value == 1
    finally:
        release.set()
        await asyncio.gather(first, return_exceptions=True)


async def test_websocket_handshake_keeps_origin_identity_and_streams_frames(tls_contexts):
    import base64
    import hashlib

    from websockets.frames import Frame, Opcode

    class WebSocketOrigin(Origin):
        async def respond(self, reader, writer):
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                self.requests.append(head)
                headers = dict(line.split(b": ", 1) for line in head.split(b"\r\n")[1:] if line)
                key = headers[b"sec-websocket-key"]
                accept = base64.b64encode(
                    hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest()
                )
                writer.write(
                    b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Accept: "
                    + accept
                    + b"\r\nSec-WebSocket-Protocol: responses\r\n\r\n"
                )
                await writer.drain()
                # Masked client frame is preserved, including its arbitrary mask.
                expected = Frame(Opcode.TEXT, b"request").serialize(mask=True)
                size = 2 + 4 + len(b"request")
                data = await reader.readexactly(size)
                assert data[:2] == expected[:2]
                mask, body = data[2:6], data[6:]
                assert bytes(value ^ mask[i % 4] for i, value in enumerate(body)) == b"request"
                writer.write(Frame(Opcode.TEXT, b"response").serialize(mask=False))
                writer.write(Frame(Opcode.CLOSE, b"\x03\xe8").serialize(mask=False))
                await writer.drain()
                await reader.readexactly(8)
            finally:
                writer.close()
                await writer.wait_closed()
                self.tasks.discard(task)

    origin = WebSocketOrigin()
    async with running_gateway(tls_contexts, origin) as (port, observed):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(ws_wire())
            await writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            assert head.startswith(b"HTTP/1.1 101")
            assert b"sec-websocket-protocol: responses" in head.lower()
            writer.write(Frame(Opcode.TEXT, b"request").serialize(mask=True))
            await writer.drain()
            assert await reader.readexactly(10) == Frame(Opcode.TEXT, b"response").serialize(
                mask=False
            )
            assert await reader.readexactly(4) == Frame(Opcode.CLOSE, b"\x03\xe8").serialize(
                mask=False
            )
            writer.write(Frame(Opcode.CLOSE, b"\x03\xe8").serialize(mask=True))
            await writer.drain()
            assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()
        assert observed.sni == [HOST]
        assert observed.dials == [(PUBLIC, 443, HOST)]
        request = observed.requests[0].lower()
        assert request.startswith(b"get /responses?logical=1 http/1.1")
        assert b"host: " + HOST.encode() in request
        assert b"egress-gateway" not in request
        assert b"sec-websocket-extensions" not in request


def ws_wire(*, headers=(), target=b"/responses?logical=1"):
    return (
        b"GET /_butlers/egress/v1/wss/"
        + HOST.encode()
        + b"/443"
        + target
        + b" HTTP/1.1\r\nHost: egress-gateway:3115\r\nConnection: Upgrade\r\n"
        b"Upgrade: websocket\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        b"Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: responses\r\n"
        b"Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits\r\n"
        + b"".join(k + b": " + v + b"\r\n" for k, v in headers)
        + b"\r\n"
    )


@pytest.mark.parametrize("status", [300, 302, 304, 307, 399])
async def test_websocket_redirect_refuses_before_upgrade_without_sensitive_response(
    tls_contexts, status, caplog
):
    caplog.set_level("INFO", logger=AUDIT_LOGGER)
    origin = Origin(
        raw=(
            f"HTTP/1.1 {status} Redirect\r\nLocation: https://private.invalid/SECRET\r\nContent-Length: 6\r\n\r\nSECRET"
        ).encode(),
        early=True,
    )
    async with running_gateway(tls_contexts, origin) as (port, observed):
        response = await exchange(port, ws_wire())
        assert b"403" in response.split(b"\r\n")[0]
        assert b"X-Egress-Refusal: redirect_refused" in response
        assert b"SECRET" not in response + caplog.text.encode()
        assert len(observed.dials) == 1


@pytest.mark.parametrize(
    "replacement",
    [
        (b"Upgrade: websocket", b"Upgrade: h2c"),
        (b"Connection: Upgrade", b"Connection: Upgrade, x-secret"),
        (b"Sec-WebSocket-Version: 13", b"Sec-WebSocket-Version: 12"),
        (b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", b"Sec-WebSocket-Key: invalid"),
        (b"Sec-WebSocket-Protocol: responses", b"Sec-WebSocket-Protocol: responses, responses"),
        (b"GET /", b"POST /"),
        (b"\r\n\r\n", b"\r\nContent-Length: 1\r\n\r\nx"),
    ],
)
async def test_websocket_invalid_client_handshake_refuses_before_origin_dial(
    tls_contexts, replacement
):
    async with running_gateway(tls_contexts) as (port, origin):
        response = await exchange(port, ws_wire().replace(*replacement))
        expected = b"403" if replacement[0] == b"Connection: Upgrade" else b"400"
        assert expected in response.split(b"\r\n")[0]
        assert not origin.dials


@pytest.mark.parametrize(
    "headers",
    [
        b"Sec-WebSocket-Accept: wrong\r\n",
        b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\nSec-WebSocket-Protocol: undeclared\r\n",
        b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\nSec-WebSocket-Extensions: permessage-deflate\r\n",
        b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\nUpgrade: websocket\r\n",
    ],
)
async def test_websocket_invalid_origin_handshake_refuses_before_101_exposure(
    tls_contexts, headers
):
    origin = Origin(
        raw=b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
        + headers
        + b"\r\nSECRET",
        early=True,
    )
    async with running_gateway(tls_contexts, origin) as (port, observed):
        response = await exchange(port, ws_wire())
        assert b"502" in response.split(b"\r\n")[0]
        assert b"101" not in response
        assert b"SECRET" not in response
        assert len(observed.dials) == 1


class FrameOrigin(Origin):
    def __init__(self, frames=b""):
        super().__init__()
        self.frames = frames
        self.received = bytearray()

    async def respond(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            self.requests.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n"
                b"Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n\r\n" + self.frames
            )
            await writer.drain()
            while chunk := await reader.read(16384):
                self.received.extend(chunk)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self.tasks.discard(task)


@pytest.mark.parametrize(
    ("side", "frame", "reason"),
    [
        ("client", b"\x81\x01a", "malformed_websocket_frame"),  # missing mask
        ("client", b"\xc1\x80abcd", "malformed_websocket_frame"),  # RSV
        ("client", b"\x83\x80abcd", "malformed_websocket_frame"),  # opcode
        ("client", b"\x09\x80abcd", "malformed_websocket_frame"),  # fragmented ping
        ("client", b"\x80\x80abcd", "malformed_websocket_frame"),  # stray continuation
        ("client", b"\x82\xfe\x00\x01abcdx", "malformed_websocket_frame"),  # overlong length
        ("client", b"\x82\xff" + (64 * 1024 * 1024 + 1).to_bytes(8), "websocket_message_limit"),
        ("origin", b"\x81\x80abcd", "malformed_websocket_frame"),  # origin mask
        ("origin", b"\x81\x01\xff", "malformed_websocket_frame"),  # UTF8
        ("origin", b"\x88\x01x", "malformed_websocket_frame"),  # malformed close
        ("origin", b"\x88\x02\x03\xee", "malformed_websocket_frame"),  # reserved close code
    ],
)
async def test_websocket_frame_validation_closes_and_settles_admission(
    tls_contexts, side, frame, reason, caplog
):
    caplog.set_level("INFO", logger=AUDIT_LOGGER)
    slots, global_slots = asyncio.Semaphore(1), asyncio.Semaphore(1)
    origin = FrameOrigin(frame if side == "origin" else b"")
    async with running_gateway(tls_contexts, origin, slots=slots, global_slots=global_slots) as (
        port,
        _,
    ):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            writer.write(ws_wire())
            await writer.drain()
            assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 101")
            if side == "client":
                writer.write(frame)
                await writer.drain()
            received = await asyncio.wait_for(reader.read(), 2)
            assert b"HTTP/1.1" not in received  # Never write an HTTP error after upgrade.
        finally:
            writer.close()
            await writer.wait_closed()
        await asyncio.wait_for(asyncio.gather(*list(origin.gateway_tasks)), 1)
        assert not slots.locked() and not global_slots.locked()
    events = [
        json.loads(record.message) for record in caplog.records if record.name == AUDIT_LOGGER
    ]
    assert events[-1]["reason"] == reason
    assert events[-1]["verdict"] == "refused"


async def test_websocket_fragment_control_bytes_and_activity_survive_until_joined_close(
    tls_contexts, monkeypatch
):
    from websockets.frames import Frame, Opcode

    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.15)
    source = b"".join(
        [
            Frame(Opcode.TEXT, b"hello ", fin=False).serialize(mask=False),
            Frame(Opcode.PING, b"probe").serialize(mask=False),
            Frame(Opcode.CONT, "world \u20ac".encode()).serialize(mask=False),
        ]
    )
    origin = FrameOrigin(source)
    async with running_gateway(tls_contexts, origin) as (port, _):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(ws_wire())
        await writer.drain()
        assert b"101" in await reader.readuntil(b"\r\n\r\n")
        assert await reader.readexactly(len(source)) == source
        sent = bytearray()
        for _ in range(4):
            # One direction progresses for longer than the idle bound; a quiet
            # opposite reader must not cancel that healthy session.
            frame = Frame(Opcode.PONG, b"probe").serialize(mask=True)
            sent.extend(frame)
            writer.write(frame)
            await writer.drain()
            await asyncio.sleep(0.055)
        closing = Frame(Opcode.CLOSE, b"\x03\xe8").serialize(mask=True)
        writer.write(closing)
        await writer.drain()
        # This origin deliberately never replies to close: the close budget,
        # independently bounded from the shared inactivity budget, terminates it.
        monkeypatch.setattr(gateway, "_CONNECT_TIMEOUT_S", 0.05)
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()
    assert origin.received == bytes(sent) + closing


@pytest.mark.parametrize("limit", ["upload", "download", "idle", "total"])
async def test_websocket_independent_resource_limits_deny_and_settle(
    tls_contexts, monkeypatch, caplog, limit
):
    from websockets.frames import Frame, Opcode

    caplog.set_level("INFO", logger=AUDIT_LOGGER)
    monkeypatch.setattr(gateway, "_UPLOAD_LIMIT", 1 if limit == "upload" else 1024)
    monkeypatch.setattr(gateway, "_DOWNLOAD_LIMIT", 1 if limit == "download" else 1024)
    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.05 if limit == "idle" else 2)
    monkeypatch.setattr(gateway, "_EXCHANGE_TIMEOUT_S", 0.05 if limit == "total" else 2)
    frame = Frame(Opcode.BINARY, b"ab")
    origin = FrameOrigin(frame.serialize(mask=False) if limit == "download" else b"")
    async with running_gateway(tls_contexts, origin) as (port, _):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(ws_wire())
        await writer.drain()
        assert b"101" in await reader.readuntil(b"\r\n\r\n")
        if limit == "upload":
            writer.write(frame.serialize(mask=True))
            await writer.drain()
        assert b"HTTP/1.1" not in await asyncio.wait_for(reader.read(), 1)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(asyncio.gather(*list(origin.gateway_tasks)), 1)
    events = [
        json.loads(record.message) for record in caplog.records if record.name == AUDIT_LOGGER
    ]
    assert events[-1]["reason"] == (
        f"{limit}_limit" if limit in {"upload", "download"} else "timeout"
    )
    assert events[-1]["verdict"] == "refused"


@pytest.mark.parametrize("status", [200, 401, 429, 500])
async def test_websocket_nonupgrade_provider_errors_preserve_status_headers_and_stream(
    tls_contexts, status
):
    origin = Origin(
        status=status,
        chunks=(b"provider detail",),
        headers=[(b"retry-after", b"7"), (b"x-request-id", b"synthetic-provider-id")],
        early=True,
    )
    async with running_gateway(tls_contexts, origin) as (port, _):
        response = await exchange(port, ws_wire())
        assert response.startswith(f"HTTP/1.1 {status} ".encode())
        assert b"retry-after: 7" in response.lower()
        assert b"x-request-id: synthetic-provider-id" in response.lower()
        assert b"provider detail" in response
        assert b"101" not in response.split(b"\r\n")[0]
