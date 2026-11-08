"""Shared egress gateway: CONNECT-only, per-port workload identity (RFC 0038 S7).

Each profiled workload with external hosts reaches the gateway on its own
``gateway_port``; NetworkPolicy admits only that workload's pods to that port, so
the listening port is the workload identity. No request header can claim another
identity. For every tunnel the gateway:

- accepts only ``CONNECT host:port`` for an exact (host, port) in that workload's
  profile, and a ``Host`` header, if sent, that agrees with it;
- refuses IP literals, resolves the absolute name itself (IPv4 only, matching its
  IPv4-only egress policy) and refuses it when any address is not globally routable
  unicast (private, loopback, link-local/metadata, CGNAT/tailnet, multicast,
  reserved), then connects to an address it validated, so a rebinding answer cannot
  redirect the tunnel;
- reads the client's TLS ClientHello and refuses unless its SNI equals the
  CONNECT host (this binds the TLS name, not the tenant: a shared front end can
  still route on the HTTP authority inside the encrypted session);
- closes a tunnel after ``_IDLE_TIMEOUT_S`` with no bytes in either direction;
- writes one content-free audit line per tunnel: workload, host, port, verdict,
  reason, byte counts and duration. Never payload, headers or tokens.

There is no plain-HTTP proxying, no runtime widening and no fallback route: a
refusal or an outage denies the request with a diagnostic status, and the caller's
own bounded retry applies.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import socket
import struct
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from butlers.egress.profiles import RENDERED_PATH, SCHEMA

AUDIT_LOGGER = "butlers.egress.audit"
HEALTH_PORT = 3100
_HEAD_LIMIT = 8192
_HEAD_TIMEOUT_S = 10.0
_CONNECT_TIMEOUT_S = 10.0
_HELLO_TIMEOUT_S = 10.0
_IDLE_TIMEOUT_S = 300.0
_MAX_TUNNELS_PER_WORKLOAD = 128
_TLS_HANDSHAKE = 0x16
_CLIENT_HELLO = 0x01
_SNI_EXTENSION = 0x0000

audit_log = logging.getLogger(AUDIT_LOGGER)
log = logging.getLogger(__name__)

Resolver = Callable[[str, int], Awaitable[list[str]]]
Opener = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class Refused(Exception):
    """A tunnel the gateway will not open; ``reason`` is a closed, content-free cause."""

    def __init__(self, reason: str, status: int = 403) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


@dataclass(frozen=True, slots=True)
class Route:
    workload: str
    port: int
    destinations: frozenset[tuple[str, int]]


def routes_from_rendered(document: Mapping[str, Any]) -> dict[int, Route]:
    """Gateway routes, keyed by listening port, from the rendered profile JSON."""
    if document.get("schema") != SCHEMA:
        raise ValueError(f"rendered profiles must be {SCHEMA}")
    routes: dict[int, Route] = {}
    for name, profile in document["workloads"].items():
        port = profile.get("gateway_port")
        if profile.get("held") or port is None:
            continue
        destinations = frozenset((e["host"], int(e["port"])) for e in profile["external"])
        if port in routes or port == HEALTH_PORT or not destinations:
            raise ValueError(f"gateway port {port} for {name} is invalid or reused")
        routes[port] = Route(workload=name, port=port, destinations=destinations)
    return routes


def admissible_address(address: str) -> bool:
    """True only for a globally routable unicast IPv4 address.

    IPv6 is refused outright: the gateway's egress policy is IPv4-only, and
    ``is_global`` admits IPv6 forms that embed or reach private IPv4 (NAT64
    ``64:ff9b::/96``, IPv4-compatible ``::/96``, deprecated site-local ``fec0::/10``).
    An IPv4-mapped address is judged as the IPv4 address it maps.
    """
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is None:
            return False
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _is_ip_literal(host: str) -> bool:
    if host.startswith("["):
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def parse_connect(head: bytes, route: Route) -> tuple[str, int]:
    """Validate a CONNECT request head against the route; return (host, port)."""
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError as exc:
        raise Refused("malformed_request", 400) from exc
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or parts[2] not in ("HTTP/1.0", "HTTP/1.1"):
        raise Refused("malformed_request", 400)
    method, authority, _ = parts
    if method != "CONNECT":
        raise Refused("method_not_connect", 405)
    host, sep, port_text = authority.rpartition(":")
    if _is_ip_literal(host or authority):
        raise Refused("ip_literal")
    if not sep or not (port_text.isascii() and port_text.isdigit()) or port_text.startswith("0"):
        raise Refused("malformed_authority", 400)
    host_header = [
        line.split(":", 1)[1].strip() for line in lines[1:] if line.lower().startswith("host:")
    ]
    if len(host_header) > 1 or (host_header and host_header[0].lower() != authority.lower()):
        raise Refused("host_header_mismatch")
    if (host.lower(), int(port_text)) not in route.destinations or host != host.lower():
        raise Refused("undeclared_destination")
    return host, int(port_text)


def client_hello_sni(record: bytes) -> str | None:
    """SNI host_name from one TLS record carrying a whole ClientHello, else None."""
    try:
        if record[0] != _TLS_HANDSHAKE or record[5] != _CLIENT_HELLO:
            return None
        (record_length,) = struct.unpack("!H", record[3:5])
        body = record[5 : 5 + record_length]
        hello_length = int.from_bytes(body[1:4], "big")
        hello = body[4 : 4 + hello_length]
        if len(hello) != hello_length:
            return None  # A ClientHello split across records is refused, not guessed.
        offset = 2 + 32
        offset += 1 + hello[offset]
        (suites,) = struct.unpack("!H", hello[offset : offset + 2])
        offset += 2 + suites
        offset += 1 + hello[offset]
        (ext_total,) = struct.unpack("!H", hello[offset : offset + 2])
        offset += 2
        end = offset + ext_total
        while offset + 4 <= end:
            ext_type, ext_len = struct.unpack("!HH", hello[offset : offset + 4])
            offset += 4
            if ext_type == _SNI_EXTENSION:
                data = hello[offset : offset + ext_len]
                cursor = 2
                while cursor + 3 <= len(data):
                    name_type = data[cursor]
                    (name_len,) = struct.unpack("!H", data[cursor + 1 : cursor + 3])
                    name = data[cursor + 3 : cursor + 3 + name_len]
                    if name_type == 0:
                        return name.decode("ascii").lower()
                    cursor += 3 + name_len
                return None
            offset += ext_len
    except (IndexError, struct.error, UnicodeDecodeError):
        return None
    return None


async def system_resolver(host: str, port: int) -> list[str]:
    """IPv4 addresses for the absolute name ``host.``.

    The trailing dot stops the pod's resolv.conf search domains (``ndots:5``) from
    turning a declared name into a different, undeclared one.
    """
    infos = await asyncio.get_running_loop().getaddrinfo(
        host + ".", port, family=socket.AF_INET, type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )  # fmt: skip
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


async def open_tcp(address: str, port: int):
    return await asyncio.open_connection(address, port)


async def _read_hello(reader: asyncio.StreamReader) -> bytes:
    header = await reader.readexactly(5)
    (length,) = struct.unpack("!H", header[3:5])
    if header[0] != _TLS_HANDSHAKE or length > 16384:
        raise Refused("not_tls")
    return header + await reader.readexactly(length)


async def _pipe(
    source: asyncio.StreamReader,
    sink: asyncio.StreamWriter,
    counter: list[int],
    last_activity: list[float],
) -> None:
    """Copy one direction; on the source's EOF, half-close the sink."""
    try:
        while chunk := await source.read(65536):
            counter[0] += len(chunk)
            last_activity[0] = time.monotonic()
            sink.write(chunk)
            await sink.drain()
    except ConnectionError:
        return
    if sink.can_write_eof():
        try:
            sink.write_eof()
        except (OSError, RuntimeError):
            pass


async def _relay(
    client: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter],
    up: list[int],
    down: list[int],
) -> str:
    """Relay both directions; the idle clock is shared, so a silent client does not
    cut a live download. Returns the closed cause."""
    last_activity = [time.monotonic()]
    pipes = asyncio.gather(
        _pipe(client[0], upstream[1], up, last_activity),
        _pipe(upstream[0], client[1], down, last_activity),
    )
    while not pipes.done():
        remaining = last_activity[0] + _IDLE_TIMEOUT_S - time.monotonic()
        if remaining <= 0:
            pipes.cancel()
            await asyncio.wait([pipes])
            try:
                pipes.exception()  # retrieve it, so the cancelled gather is not logged
            except asyncio.CancelledError:
                pass
            return "idle_timeout"
        await asyncio.wait([pipes], timeout=remaining)
    pipes.result()
    return "closed"


def _audit(route: Route, host: str | None, port: int | None, verdict: str, reason: str,
           up: int, down: int, started: float) -> None:  # fmt: skip
    audit_log.info(
        json.dumps(
            {
                "event": "egress",
                "workload": route.workload,
                "host": host,
                "port": port,
                "verdict": verdict,
                "reason": reason,
                "bytes_up": up,
                "bytes_down": down,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
            sort_keys=True,
        )
    )


async def _refuse(writer: asyncio.StreamWriter, refusal: Refused) -> None:
    reasons = {400: "Bad Request", 403: "Forbidden", 405: "Method Not Allowed",
               502: "Bad Gateway", 503: "Service Unavailable"}  # fmt: skip
    writer.write(
        f"HTTP/1.1 {refusal.status} {reasons.get(refusal.status, 'Refused')}\r\n"
        f"X-Egress-Refusal: {refusal.reason}\r\nContent-Length: 0\r\n"
        "Connection: close\r\n\r\n".encode("ascii")
    )
    try:
        await writer.drain()
    except ConnectionError:
        pass


async def handle_tunnel(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    route: Route,
    *,
    resolve: Resolver = system_resolver,
    open_upstream: Opener = open_tcp,
    slots: asyncio.Semaphore | None = None,
) -> None:
    """Serve one client connection on ``route``'s port."""
    started = time.monotonic()
    host: str | None = None
    port: int | None = None
    up, down = [0], [0]
    upstream: asyncio.StreamWriter | None = None
    tunnelled = False
    acquired = False
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT_S)
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError) as exc:
            raise Refused("malformed_request", 400) from exc
        if slots is not None:
            if slots.locked():
                raise Refused("busy", 503)
            await slots.acquire()
            acquired = True
        host, port = parse_connect(head[:-4], route)
        try:
            addresses = await asyncio.wait_for(resolve(host, port), _CONNECT_TIMEOUT_S)
        except (OSError, TimeoutError) as exc:
            raise Refused("dns_failed", 502) from exc
        if not addresses or not all(admissible_address(a) for a in addresses):
            raise Refused("non_public_address")
        upstream_reader = None
        for address in addresses:
            try:
                upstream_reader, upstream = await asyncio.wait_for(
                    open_upstream(address, port), _CONNECT_TIMEOUT_S
                )
                break
            except (OSError, TimeoutError):
                continue
        if upstream is None or upstream_reader is None:
            raise Refused("upstream_unreachable", 502)
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        tunnelled = True
        try:
            hello = await asyncio.wait_for(_read_hello(reader), _HELLO_TIMEOUT_S)
        except (asyncio.IncompleteReadError, TimeoutError) as exc:
            raise Refused("not_tls") from exc
        if client_hello_sni(hello) != host:
            raise Refused("sni_mismatch")
        upstream.write(hello)
        await upstream.drain()
        up[0] += len(hello)
        closed = await _relay((reader, writer), (upstream_reader, upstream), up, down)
        _audit(route, host, port, "allowed", closed, up[0], down[0], started)
    except Refused as refusal:
        if not tunnelled:
            await _refuse(writer, refusal)
        _audit(route, host, port, "refused", refusal.reason, up[0], down[0], started)
    except Exception:  # noqa: BLE001 - one bad tunnel must not stop the gateway
        log.exception("egress tunnel failed for %s", route.workload)
        _audit(route, host, port, "refused", "gateway_error", up[0], down[0], started)
    finally:
        if acquired and slots is not None:
            slots.release()
        for stream in (upstream, writer):
            if stream is not None:
                stream.close()


async def serve(routes: Mapping[int, Route], host: str = "0.0.0.0") -> None:
    servers = []
    for port, route in routes.items():
        slots = asyncio.Semaphore(_MAX_TUNNELS_PER_WORKLOAD)

        async def on_connect(reader, writer, route=route, slots=slots):
            await handle_tunnel(reader, writer, route, slots=slots)

        servers.append(await asyncio.start_server(on_connect, host, port, limit=_HEAD_LIMIT))

    async def health(_reader, writer):
        writer.close()

    servers.append(await asyncio.start_server(health, host, HEALTH_PORT))
    log.info("egress gateway serving %d workload ports", len(routes))
    await asyncio.gather(*(server.serve_forever() for server in servers))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the shared egress gateway.")
    parser.add_argument("--profiles", type=Path, default=RENDERED_PATH)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    routes = routes_from_rendered(json.loads(args.profiles.read_text(encoding="utf-8")))
    asyncio.run(serve(routes, args.host))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
