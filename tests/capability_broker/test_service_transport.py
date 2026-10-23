"""Private transport software contracts; synthetic operations are NOT A2 commits."""

import asyncio
import copy
import os
import socket
import ssl
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from butlers.capability_broker.transport.client import PrivateClient
from butlers.capability_broker.transport.config import PeerPolicy, TransportConfig
from butlers.capability_broker.transport.protocol import (
    PROTOCOL,
    RECEIPT_PROTOCOL,
    ProtocolError,
    receipt_digest,
    reply,
    rpc_request,
    rpc_result,
    strict_json,
)
from butlers.capability_broker.transport.server import PrivateApp, PrivateServer
from butlers.capability_broker.transport.tls import HeadGate
from butlers.core.capability_json import arguments_digest, canonical_bytes

pytestmark = pytest.mark.unit


@pytest.fixture
def material(tmp_path):
    """Disposable test-only trust/material. Keys never enter logs or artifacts."""
    paths = []
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-only")])
    now = datetime.now(UTC)
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(ca_key, hashes.SHA256())
    )

    def save(label, data):
        path = tmp_path / label
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        paths.append(path)
        return path

    ca_path = save("ca.pem", ca.public_bytes(serialization.Encoding.PEM))

    def leaf(label, uri, *, client=False, expired=False):
        key = ec.generate_private_key(ec.SECP256R1())
        sans = [x509.DNSName("localhost"), x509.UniformResourceIdentifier(uri)]
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectAlternativeName(sans), False)
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH]
                ),
                False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        cert_path = save(label + ".pem", cert.public_bytes(serialization.Encoding.PEM))
        key_path = save(
            label + ".key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        return cert_path, key_path

    def contexts(
        *,
        server_uri="urn:test:health",
        client_uri="urn:test:control",
        expired=False,
        wrong_eku=False,
        missing_client=False,
        untrusted=False,
    ):
        server_cert, server_key = leaf(
            "server" + str(uuid4()), server_uri, client=wrong_eku, expired=expired
        )
        client_cert, client_key = leaf("client" + str(uuid4()), client_uri, client=True)
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.verify_mode = ssl.CERT_REQUIRED
        server.load_verify_locations(cafile=ca_path)
        server.load_cert_chain(server_cert, server_key)
        client = ssl.create_default_context(cafile=None if untrusted else ca_path)
        if not missing_client:
            client.load_cert_chain(client_cert, client_key)
        return server, client

    try:
        yield contexts
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


SERVICE = PeerPolicy(
    "owning-service", "health", frozenset({"DNS:localhost", "URI:urn:test:health"})
)
CONTROL = PeerPolicy(
    "broker-control", "health", frozenset({"DNS:localhost", "URI:urn:test:control"}), True
)


def call_value(op="CANCEL"):
    effect = str(uuid4())
    args = {"value": "synthetic"}
    value = {
        "protocol": PROTOCOL,
        "operation": op,
        "request_id": str(uuid4()),
        "identity": {
            "goal_id": str(uuid4()),
            "effect_id": effect,
            "action_id": effect,
            "owning_domain": "health",
            "capability": "health__record",
            "args_digest": arguments_digest(args),
        },
    }
    if op == "PARK":
        value.update(arguments=args, allow_standing_rule=False)
    return value


def fixture_reply(value, outcome="cancelled"):
    """Explicit software-only receipt value; no durable producer is claimed."""
    r = {
        "protocol": RECEIPT_PROTOCOL,
        "receipt_id": str(uuid4()),
        "identity": copy.deepcopy(value["identity"]),
        "outcome": outcome,
        "approval_ref": None if outcome == "fenced_absent" else "synthetic-row",
    }
    r["digest"] = receipt_digest(r)
    return {
        k: copy.deepcopy(value[k]) for k in ("protocol", "operation", "request_id", "identity")
    } | {"outcome": outcome, "receipt": r}


@asynccontextmanager
async def running(
    material,
    *,
    server_uri="urn:test:health",
    client_uri="urn:test:control",
    expired=False,
    wrong_eku=False,
):
    server_context, client_context = material(
        server_uri=server_uri, client_uri=client_uri, expired=expired, wrong_eku=wrong_eku
    )
    calls = []

    async def operation(value, peer, assertion):
        calls.append((value, peer, assertion))
        return fixture_reply(value, "parked" if value["operation"] == "PARK" else "cancelled")

    server = PrivateServer(PrivateApp("health", operation), server_context, CONTROL)
    async with server.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{server.port}/mcp", SERVICE), client_context
        )
        try:
            yield server, client, calls
        finally:
            await client.aclose()
    assert not server.protocols and not server.handshakes and server.slot_count == 0


async def test_real_TLS_MCP_roundtrip_uses_connection_peer_and_closed_receipt(material):
    async with running(material) as (server, client, calls):
        for op in ("PARK", "LOOKUP", "CANCEL"):
            value = call_value(op)
            result = await client.call(
                value, assertion="synthetic.assertion.value" if op == "PARK" else None
            )
            assert result["identity"] == value["identity"]
            assert result["receipt"]["digest"] == receipt_digest(result["receipt"])
        assert len(calls) == 3
        assert all(p.role == "broker-control" and p.domain == "health" for _, p, _ in calls)
        assert calls[0][2] == "synthetic.assertion.value"
        assert all(a is None for _, _, a in calls[1:])
        assert server.http_bytes > 0


@pytest.mark.parametrize("change", ["role", "domain", "expired", "eku"])
async def test_real_TLS_wrong_service_refused_before_any_HTTP(material, change):
    kwargs = {"server_uri": "urn:test:control" if change == "role" else "urn:test:other"}
    if change in {"expired", "eku"}:
        kwargs = {"expired": change == "expired", "wrong_eku": change == "eku"}
    async with running(material, **kwargs) as (server, client, calls):
        with pytest.raises(BaseExceptionGroup):
            await client.call(call_value())
        assert server.http_bytes == 0 and calls == []


async def test_real_TLS_wrong_client_identity_cannot_reach_MCP(material):
    async with running(material, client_uri="urn:test:other") as (server, client, calls):
        with pytest.raises(BaseExceptionGroup):
            await client.call(call_value())
        assert calls == [] and server.http_bytes == 0


def test_closed_receipt_RPC_errors_ambiguity_and_raw_duplicates_refuse():
    value = call_value()
    good = fixture_reply(value)
    outgoing = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "broker_approval_cancel", "arguments": value},
    }

    def rpc(app):
        return {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "isError": False,
                "structuredContent": app,
                "content": [{"type": "text", "text": canonical_bytes(app).decode()}],
            },
        }

    rpc_result(canonical_bytes(rpc(good)), outgoing, "health")
    for mutate in (
        lambda r: r.update(id=True),
        lambda r: r.update(error={"code": -1, "message": "closed"}),
        lambda r: r["result"].update(isError=True),
        lambda r: r["result"].pop("structuredContent"),
        lambda r: r["result"]["content"].append({"type": "text", "text": "{}"}),
        lambda r: r["result"]["content"][0].update(text="{}"),
    ):
        broken = rpc(copy.deepcopy(good))
        mutate(broken)
        with pytest.raises(ProtocolError):
            rpc_result(canonical_bytes(broken), outgoing, "health")
    for field, replacement in (
        ("receipt_id", str(uuid4())),
        ("outcome", "committed"),
        ("digest", "0" * 64),
        ("approval_ref", None),
    ):
        broken = copy.deepcopy(good)
        broken["receipt"][field] = replacement
        with pytest.raises(ProtocolError):
            reply(broken, value)
    with pytest.raises(ProtocolError):
        strict_json(b'{"nested":{"id":1,"id":2}}')


@pytest.mark.parametrize("response", [False, True])
def test_raw_head_cap_complete_onechunk_and_fragmented_precedes_parser(response, monkeypatch):
    import h11

    def forbidden(*args, **kwargs):
        pytest.fail("oversized head reached parser")

    monkeypatch.setattr(h11, "Connection", forbidden)
    prefix = b"HTTP/1.1 200 OK\r\n" if response else b"POST /mcp HTTP/1.1\r\n"
    raw = prefix + b"X-Large: " + b"a" * 16384 + b"\r\n\r\n"
    with pytest.raises(ProtocolError, match="header_limit"):
        HeadGate(response=response).feed(raw)
    gate = HeadGate(response=response)
    assert gate.feed(raw[:8000]) == b""
    with pytest.raises(ProtocolError, match="header_limit"):
        gate.feed(raw[8000:])


@asynccontextmanager
async def peer_server(handler, context=None):
    """Every accepted test connection/task is owned and finally drained."""
    tasks, writers, sockets = set(), [], []

    async def run(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        writers.append(writer)
        sockets.append(writer.get_extra_info("socket"))
        try:
            await handler(reader, writer)
        finally:
            writer.transport.abort()
            try:
                async with asyncio.timeout(2):
                    await writer.wait_closed()
            except (ConnectionError, ssl.SSLError, asyncio.CancelledError):
                pass
            tasks.discard(task)

    server = await asyncio.start_server(
        run, "127.0.0.1", 0, ssl=context, ssl_handshake_timeout=2 if context else None
    )
    try:
        yield server.sockets[0].getsockname()[1], writers
    finally:
        server.close()
        await server.wait_closed()
        for writer in writers:
            writer.transport.abort()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        async with asyncio.timeout(3):
            await asyncio.gather(*pending, return_exceptions=True)
        assert not tasks
        assert all(sock.fileno() == -1 for sock in sockets)


@pytest.mark.parametrize("direction", ["request", "response"])
@pytest.mark.parametrize("fragmented", [False, True])
async def test_real_TLS_header_bounds_hold_for_complete_and_fragmented_heads(
    material, direction, fragmented, monkeypatch
):
    server_ctx, client_ctx = material()
    prefix = b"POST /mcp HTTP/1.1\r\n" if direction == "request" else b"HTTP/1.1 200 OK\r\n"
    raw = prefix + b"X-Padding: " + b"x" * 16384 + b"\r\nContent-Length: 0\r\n\r\n"

    async def send(writer):
        if fragmented:
            writer.write(raw[:8000])
            await writer.drain()
            await asyncio.sleep(0.01)
            writer.write(raw[8000:])
        else:
            writer.write(raw)
        await writer.drain()

    if direction == "request":
        calls = []

        async def operation(*args):
            calls.append(True)
            pytest.fail("oversized request reached operation")

        private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
        async with private.run():
            reader, writer = await asyncio.open_connection(
                "localhost", private.port, ssl=client_ctx
            )
            try:
                await send(writer)
                try:
                    async with asyncio.timeout(2):
                        assert await reader.read() == b""
                except (ConnectionError, ssl.SSLError):
                    pass
                assert calls == []
            finally:
                writer.transport.abort()
                try:
                    await writer.wait_closed()
                except (ConnectionError, ssl.SSLError):
                    pass
    else:
        parsed = []
        import butlers.capability_broker.transport.client as client_module

        def forbidden(*args):
            parsed.append(True)
            pytest.fail("oversized response reached SDK JSON parser")

        monkeypatch.setattr(client_module, "rpc_result", forbidden)

        async def respond(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            await send(writer)
            await reader.read()

        async with peer_server(respond, server_ctx) as (port, _):
            client = PrivateClient(
                TransportConfig(f"https://localhost:{port}/mcp", SERVICE), client_ctx
            )
            try:
                with pytest.raises(BaseExceptionGroup):
                    await client.call(call_value())
                assert parsed == []
            finally:
                await client.aclose()


async def until(predicate):
    # A finite observation of actual transport state, not a guessed sleep race.
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 -- bounded transport-state observer
            await asyncio.sleep(0.001)


async def test_real_backpressure_double_cancellation_settles_owned_FD_tasks_and_pool(material):
    from butlers.capability_broker.transport.tls import PurposeBackend

    server_ctx, client_ctx = material()
    accepted = asyncio.Event()
    release = asyncio.Event()

    async def unread(reader, writer):
        writer.get_extra_info("socket").setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        writer.transport.pause_reading()
        accepted.set()
        await release.wait()

    started = time.monotonic()
    async with peer_server(unread, server_ctx) as (port, peers):
        backend = PurposeBackend("localhost", port, SERVICE)
        backend.deadline = started + 10
        stream = await backend.connect_tcp("localhost", port)
        write = None
        try:
            await stream.start_tls(client_ctx, server_hostname="localhost")
            await accepted.wait()
            # Exercise the actual stream's backpressure teardown, not an HTTP
            # business operation or a larger allowed application body.
            stream.writer.get_extra_info("socket").setsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF, 4096
            )
            owned_socket = stream.writer.get_extra_info("socket")
            stream.writer.transport.set_write_buffer_limits(high=1024, low=0)

            async def pressure():
                for _ in range(512):
                    await stream.write(b"x" * 65536)
                    await asyncio.sleep(0)

            write = asyncio.create_task(pressure())
            await until(lambda: write.done() or stream.writer.transport.get_write_buffer_size() > 0)
            assert not write.done(), (
                type(write.exception()).__name__ if write.exception() else "write_completed"
            )
            write.cancel()
            write.cancel()
            with pytest.raises(asyncio.CancelledError):
                await write
            await until(lambda: owned_socket.fileno() == -1)
            assert stream.unusable
        finally:
            if write is not None:
                write.cancel()
                await asyncio.gather(write, return_exceptions=True)
            stream.abort()
            try:
                await stream.aclose()
            except (ConnectionError, ssl.SSLError, TimeoutError, asyncio.CancelledError):
                pass
            release.set()
        assert not backend.streams
    assert peers and time.monotonic() - started < 10

    entered = asyncio.Event()
    calls = []

    async def blocked_operation(value, peer, assertion):
        calls.append(value)
        entered.set()
        await asyncio.Future()

    pool_started = time.monotonic()
    private = PrivateServer(PrivateApp("health", blocked_operation), server_ctx, CONTROL)
    async with private.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{private.port}/mcp", SERVICE), client_ctx
        )
        task = asyncio.create_task(client.call(call_value()))
        try:
            async with asyncio.timeout(3):
                await entered.wait()
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await until(lambda: not client.transport.pool.connections)
            assert len(calls) == 1
            assert all(
                s.writer.get_extra_info("socket").fileno() == -1
                for s in client.transport.backend.streams
            )
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await client.aclose()
    assert not private.protocols and not private.server_state.tasks
    assert time.monotonic() - pool_started < 10


async def test_real_interrupted_TLS_upgrade_releases_actual_socket(material):
    from butlers.capability_broker.transport.tls import PurposeBackend

    _, context = material()
    hello = asyncio.Event()

    async def no_handshake(reader, writer):
        assert await reader.read(5)  # actual TLS ClientHello, no server TLS reply
        hello.set()
        await reader.read()

    started = time.monotonic()
    async with peer_server(no_handshake) as (port, peers):
        backend = PurposeBackend("localhost", port, SERVICE)
        backend.deadline = started + 10
        stream = await backend.connect_tcp("localhost", port)
        owned_socket = stream.writer.get_extra_info("socket")
        task = asyncio.create_task(stream.start_tls(context, server_hostname="localhost"))
        try:
            async with asyncio.timeout(2):
                await hello.wait()
            task.cancel()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await until(lambda: owned_socket.fileno() == -1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            try:
                await stream.aclose()
            except (ConnectionError, ssl.SSLError, TimeoutError, asyncio.CancelledError):
                pass
    assert peers and not backend.streams and time.monotonic() - started < 10


async def test_second_cancellation_during_real_pool_cleanup_retains_ownership(
    material, monkeypatch
):
    server_ctx, client_ctx = material()
    entered, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def operation(value, peer, assertion):
        calls.append((value["operation"], assertion))
        if value["operation"] == "PARK":
            entered.set()
            await asyncio.Future()
        return fixture_reply(value)

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    started = time.monotonic()
    async with private.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{private.port}/mcp", SERVICE), client_ctx
        )
        original_close = client.transport.pool.aclose

        async def paused_real_close():
            closing.set()
            await release.wait()
            await original_close()

        monkeypatch.setattr(client.transport.pool, "aclose", paused_real_close)
        task = asyncio.create_task(client.call(call_value("PARK"), assertion="synthetic.assertion"))
        queued = None
        try:
            async with asyncio.timeout(2):
                await entered.wait()
            sockets = [s.writer.get_extra_info("socket") for s in client.transport.backend.streams]
            assert sockets
            task.cancel()
            async with asyncio.timeout(2):
                await closing.wait()
            task.cancel()  # distinct delivery while cleanup is actually awaiting
            await asyncio.sleep(0)
            assert not task.done() and client.lock.locked()
            queued = asyncio.create_task(client.call(call_value()))
            await asyncio.sleep(0)
            queued.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queued
            assert calls == [("PARK", "synthetic.assertion")]
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert all(sock.fileno() == -1 for sock in sockets)
            assert not client.transport.pool.connections and not client.lock.locked()
            assert "X-Butlers-Assertion" not in client.http.headers
            result = await client.call(call_value())
            assert result["outcome"] == "cancelled" and calls[-1] == ("CANCEL", None)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, *([queued] if queued else []), return_exceptions=True)
            await client.aclose()
    assert time.monotonic() - started < 10 and not private.server_state.tasks


@pytest.mark.parametrize(
    "fault",
    ["redirect", "sse", "chunked", "compressed", "oversize", "truncated", "extra", "rpc_duplicate"],
)
async def test_actual_TLS_reply_refusals_never_reach_SDK_or_retry(material, fault, monkeypatch):
    server_ctx, client_ctx = material()
    received, parsed = [], []
    import butlers.capability_broker.transport.client as client_module

    original_parser = client_module.rpc_result

    def checked(raw, *args):
        if fault != "rpc_duplicate":
            parsed.append(True)
            pytest.fail("invalid HTTP profile reached RPC parser")
        return original_parser(raw, *args)

    monkeypatch.setattr(client_module, "rpc_result", checked)
    body = b'{"jsonrpc":"2.0","id":0,"id":0,"result":{}}'
    responses = {
        "redirect": b"HTTP/1.1 307 Temporary Redirect\r\nLocation: https://localhost:1/mcp\r\nContent-Length: 0\r\n\r\n",
        "sse": b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 0\r\n\r\n",
        "chunked": b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
        "compressed": b"HTTP/1.1 200 OK\r\nContent-Encoding: gzip\r\nContent-Length: 0\r\n\r\n",
        "oversize": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 65537\r\n\r\n",
        "truncated": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 5\r\n\r\n{}",
        "extra": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}x",
        "rpc_duplicate": b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n\r\n"
        + body,
    }

    async def respond(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        received.append(True)
        writer.write(responses[fault])
        await writer.drain()

    async with peer_server(respond, server_ctx) as (port, _):
        client = PrivateClient(
            TransportConfig(f"https://localhost:{port}/mcp", SERVICE), client_ctx
        )
        try:
            with pytest.raises(BaseExceptionGroup):
                await client.call(call_value())
            assert received == [True] and parsed == []
            assert not client.transport.pool.connections
        finally:
            await client.aclose()


@pytest.mark.parametrize("change", ["missing_client", "untrusted"])
async def test_real_TLS_missing_client_or_wrong_trust_has_zero_HTTP(material, change):
    server_ctx, client_ctx = material(**{change: True})
    calls = []

    async def operation(*args):
        calls.append(True)
        pytest.fail("unauthenticated connection reached operation")

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    async with private.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{private.port}/mcp", SERVICE), client_ctx
        )
        try:
            with pytest.raises(BaseExceptionGroup):
                await client.call(call_value())
            assert calls == [] and private.http_bytes == 0
        finally:
            await client.aclose()


@pytest.mark.parametrize("phase", ["handshake", "read"])
async def test_actual_network_stall_refuses_with_cleanup_inside_total_deadline(material, phase):
    server_ctx, client_ctx = material()
    received = []

    async def stalled(reader, writer):
        if phase == "handshake":
            assert await reader.read(5)
        else:
            await reader.readuntil(b"\r\n\r\n")
        received.append(True)
        await reader.read()

    started = time.monotonic()
    async with peer_server(stalled, None if phase == "handshake" else server_ctx) as (port, peers):
        client = PrivateClient(
            TransportConfig(f"https://localhost:{port}/mcp", SERVICE), client_ctx
        )
        try:
            with pytest.raises((BaseExceptionGroup, TimeoutError)):
                await client.call(call_value())
            assert received == [True] and not client.transport.pool.connections
            assert not client.lock.locked()
        finally:
            await client.aclose()
    assert peers and time.monotonic() - started < 10


async def test_accepted_handshake_capacity_and_shutdown_are_owned(material):
    server_ctx, _ = material()
    calls = []

    async def operation(*args):
        calls.append(True)
        pytest.fail("incomplete TLS connection reached operation")

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    connections = []
    started = time.monotonic()
    try:
        async with private.run():
            # Sixteen incomplete real handshakes consume all accepted slots;
            # the seventeenth remains kernel backlog, never an extra TLS task.
            for _ in range(17):
                connections.append(await asyncio.open_connection("localhost", private.port))
            await until(lambda: private.slot_count == 16)
            assert len(private.handshakes) <= 16 and not private.protocols
            assert private.http_bytes == 0 and calls == []
        assert private.slot_count == 0 and not private.handshakes and not private.protocols
    finally:
        for _, writer in connections:
            writer.transport.abort()
        await asyncio.gather(*(w.wait_closed() for _, w in connections), return_exceptions=True)
    assert time.monotonic() - started < 10


def test_closed_target_tuple_and_precoercion_MCP_request_refuse_forgery():
    value = call_value("PARK")
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "broker_approval_park", "arguments": value},
    }
    assert rpc_request(copy.deepcopy(rpc), "health") == rpc
    for mutate in (
        lambda r: r.update(actor="forged"),
        lambda r: r.update(id=True),
        lambda r: r["params"].update(name="ordinary_public_tool"),
        lambda r: r["params"].update(claim_id=str(uuid4())),
        lambda r: r["params"]["arguments"].update(operation=[]),
        lambda r: r["params"]["arguments"]["identity"].update(owning_domain="other"),
        lambda r: r["params"]["arguments"]["identity"].update(action_id=str(uuid4())),
        lambda r: r["params"]["arguments"]["arguments"].update(value="changed"),
        lambda r: r["params"]["arguments"].update(allow_standing_rule="false"),
    ):
        broken = copy.deepcopy(rpc)
        mutate(broken)
        with pytest.raises(ProtocolError):
            rpc_request(broken, "health")
    for mutate in (
        lambda r: r["identity"].update(goal_id=str(uuid4())),
        lambda r: r.update(outcome="dispatched"),
        lambda r: r["receipt"]["identity"].update(goal_id=str(uuid4())),
        lambda r: r["receipt"].update(extra=True),
    ):
        broken = fixture_reply(value, "parked")
        mutate(broken)
        broken["receipt"]["digest"] = receipt_digest(broken["receipt"])
        with pytest.raises(ProtocolError):
            reply(broken, value)


async def test_real_listener_is_closed_when_MCP_lifespan_entry_refuses(material, monkeypatch):
    server_ctx, _ = material()

    async def operation(*args):
        pytest.fail("failed startup reached operation")

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)

    @asynccontextmanager
    async def refused(app):
        raise ProtocolError("test_startup_refusal")
        yield  # pragma: no cover -- async context manager protocol

    monkeypatch.setattr(private.app.app.router, "lifespan_context", refused)
    try:
        with pytest.raises(ProtocolError, match="test_startup_refusal"):
            async with private.run():
                pytest.fail("failed startup yielded server")
        assert private.socket.fileno() == -1
        assert not private.handshakes and not private.protocols and private.slot_count == 0
    finally:
        private.socket.close()  # test owns cleanup even for its RED source


async def test_actual_request_body_cap_and_raw_duplicate_refuse_before_MCP(material):
    server_ctx, client_ctx = material()
    calls = []

    async def operation(*args):
        calls.append(True)
        pytest.fail("invalid raw request reached operation")

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    async with private.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{private.port}/mcp", SERVICE), client_ctx
        )
        try:
            oversized = call_value("PARK")
            oversized["arguments"] = {"value": "x" * 262144}
            oversized["identity"]["args_digest"] = arguments_digest(oversized["arguments"])
            with pytest.raises(ProtocolError, match="request_limit"):
                await client.call(oversized, assertion="synthetic.assertion")
            assert private.http_bytes == 0
        finally:
            await client.aclose()
        duplicate = b'{"jsonrpc":"2.0","id":1,"method":"tools/call","method":"tools/list"}'
        for head, body in (
            (b"Content-Length: 262145\r\n", b""),
            (b"Content-Length: " + str(len(duplicate)).encode() + b"\r\n", duplicate),
        ):
            reader, writer = await asyncio.open_connection(
                "localhost", private.port, ssl=client_ctx
            )
            try:
                writer.write(
                    b"POST /mcp HTTP/1.1\r\nHost: localhost\r\n"
                    b"Content-Type: application/json\r\nConnection: close\r\n"
                    + head
                    + b"\r\n"
                    + body
                )
                await writer.drain()
                async with asyncio.timeout(2):
                    response = await reader.read()
                # Either an early disconnect or the ordinary ASGI 500 error;
                # neither is a successful MCP/owning reply.
                assert not response or response.startswith(b"HTTP/1.1 500 ")
            except (ConnectionError, ssl.SSLError):
                pass
            finally:
                writer.transport.abort()
                await asyncio.gather(writer.wait_closed(), return_exceptions=True)
        assert calls == []


async def test_operation_exception_is_closed_before_MCP_wire_and_logging(
    material, monkeypatch, caplog
):
    server_ctx, client_ctx = material()
    marker = "synthetic-private-error-marker"
    seen = []
    import butlers.capability_broker.transport.client as client_module

    original = client_module.rpc_result

    def observe(raw, *args):
        seen.append(raw)
        return original(raw, *args)

    monkeypatch.setattr(client_module, "rpc_result", observe)

    async def refused_operation(*args):
        raise RuntimeError(marker)

    private = PrivateServer(PrivateApp("health", refused_operation), server_ctx, CONTROL)
    async with private.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{private.port}/mcp", SERVICE), client_ctx
        )
        try:
            with pytest.raises(BaseExceptionGroup):
                await client.call(call_value())
            assert seen and all(marker.encode() not in raw for raw in seen)
            assert marker not in caplog.text
        finally:
            await client.aclose()


async def test_next_real_MCP_operation_outlives_keepalive_without_extending_call(material):
    server_ctx, client_ctx = material()
    entered = []

    async def operation(value, peer, assertion):
        entered.append((value, tuple(private.protocols)))
        await asyncio.sleep(2.25)  # healthy read remains below the existing 3s cap
        return fixture_reply(value)

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    started = time.monotonic()
    async with private.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{private.port}/mcp", SERVICE), client_ctx
        )
        try:
            value = call_value()
            result = await client.call(value)
            assert result["identity"] == value["identity"]
            assert len(entered) == 1 and len(entered[0][1]) == 1
            assert entered[0][1][0] in private.protocols
        finally:
            await client.aclose()
    assert 2 < time.monotonic() - started < 10


async def read_private_response(reader):
    head = await reader.readuntil(b"\r\n\r\n")
    length = next(
        int(line.split(b":", 1)[1])
        for line in head.split(b"\r\n")
        if line.lower().startswith(b"content-length:")
    )
    return head, await reader.readexactly(length)


async def test_fragmented_next_MCP_head_has_body_bound_and_true_idle_timeout(material):
    server_ctx, client_ctx = material()

    async def operation(*args):
        pytest.fail("protocol-only request invoked an owning operation")

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    body = canonical_bytes({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    head = (
        b"POST /mcp HTTP/1.1\r\nHost: localhost\r\n"
        b"Content-Type: application/json\r\nAccept: application/json, text/event-stream\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n"
    )
    async with private.run():
        reader, writer = await asyncio.open_connection("localhost", private.port, ssl=client_ctx)
        owned = writer.get_extra_info("socket")
        try:
            writer.write(head + body)
            await writer.drain()
            async with asyncio.timeout(2):
                response, _ = await read_private_response(reader)
            assert response.startswith(b"HTTP/1.1 200 ")
            writer.write(head[:12])
            await writer.drain()
            await asyncio.sleep(2.25)
            writer.write(head[12:] + body)
            await writer.drain()
            async with asyncio.timeout(2):
                response, _ = await read_private_response(reader)
            assert response.startswith(b"HTTP/1.1 200 ")
            async with asyncio.timeout(3):
                assert await reader.read() == b""  # actual quiet keepalive closes
        finally:
            writer.transport.abort()
            await asyncio.gather(writer.wait_closed(), return_exceptions=True)
        assert owned.fileno() == -1
        # A partial body remains bounded and does not gain a new budget per byte.
        reader, writer = await asyncio.open_connection("localhost", private.port, ssl=client_ctx)
        try:
            writer.write(head + body[:1])
            await writer.drain()
            started = time.monotonic()
            async with asyncio.timeout(6):
                assert await reader.read() == b""
            assert time.monotonic() - started < 5.8
        finally:
            writer.transport.abort()
            await asyncio.gather(writer.wait_closed(), return_exceptions=True)


@pytest.mark.parametrize("used", [False, True])
async def test_idle_client_close_has_one_resource_budget_and_repeats_safely(material, used):
    async with running(material) as (_, client, calls):
        sockets = []
        if used:
            await client.call(call_value())
            sockets = [s.writer.get_extra_info("socket") for s in client.transport.backend.streams]
            assert sockets and calls
            # Model the completed idle state after its prior deadline expires;
            # do not patch the clock or extend an active call's budget.
            client.transport.backend.deadline = time.monotonic() - 1
        started = time.monotonic()
        await client.aclose()
        await client.aclose()
        assert time.monotonic() - started < 5
        assert all(sock.fileno() == -1 for sock in sockets)
        assert not client.transport.pool.connections and not client.transport.backend.streams


async def test_server_second_cancel_retains_actual_MCP_lifespan_and_resources(
    material, monkeypatch
):
    server_ctx, client_ctx = material()
    ready, closing, release, exited = (asyncio.Event() for _ in range(4))
    requested = asyncio.Event()

    async def operation(*args):
        requested.set()
        await asyncio.Future()

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    original = private.app.app.router.lifespan_context

    @asynccontextmanager
    async def tracked(app):
        async with original(app):
            try:
                yield
            finally:
                closing.set()
                await release.wait()
        exited.set()  # actual delegated MCP lifespan has finished, not just begun

    monkeypatch.setattr(private.app.app.router, "lifespan_context", tracked)

    async def serve():
        async with private.run():
            ready.set()
            await asyncio.Future()

    hosting = asyncio.create_task(serve())
    client = call = None
    sockets = []
    started = time.monotonic()
    try:
        async with asyncio.timeout(2):
            await ready.wait()
        client = PrivateClient(
            TransportConfig(f"https://localhost:{private.port}/mcp", SERVICE), client_ctx
        )
        call = asyncio.create_task(client.call(call_value()))
        async with asyncio.timeout(2):
            await requested.wait()
        sockets = [s.writer.get_extra_info("socket") for s in client.transport.backend.streams]
        assert sockets and private.server_state.tasks
        hosting.cancel()
        async with asyncio.timeout(2):
            await closing.wait()
        hosting.cancel()  # distinct delivery during the real lifespan-exit await
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not hosting.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await hosting
        assert exited.is_set()
        assert private.socket.fileno() == -1
        assert not private.protocols and not private.handshakes and private.slot_count == 0
        assert not private.server_state.tasks
        await until(lambda: all(sock.fileno() == -1 for sock in sockets))
    finally:
        release.set()
        hosting.cancel()
        if call is not None:
            call.cancel()
        async with asyncio.timeout(5):
            await asyncio.gather(
                hosting, *([call] if call is not None else []), return_exceptions=True
            )
            if client is not None:
                await client.aclose()
    assert time.monotonic() - started < 10


async def test_server_cleanup_failure_still_exits_real_lifespan_and_reports_failure(
    material, monkeypatch
):
    server_ctx, client_ctx = material()
    exited = asyncio.Event()
    delayed = []

    async def operation(*args):
        pytest.fail("cleanup-only connection invoked an operation")

    private = PrivateServer(PrivateApp("health", operation), server_ctx, CONTROL)
    original_lifespan = private.app.app.router.lifespan_context
    original_protocol = private._protocol

    @asynccontextmanager
    async def tracked(app):
        async with original_lifespan(app):
            yield
        exited.set()

    def deferred_release_protocol():
        protocol = original_protocol()
        actual_lost = protocol.connection_lost

        def defer(exc):
            # Fault injection delays actual slot/bookkeeping notification,
            # never the transport abort/FD close or the TLS/HTTP implementation.
            delayed.append((actual_lost, exc))

        protocol.connection_lost = defer
        return protocol

    monkeypatch.setattr(private.app.app.router, "lifespan_context", tracked)
    monkeypatch.setattr(private, "_protocol", deferred_release_protocol)
    writer = None
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            async with private.run():
                _, writer = await asyncio.open_connection("localhost", private.port, ssl=client_ctx)
                await until(lambda: bool(private.protocols))
        assert exited.is_set() and private._lifetime_task.done()
        assert private.socket.fileno() == -1 and private.slot_count == 1
        assert delayed and time.monotonic() - started < 5
    finally:
        for callback, exc in delayed:
            callback(exc)
        if writer is not None:
            writer.transport.abort()
            await asyncio.gather(writer.wait_closed(), return_exceptions=True)
        await until(lambda: private.slot_count == 0)
    assert not private.protocols


async def test_cancellation_consumer_reclaims_exact_tuple_over_real_private_MCP(
    material, monkeypatch
):
    from butlers.capability_broker.cancel_worker import CancellationWorker
    from butlers.core.goal_stop import CancelClaim, CancelIdentity

    value = call_value()
    i = value["identity"]
    from uuid import UUID

    identity = CancelIdentity(
        UUID(i["goal_id"]),
        UUID(i["effect_id"]),
        "health",
        UUID(i["action_id"]),
        i["capability"],
        i["args_digest"],
    )

    # Software repository control only: not actual A0 commit/lease/RLS proof.
    class StopControl:
        def __init__(self):
            self.claim = CancelClaim(identity, uuid4(), 2)
            self.acks = []
            self.held = False
            self.ack_error = False

        async def claim_cancel_work(self, candidate, *, lease_seconds):
            assert candidate == identity and lease_seconds == 60
            return self.claim

        async def ack_cancel_work(self, claim, receipt, outcome):
            assert not self.held and claim is self.claim
            if self.ack_error:
                raise RuntimeError("controlled_ACK_commit_unknown")
            self.acks.append((claim, receipt, outcome))

        async def cancel_candidates(self, *, limit):
            assert limit == 4
            return (identity,)

    stop = StopControl()
    observed = "unknown"
    calls = []
    phase_clock = [time.monotonic()]
    spend_lookup_budget = False

    async def operation(outgoing, peer, assertion):
        assert not stop.held and assertion is None
        assert peer.role == "broker-control" and peer.domain == "health"
        assert outgoing["identity"] == i
        calls.append(outgoing["operation"])
        if outgoing["operation"] == "LOOKUP":
            if spend_lookup_budget:
                phase_clock[0] += 15
            if observed == "unknown":
                return {
                    k: outgoing[k] for k in ("protocol", "operation", "request_id", "identity")
                } | {
                    "outcome": "unknown",
                    "receipt": None,
                }
            return fixture_reply(outgoing, observed)
        return fixture_reply(outgoing, "fenced_absent")

    server_context, client_context = material()
    server = PrivateServer(PrivateApp("health", operation), server_context, CONTROL)
    async with server.run():
        client = PrivateClient(
            TransportConfig(f"https://localhost:{server.port}/mcp", SERVICE), client_context
        )
        try:
            worker = CancellationWorker(stop, {"health": client})
            # Claim-before-send crash recovery: fresh unknown is not an ACK but
            # permits one atomic owning revalidation. Fixture receipt is NOT A2.
            assert await worker.run_batch() == ("acknowledged",)
            assert calls == ["LOOKUP", "CANCEL"]
            assert len(stop.acks) == 1 and stop.acks[0][2] == "fenced_absent"
            for outcome in ("dispatched", "committed"):
                observed = outcome
                calls.clear()
                stop.acks.clear()
                assert await worker.process(identity) == "unknown"
                assert calls == ["LOOKUP"] and stop.acks == []
            # An uncertain central ACK must never trigger a second CANCEL.
            observed = "unknown"
            calls.clear()
            stop.ack_error = True
            assert await worker.process(identity) == "unknown"
            assert calls == ["LOOKUP", "CANCEL"] and stop.acks == []
            # Advance only the worker's admission clock, not TLS/SDK timers.
            # Fifteen seconds remaining cannot admit native10 + ACK5 + book1.
            import butlers.capability_broker.cancel_worker as consumer

            monkeypatch.setattr(consumer, "monotonic", lambda: phase_clock[0])
            spend_lookup_budget = True
            stop.ack_error = False
            calls.clear()
            assert await worker.process(identity) == "unknown"
            assert calls == ["LOOKUP"] and stop.acks == []
            # A missing configured service never claims/discovers transport.
            assert await CancellationWorker(stop, {}).process(identity) == "unavailable"
        finally:
            await client.aclose()
    assert not server.protocols and not server.handshakes and server.slot_count == 0


def broker_lifecycle_listener():
    """Real listener/MCP lifetime only; these collaborators grant no authority."""
    from butlers.capability_broker.service import Broker
    from butlers.capability_broker.session import BrokerSessionListener

    broker = Broker(None, object(), object(), object())
    return BrokerSessionListener(broker, host="127.0.0.1", port=0)


@pytest.mark.parametrize("phase", ["startup", "shutdown"])
async def test_broker_listener_repeated_cancel_retains_real_MCP_lifetime(monkeypatch, phase):
    listener = broker_lifecycle_listener()
    app = listener._app.app
    original = app.router.lifespan_context
    entered, closing, release, exited = (asyncio.Event() for _ in range(4))
    lifecycle_tasks = []

    @asynccontextmanager
    async def tracked(application):
        lifecycle_tasks.append(asyncio.current_task())
        async with original(application):
            entered.set()
            if phase == "startup":
                await release.wait()  # actual MCP entered; listener not yet ready
            yield
            closing.set()
            if phase == "shutdown":
                await release.wait()
        assert asyncio.current_task() is lifecycle_tasks[0]
        exited.set()  # actual MCP task group exited on its entering task

    monkeypatch.setattr(app.router, "lifespan_context", tracked)
    starting = asyncio.create_task(listener.start())
    closing_call = None
    writer = None
    accepted_sockets = []
    try:
        async with asyncio.timeout(3):
            await entered.wait()
            if phase == "shutdown":
                await starting
                reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
                writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
                await writer.drain()
                assert b"401" in await reader.readuntil(b"\r\n\r\n")
                assert listener._server.server_state.connections
                accepted_sockets = [
                    connection.transport.get_extra_info("socket")
                    for connection in listener._server.server_state.connections
                ]
                closing_call = asyncio.create_task(listener.close())
                await closing.wait()
            else:
                closing_call = starting
        closing_call.cancel("original_listener_cancel")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not closing_call.done() and not exited.is_set()
        closing_call.cancel("second_listener_cancel")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not closing_call.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError, match="original_listener_cancel"):
            async with asyncio.timeout(6):
                await closing_call
        assert exited.is_set() and listener.task.done()
        assert listener._socket.fileno() == -1
        assert all(sock.fileno() == -1 for sock in accepted_sockets)
        assert not listener._server.server_state.connections
        assert not listener._server.server_state.tasks
        assert all(task.done() for task in lifecycle_tasks)
        await listener.close()  # same completed cleanup; no second lifetime
        assert len(lifecycle_tasks) == 1
    finally:
        release.set()
        if writer is not None:
            writer.transport.abort()
            await asyncio.gather(writer.wait_closed(), return_exceptions=True)
        # Test-owned release makes even the old faulty listener finite on RED.
        await asyncio.wait({starting, *(lifecycle_tasks)}, timeout=2)
        if closing_call is not None:
            await asyncio.gather(closing_call, return_exceptions=True)
        await asyncio.gather(listener.close(), return_exceptions=True)
        pending = {task for task in lifecycle_tasks if not task.done()}
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=2)


@pytest.mark.parametrize("resistant", ["lifespan", "server"])
async def test_broker_listener_resistant_shutdown_is_bounded_and_honestly_incomplete(
    monkeypatch, resistant
):
    listener = broker_lifecycle_listener()
    app = listener._app.app
    original = app.router.lifespan_context
    entered, closing, release, exited = (asyncio.Event() for _ in range(4))
    lifecycle_tasks = []

    async def resist():
        closing.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue  # controlled application resistance, released by finally

    @asynccontextmanager
    async def tracked(application):
        lifecycle_tasks.append(asyncio.current_task())
        async with original(application):
            entered.set()
            yield
            if resistant == "lifespan":
                await resist()
        exited.set()

    monkeypatch.setattr(app.router, "lifespan_context", tracked)
    await listener.start()
    actual_shutdown = listener._server.shutdown

    async def shutdown(*args, **kwargs):
        await actual_shutdown(*args, **kwargs)
        if resistant == "server":
            await resist()

    monkeypatch.setattr(listener._server, "shutdown", shutdown)
    call = asyncio.create_task(listener.close())
    started = time.monotonic()
    try:
        async with asyncio.timeout(3):
            await closing.wait()
        done, _ = await asyncio.wait({call}, timeout=6.5)
        assert call in done, "shutdown exceeded its original absolute six-second bound"
        with pytest.raises(TimeoutError, match="cleanup incomplete"):
            await call
        assert time.monotonic() - started < 6.5
        assert listener._socket.fileno() == -1
        assert not listener._server.server_state.connections
        assert not listener._server.server_state.tasks
        assert listener.cleanup_complete is False
        assert not listener.task.done() or not exited.is_set()
        # Repeated close must observe the original failed cleanup, not reset it.
        again = time.monotonic()
        with pytest.raises(TimeoutError, match="cleanup incomplete"):
            await listener.close()
        assert time.monotonic() - again < 0.2
    finally:
        release.set()
        await asyncio.wait({call, listener.task, *lifecycle_tasks}, timeout=2)
        await asyncio.gather(call, return_exceptions=True)
    assert listener.task.done() and exited.is_set()


@pytest.mark.parametrize("failure", ["entry", "shutdown"])
async def test_broker_listener_stage_error_preserves_error_and_attempts_all_resources(
    monkeypatch, failure
):
    listener = broker_lifecycle_listener()
    app = listener._app.app
    original = app.router.lifespan_context
    original_error = RuntimeError("synthetic listener stage failed")
    exited = asyncio.Event()
    lifecycle_tasks = []
    accepted_sockets = []
    writer = None

    @asynccontextmanager
    async def tracked(application):
        lifecycle_tasks.append(asyncio.current_task())
        try:
            async with original(application):
                if failure == "entry":
                    raise original_error
                yield
        finally:
            assert asyncio.current_task() is lifecycle_tasks[0]
            exited.set()

    monkeypatch.setattr(app.router, "lifespan_context", tracked)
    try:
        # Actual AnyIO startup wraps an entry failure in its task-group error.
        # Preserve that real group and the exact original leaf, never flatten it.
        error_type = BaseExceptionGroup if failure == "entry" else RuntimeError
        with pytest.raises(error_type) as caught:
            await listener.start()
            reader, writer = await asyncio.open_connection("127.0.0.1", listener.port)
            writer.write(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            assert b"401" in await reader.readuntil(b"\r\n\r\n")
            accepted_sockets = [
                connection.transport.get_extra_info("socket")
                for connection in listener._server.server_state.connections
            ]
            assert accepted_sockets

            async def failed_shutdown(*args, **kwargs):
                raise original_error  # earlier stage fails BEFORE transport teardown

            monkeypatch.setattr(listener._server, "shutdown", failed_shutdown)
            await listener.close()
        if failure == "entry":
            assert caught.value.exceptions == (original_error,)
        else:
            assert caught.value is original_error
        assert exited.is_set() and listener.task.done()
        assert all(task.done() for task in lifecycle_tasks)
        assert listener._socket.fileno() == -1
        assert all(sock.fileno() == -1 for sock in accepted_sockets)
        assert not listener._server.server_state.connections
        assert not listener._server.server_state.tasks
        assert listener.cleanup_complete is False  # error is never reported as success
    finally:
        if writer is not None:
            writer.transport.abort()
            await asyncio.gather(writer.wait_closed(), return_exceptions=True)
        await asyncio.gather(listener.close(), return_exceptions=True)
