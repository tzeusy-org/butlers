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
    def __init__(self, status=200, chunks=(b"healthy",), delay=0, headers=(), raw=None):
        self.status, self.chunks, self.delay, self.headers = status, chunks, delay, headers
        self.raw = raw
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
    tls_contexts, origin=None, *, answers=None, slots=None, context=None, global_slots=None
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
                    ROUTE,
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
