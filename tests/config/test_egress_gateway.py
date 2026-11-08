"""bu-dhloi1.15 / RFC 0038 S7: the shared egress gateway refuses everything undeclared.

Tunnels run through real sockets on loopback. Name resolution and the upstream dial are
injected (the gateway itself refuses loopback), so each test fixes what the name
"resolves" to and observes what the gateway does with it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct

import pytest

from butlers.egress import gateway
from butlers.egress.gateway import (
    AUDIT_LOGGER,
    HEALTH_PORT,
    Refused,
    Route,
    admissible_address,
    client_hello_sni,
    handle_tunnel,
    parse_connect,
    routes_from_rendered,
    system_resolver,
)
from butlers.egress.profiles import RENDERED_PATH, load_profiles

pytestmark = pytest.mark.unit

_PUBLIC = "93.184.216.34"
_ROUTE = Route(
    workload="connector-gmail",
    port=3115,
    destinations=frozenset({("gmail.googleapis.com", 443), ("oauth2.googleapis.com", 443)}),
)


def _hello(sni: str | None) -> bytes:
    """A minimal TLS 1.2-framed ClientHello record, with or without SNI."""
    extensions = b""
    if sni is not None:
        name = sni.encode("ascii")
        server_name_list = b"\x00" + struct.pack("!H", len(name)) + name
        data = struct.pack("!H", len(server_name_list)) + server_name_list
        extensions = struct.pack("!HH", 0, len(data)) + data
    hello = (
        b"\x03\x03" + b"\x00" * 32 + b"\x00"  # version, random, empty session id
        + struct.pack("!H", 2) + b"\x13\x01"  # one cipher suite
        + b"\x01\x00"  # null compression
        + struct.pack("!H", len(extensions)) + extensions
    )  # fmt: skip
    handshake = b"\x01" + len(hello).to_bytes(3, "big") + hello
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


def _connect(authority: str, *headers: str) -> bytes:
    lines = [f"CONNECT {authority} HTTP/1.1", *headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


class _Upstream:
    """Loopback echo server standing in for the declared public host."""

    def __init__(self) -> None:
        self.received = bytearray()
        self.dials: list[tuple[str, int]] = []
        self.server: asyncio.Server | None = None

    async def __aenter__(self):
        async def echo(reader, writer):
            while chunk := await reader.read(65536):
                self.received += chunk
                writer.write(chunk)
                await writer.drain()
            writer.close()

        self.server = await asyncio.start_server(echo, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *_exc):
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def open(self, address: str, port: int):
        self.dials.append((address, port))
        assert self.server is not None
        return await asyncio.open_connection(*self.server.sockets[0].getsockname()[:2])


async def _exchange(request: bytes, *, resolve=None, open_upstream=None, slots=None) -> bytes:
    async def resolver(_host, _port):
        return [_PUBLIC]

    async def refuse_dial(_address, _port):
        raise AssertionError("this test expects no upstream dial")

    async def on_connect(reader, writer):
        await handle_tunnel(
            reader,
            writer,
            _ROUTE,
            resolve=resolve or resolver,
            open_upstream=open_upstream or refuse_dial,
            slots=slots,
        )

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection(*server.sockets[0].getsockname()[:2])
    writer.write(request)
    await writer.drain()
    writer.write_eof()
    response = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    server.close()
    await server.wait_closed()
    return response


def _audits(caplog) -> list[dict]:
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == AUDIT_LOGGER]


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


@pytest.mark.parametrize(
    ("head", "reason"),
    [
        (b"GET http://gmail.googleapis.com/ HTTP/1.1", "method_not_connect"),
        (b"CONNECT 93.184.216.34:443 HTTP/1.1", "ip_literal"),
        (b"CONNECT [2606:4700:4700::1111]:443 HTTP/1.1", "ip_literal"),
        (b"CONNECT www.googleapis.com:443 HTTP/1.1", "undeclared_destination"),
        (b"CONNECT gmail.googleapis.com:80 HTTP/1.1", "undeclared_destination"),
        (b"CONNECT GMAIL.googleapis.com:443 HTTP/1.1", "undeclared_destination"),
        (b"CONNECT gmail.googleapis.com.:443 HTTP/1.1", "undeclared_destination"),
        (
            b"CONNECT gmail.googleapis.com:443 HTTP/1.1\r\nHost: oauth2.googleapis.com:443",
            "host_header_mismatch",
        ),
        (b"CONNECT gmail.googleapis.com HTTP/1.1", "malformed_authority"),
        (b"CONNECT gmail.googleapis.com:0443 HTTP/1.1", "malformed_authority"),
        (b"CONNECT gmail.googleapis.com:443", "malformed_request"),
        (b"CONNECT gmail.googleapis.com:443 HTTP/1.9", "malformed_request"),
    ],
)
def test_a_connect_outside_the_route_is_refused(head, reason):
    with pytest.raises(Refused) as refused:
        parse_connect(head, _ROUTE)
    assert refused.value.reason == reason


def test_an_identity_header_cannot_borrow_another_workloads_hosts():
    # Identity is the listening port; a header naming a workload with wider hosts is inert.
    head = b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nX-Butlers-Workload: butlers-up"
    with pytest.raises(Refused, match="undeclared_destination"):
        parse_connect(head, _ROUTE)


def test_a_matching_connect_is_accepted():
    head = b"CONNECT gmail.googleapis.com:443 HTTP/1.1\r\nHost: gmail.googleapis.com:443"
    assert parse_connect(head, _ROUTE) == ("gmail.googleapis.com", 443)


def test_sni_is_read_from_a_whole_client_hello_and_nothing_else():
    assert client_hello_sni(_hello("gmail.googleapis.com")) == "gmail.googleapis.com"
    assert client_hello_sni(_hello(None)) is None
    assert client_hello_sni(_hello("gmail.googleapis.com")[:20]) is None
    assert client_hello_sni(b"\x17\x03\x03\x00\x01x") is None


async def test_a_declared_tunnel_relays_and_audits_counts_without_content(caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    request = _connect("gmail.googleapis.com:443", "Proxy-Authorization: Bearer tok-SECRET")
    payload = _hello("gmail.googleapis.com") + b"PAYLOAD-SECRET"
    async with _Upstream() as upstream:
        response = await _exchange(request + payload, open_upstream=upstream.open)
    assert response.startswith(b"HTTP/1.1 200 ")
    assert response.endswith(payload)
    assert bytes(upstream.received) == payload
    assert upstream.dials == [(_PUBLIC, 443)]
    (audit,) = _audits(caplog)
    assert audit["verdict"] == "allowed"
    assert (audit["workload"], audit["host"], audit["port"]) == (
        "connector-gmail",
        "gmail.googleapis.com",
        443,
    )
    assert audit["bytes_up"] == len(payload) and audit["bytes_down"] == len(payload)
    assert "SECRET" not in caplog.text


async def test_an_sni_that_disagrees_with_the_connect_host_is_never_forwarded(caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    request = _connect("gmail.googleapis.com:443") + _hello("evil.example.org") + b"data"
    async with _Upstream() as upstream:
        response = await _exchange(request, open_upstream=upstream.open)
    assert response == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    assert upstream.received == b""
    assert _audits(caplog)[0]["reason"] == "sni_mismatch"


async def test_a_tunnel_without_tls_is_refused(caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    request = _connect("gmail.googleapis.com:443") + b"GET / HTTP/1.1\r\n\r\n"
    async with _Upstream() as upstream:
        await _exchange(request, open_upstream=upstream.open)
    assert upstream.received == b""
    assert _audits(caplog)[0]["reason"] == "not_tls"


@pytest.mark.parametrize(
    "answers",
    [
        pytest.param(["10.0.0.5"], id="private"),
        pytest.param(["169.254.169.254"], id="metadata"),
        pytest.param(["100.100.100.100"], id="tailnet"),
        pytest.param([_PUBLIC, "10.0.0.5"], id="rebinding-mixed-answer"),
        pytest.param([], id="no-answer"),
    ],
)
async def test_a_name_resolving_anywhere_non_public_is_refused_before_dialling(answers, caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)

    async def resolve(_host, _port):
        return answers

    async with _Upstream() as upstream:
        response = await _exchange(
            _connect("gmail.googleapis.com:443") + _hello("gmail.googleapis.com"),
            resolve=resolve,
            open_upstream=upstream.open,
        )
    assert response.startswith(b"HTTP/1.1 403 ")
    assert b"X-Egress-Refusal: non_public_address" in response
    assert upstream.dials == []
    assert _audits(caplog)[0]["reason"] == "non_public_address"


async def test_resolution_and_upstream_outages_deny_with_a_diagnostic(caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)

    async def no_dns(_host, _port):
        raise OSError("resolver down")

    async def no_route(_address, _port):
        raise ConnectionRefusedError

    request = _connect("gmail.googleapis.com:443")
    dns = await _exchange(request, resolve=no_dns)
    upstream = await _exchange(request, open_upstream=no_route)
    assert dns.startswith(b"HTTP/1.1 502 ") and b"dns_failed" in dns
    assert upstream.startswith(b"HTTP/1.1 502 ") and b"upstream_unreachable" in upstream
    assert [a["reason"] for a in _audits(caplog)] == ["dns_failed", "upstream_unreachable"]


async def test_an_undeclared_host_is_refused_with_a_content_free_cause(caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    response = await _exchange(_connect("api.anthropic.com:443"))
    assert response.startswith(b"HTTP/1.1 403 ")
    assert b"X-Egress-Refusal: undeclared_destination" in response
    assert _audits(caplog)[0]["verdict"] == "refused"


async def test_the_system_resolver_asks_for_the_absolute_name_over_ipv4(monkeypatch):
    calls = []

    async def fake_getaddrinfo(host, port, **kwargs):
        calls.append((host, port, kwargs.get("family")))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_PUBLIC, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    assert await system_resolver("gmail.googleapis.com", 443) == [_PUBLIC]
    assert calls == [("gmail.googleapis.com.", 443, socket.AF_INET)]


async def _silent_client_tunnel(stream, caplog, monkeypatch) -> tuple[bytes, dict]:
    """A tunnel whose client sends only the ClientHello and then stays silent."""
    monkeypatch.setattr(gateway, "_IDLE_TIMEOUT_S", 0.4)
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)

    async def upstream_handler(reader, writer):
        await reader.readexactly(len(_hello("gmail.googleapis.com")))
        await stream(reader, writer)

    server = await asyncio.start_server(upstream_handler, "127.0.0.1", 0)
    address = server.sockets[0].getsockname()[:2]

    async def open_upstream(_address, _port):
        return await asyncio.open_connection(*address)

    gateway_server = await asyncio.start_server(
        lambda r, w: handle_tunnel(r, w, _ROUTE, resolve=_public, open_upstream=open_upstream),
        "127.0.0.1",
        0,
    )
    reader, writer = await asyncio.open_connection(*gateway_server.sockets[0].getsockname()[:2])
    writer.write(_connect("gmail.googleapis.com:443") + _hello("gmail.googleapis.com"))
    await writer.drain()
    received = await asyncio.wait_for(reader.read(), 10)
    writer.close()
    for s in (gateway_server, server):
        s.close()
        await s.wait_closed()
    (audit,) = _audits(caplog)
    return received, audit


async def _public(_host, _port):
    return [_PUBLIC]


async def test_a_silent_client_does_not_cut_a_live_download(caplog, monkeypatch):
    # Each chunk arrives inside the idle window, but the whole stream outlasts it. Like
    # a front end that treats the client's FIN as an abort, the upstream stops early if
    # the gateway half-closes it.
    async def stream(reader, writer):
        client_eof = asyncio.ensure_future(reader.read())
        for index in range(6):
            if client_eof.done():
                break
            writer.write(f"chunk-{index};".encode())
            await writer.drain()
            await asyncio.sleep(0.15)
        client_eof.cancel()
        writer.close()

    received, audit = await _silent_client_tunnel(stream, caplog, monkeypatch)
    assert received.endswith(b"".join(f"chunk-{i};".encode() for i in range(6)))
    assert audit["verdict"] == "allowed"


async def test_a_tunnel_idle_in_both_directions_is_closed(caplog, monkeypatch):
    async def stream(reader, writer):
        await reader.read()  # Silent until the gateway closes the tunnel.
        writer.close()

    received, audit = await _silent_client_tunnel(stream, caplog, monkeypatch)
    assert received == b"HTTP/1.1 200 Connection Established\r\n\r\n"
    assert (audit["verdict"], audit["reason"]) == ("allowed", "idle_timeout")


async def test_a_workload_at_its_tunnel_limit_is_refused_busy(caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER)
    slots = asyncio.Semaphore(1)
    await slots.acquire()
    response = await _exchange(_connect("gmail.googleapis.com:443"), slots=slots)
    assert response.startswith(b"HTTP/1.1 503 ") and b"busy" in response
    assert _audits(caplog)[0]["reason"] == "busy"
