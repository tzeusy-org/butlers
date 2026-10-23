"""Native TLS ClientHello/DNS parser controls, not an executed NetworkPolicy."""

import asyncio
import ssl
import struct

import pytest

from butlers.session_host.egress import FixedDNS, read_client_hello
from butlers.session_host.launch import Refused

pytestmark = pytest.mark.unit


def hello(host):
    outgoing = ssl.MemoryBIO()
    client = ssl.create_default_context().wrap_bio(
        ssl.MemoryBIO(), outgoing, server_side=False, server_hostname=host
    )
    with pytest.raises(ssl.SSLWantReadError):
        client.do_handshake()
    return outgoing.read()


async def test_sni_gate_observes_real_hello_and_refuses_wrong_truncated_or_oversized_name():
    wire = hello("api.openai.com")
    reader = asyncio.StreamReader()

    # Fragmented transport delivery is distinct from fragmented TLS records.
    async def deliver():
        for start in range(0, len(wire), 17):
            reader.feed_data(wire[start : start + 17])
            await asyncio.sleep(0)

    sender = asyncio.create_task(deliver())
    try:
        name, initial = await read_client_hello(reader, frozenset({"api.openai.com"}))
        assert name == "api.openai.com" and initial == wire
    finally:
        await sender
    # A handshake may also span multiple TLS records, not just TCP chunks.
    payload = wire[5:]
    fragmented = b"".join(
        b"\x16\x03\x03" + len(payload[i : i + 37]).to_bytes(2, "big") + payload[i : i + 37]
        for i in range(0, len(payload), 37)
    )
    reader = asyncio.StreamReader()
    reader.feed_data(fragmented)
    assert await read_client_hello(reader, frozenset({"api.openai.com"})) == (
        "api.openai.com",
        fragmented,
    )
    for wire in (hello("database.internal"), b"\x16\x03\x01\xff\xff", hello("api.openai.com")[:-1]):
        reader = asyncio.StreamReader()
        reader.feed_data(wire)
        reader.feed_eof()
        with pytest.raises(Refused):
            await read_client_hello(reader, frozenset({"api.openai.com"}))


def test_dns_never_forwards_arbitrary_queries_or_publishes_domain_addresses():
    dns = FixedDNS("172.30.253.2", frozenset({"api.openai.com", "broker.session.internal"}))

    def query(name, kind=1):
        labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"
        return struct.pack("!HHHHHH", 123, 0x100, 1, 0, 0, 0) + labels + struct.pack("!HH", kind, 1)

    known = dns.answer(query("api.openai.com"))
    assert known[-4:] == bytes([172, 30, 253, 2])
    assert struct.unpack("!HHHHHH", known[:12])[3] == 1
    for name in ("database.internal", "secrets.example", "other.api.openai.com"):
        refused = dns.answer(query(name))
        assert struct.unpack("!HHHHHH", refused[:12])[1] & 15 == 3
        assert struct.unpack("!HHHHHH", refused[:12])[3] == 0
    assert struct.unpack("!HHHHHH", dns.answer(query("api.openai.com", 28))[:12])[3] == 0
    assert dns.answer(b"malformed") is None


async def test_fixed_gateway_relays_only_after_sni_admission_and_settles_real_connections(
    monkeypatch,
):
    from butlers.session_host.egress import EgressGateway

    received = asyncio.Queue()

    async def upstream(reader, writer):
        try:
            payload = await reader.read(16384)
            await received.put(payload)
            writer.write(b"fixed upstream reply")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    remote = await asyncio.start_server(upstream, "127.0.0.1", 0)
    gateway = EgressGateway(
        providers=frozenset({"api.openai.com"}), broker_host="control", host_target="host"
    )
    contacted = []

    async def connection(name):
        contacted.append(name)
        return await asyncio.open_connection("127.0.0.1", remote.sockets[0].getsockname()[1])

    monkeypatch.setattr(gateway, "_public_connection", connection)
    server = await asyncio.start_server(gateway.model, "127.0.0.1", 0)
    try:
        async with asyncio.timeout(5):
            for name, accepted in (("database.internal", False), ("api.openai.com", True)):
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", server.sockets[0].getsockname()[1]
                )
                wire = hello(name)
                writer.write(wire)
                await writer.drain()
                result = await reader.read()
                writer.close()
                await writer.wait_closed()
                if accepted:
                    assert result == b"fixed upstream reply"
                    assert await received.get() == wire
                else:
                    assert result == b"" and contacted == [] and received.empty()
            await gateway.close()
            assert contacted == ["api.openai.com"] and not gateway._tasks
    finally:
        server.close()
        remote.close()
        await server.wait_closed()
        await remote.wait_closed()
        await gateway.close()


async def test_gateway_startup_disabled_and_partial_bind_failure_release_owned_listeners(
    monkeypatch,
):
    import butlers.session_host.egress as egress

    async def forbidden(*args, **kwargs):
        pytest.fail("OFF constructed a listener")

    real = egress.asyncio.start_server
    monkeypatch.setattr(egress.asyncio, "start_server", forbidden)
    assert await egress.start_gateway(egress.GatewayConfig()) is None
    opened = []

    async def failing(handler, address, port, **kwargs):
        if opened:
            raise OSError("fixture bind refusal")
        server = await real(handler, "127.0.0.1", 0, **kwargs)
        opened.append(server)
        return server

    monkeypatch.setattr(egress.asyncio, "start_server", failing)
    with pytest.raises(OSError, match="fixture bind refusal"):
        await egress.start_gateway(
            egress.GatewayConfig(
                enabled=True,
                internal_address="127.0.0.1",
                control_address="127.0.0.2",
                dns_address="127.0.0.1",
                providers=frozenset({"api.openai.com"}),
                broker_host="control",
                host_target="host",
            )
        )
    assert len(opened) == 1 and not opened[0].is_serving() and opened[0].sockets == ()
