"""Fixed-destination encrypted egress and non-recursive DNS for session hosts.

The sandbox has no ordinary network attachment. This gateway is its only
reachable peer. TLS is passed through: SNI selects only configured provider
hosts, whose certificates are still checked by the runtime. A separate fixed
TCP route reaches only the broker session port. No CONNECT/HTTP proxy, caller
URL, dynamic target, certificate impersonation, or private-content logging.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
from dataclasses import dataclass

from butlers.session_host.auth import AUTH_RELAY_SECONDS
from butlers.session_host.launch import Refused

_HELLO_LIMIT = 16384
_RELAY_LIMIT = 32 * 1024 * 1024


def _sni(body: bytes) -> str:
    """Parse a complete native TLS ClientHello with strict length accounting."""
    try:
        offset = 34  # legacy version and random
        offset += 1 + body[offset]
        size = int.from_bytes(body[offset : offset + 2], "big")
        if not size or size % 2 or size > 512:
            raise ValueError
        offset += 2 + size
        offset += 1 + body[offset]
        extensions_size = int.from_bytes(body[offset : offset + 2], "big")
        offset += 2
        if len(body) != offset + extensions_size:
            raise ValueError
        seen, host = set(), None
        while offset < len(body):
            if offset + 4 > len(body):
                raise ValueError
            kind, size = struct.unpack("!HH", body[offset : offset + 4])
            offset += 4
            end = offset + size
            if kind in seen or end > len(body):
                raise ValueError
            seen.add(kind)
            if kind == 0:
                value = body[offset:end]
                if (
                    len(value) < 6
                    or int.from_bytes(value[:2], "big") != len(value) - 2
                    or value[2] != 0
                    or int.from_bytes(value[3:5], "big") != len(value) - 5
                ):
                    raise ValueError
                host = value[5:].decode("ascii")
            offset = end
        if host is None:
            raise ValueError
        return host
    except (ValueError, IndexError, UnicodeError, struct.error):
        raise Refused("egress_refused") from None


async def read_client_hello(reader, allowed: frozenset[str]) -> tuple[str, bytes]:
    """Bound aggregate fragmented TLS records before allocating or forwarding."""
    wire, handshake = bytearray(), bytearray()
    try:
        async with asyncio.timeout(3):
            while True:
                header = await reader.readexactly(5)
                size = int.from_bytes(header[3:], "big")
                if (
                    header[0] != 22
                    or header[1:3] not in (b"\x03\x01", b"\x03\x02", b"\x03\x03")
                    or not size
                    or len(wire) + 5 + size > _HELLO_LIMIT
                ):
                    raise ValueError
                content = await reader.readexactly(size)
                wire.extend(header + content)
                handshake.extend(content)
                if len(handshake) < 4:
                    continue
                if handshake[0] != 1:
                    raise ValueError
                length = int.from_bytes(handshake[1:4], "big")
                if length > _HELLO_LIMIT - 4:
                    raise ValueError
                if len(handshake) < length + 4:
                    continue
                if len(handshake) != length + 4:
                    raise ValueError
                name = _sni(bytes(handshake[4:]))
                if name not in allowed:
                    raise ValueError
                return name, bytes(wire)
    except (ValueError, TimeoutError, asyncio.IncompleteReadError):
        raise Refused("egress_refused") from None


class FixedDNS(asyncio.DatagramProtocol):
    """Only named broker/provider queries get the gateway's own internal IP.

    No recursion or forwarding, including unknown subdomains. AAAA queries get
    no data; this deployment uses the explicit private IPv4 gateway address.
    """

    def __init__(self, address: str, names: frozenset[str]):
        self._address = ipaddress.IPv4Address(address).packed
        self._names = names
        self._transport = None

    def connection_made(self, transport):
        self._transport = transport

    def answer(self, data: bytes) -> bytes | None:
        try:
            if not 17 <= len(data) <= 512:
                return None
            identity, flags, questions, answers, authorities, extra = struct.unpack(
                "!6H", data[:12]
            )
            if flags & 0xF800 or (questions, answers, authorities, extra) != (1, 0, 0, 0):
                return None
            offset, labels = 12, []
            while True:
                size = data[offset]
                offset += 1
                if not size:
                    break
                if size > 63 or offset + size > len(data):
                    return None
                labels.append(data[offset : offset + size].decode("ascii"))
                offset += size
            if offset + 4 != len(data):
                return None
            kind, family = struct.unpack("!HH", data[offset:])
            name = ".".join(labels).lower()
            known = name in self._names and family == 1
            answer = known and kind == 1
            header = struct.pack(
                "!6H",
                identity,
                0x8000 | (flags & 0x100) | (0 if known else 3),
                1,
                int(answer),
                0,
                0,
            )
            result = header + data[12:]
            if answer:
                result += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + self._address
            return result
        except (ValueError, IndexError, UnicodeError, struct.error):
            return None

    def datagram_received(self, data, peer):
        answer = self.answer(data)
        if answer is not None:
            self._transport.sendto(answer, peer)


async def _discard(writer):
    if writer is None:
        return
    writer.close()
    try:
        async with asyncio.timeout(2):
            await writer.wait_closed()
    except (OSError, TimeoutError):
        pass


class EgressGateway:
    """Trusted deployment routes; launch requests cannot choose destinations."""

    def __init__(self, *, providers: frozenset[str], broker_host: str, host_target: str):
        if (
            not providers
            or len(providers) > 16
            or any(
                type(name) is not str
                or not name
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in name)
                for name in providers
            )
        ):
            raise Refused("invalid_egress_config")
        for name in (broker_host, host_target):
            if (
                type(name) is not str
                or not name
                or len(name) > 253
                or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in name)
            ):
                raise Refused("invalid_egress_config")
        self._providers = providers
        self._broker, self._host = broker_host, host_target
        self._tasks: set[asyncio.Task] = set()

    async def _public_connection(self, name):
        async with asyncio.timeout(5):
            addresses = await asyncio.get_running_loop().getaddrinfo(
                name, 443, type=socket.SOCK_STREAM
            )
            if not addresses or len(addresses) > 16:
                raise Refused("egress_refused")
            # The provider name is configured, but resolver rebinding to a
            # loopback/private/link-local target must still fail closed.
            if any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
                raise Refused("egress_refused")
            return await asyncio.open_connection(addresses[0][4][0], 443)

    async def _relay(self, reader, writer, *, kind):
        task = asyncio.current_task()
        upstream = None
        copies = []
        try:
            if len(self._tasks) >= 64:
                raise Refused("egress_busy")
            self._tasks.add(task)
            # CLI login retains its existing 900s provider lifetime and 32s
            # containment budget. Only the authenticated control relay needs
            # that longer aggregate bound; model/broker traffic stays 340s.
            async with asyncio.timeout(AUTH_RELAY_SECONDS if kind == "control" else 340):
                if kind == "model":
                    name, initial = await read_client_hello(reader, self._providers)
                    remote, upstream = await self._public_connection(name)
                    upstream.write(initial)
                    await upstream.drain()
                else:
                    host, port = (self._broker, 41199) if kind == "broker" else (self._host, 41299)
                    async with asyncio.timeout(5):
                        remote, upstream = await asyncio.open_connection(host, port)
                used = 0

                async def copy(source, destination):
                    nonlocal used
                    while chunk := await source.read(16384):
                        used += len(chunk)
                        if used > _RELAY_LIMIT:
                            raise Refused("egress_size")
                        destination.write(chunk)
                        await destination.drain()

                copies = [
                    asyncio.create_task(copy(reader, upstream)),
                    asyncio.create_task(copy(remote, writer)),
                ]
                done, _ = await asyncio.wait(copies, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    finished.result()
        except (Refused, OSError, TimeoutError):
            pass  # Never log encrypted payloads, upstream details, or requests.
        finally:
            for child in copies:
                child.cancel()
            if copies:
                await asyncio.gather(*copies, return_exceptions=True)
            await _discard(upstream)
            await _discard(writer)
            self._tasks.discard(task)

    async def model(self, reader, writer):
        await self._relay(reader, writer, kind="model")

    async def broker(self, reader, writer):
        await self._relay(reader, writer, kind="broker")

    async def control(self, reader, writer):
        # Opaque mTLS forwarding: dashboard certificates cannot authenticate as
        # runner, and this gateway holds no TLS identity or model credential.
        await self._relay(reader, writer, kind="control")

    async def close(self):
        """Cancel each owned relay once and bound settlement of its sockets."""
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=5)
            if pending:
                raise Refused("egress_cleanup_incomplete")


@dataclass(frozen=True)
class GatewayConfig:
    enabled: bool = False
    internal_address: str | None = None
    control_address: str | None = None
    dns_address: str | None = None
    providers: frozenset[str] = frozenset()
    broker_host: str | None = None
    host_target: str | None = None


@dataclass
class RunningGateway:
    servers: tuple[asyncio.Server, ...]
    dns: asyncio.DatagramTransport
    gateway: EgressGateway

    async def close(self):
        # Stop accepting before settling every already admitted connection.
        for server in self.servers:
            server.close()
        self.dns.close()
        for server in self.servers:
            await server.wait_closed()
        await self.gateway.close()


async def start_gateway(config: GatewayConfig) -> RunningGateway | None:
    """OFF has no DNS, sockets or workers. Enabled routes are deployment-owned.

    Compose binds the control relay to a separate interface; Kubernetes uses
    port-specific NetworkPolicies on its single interface. Both deployments
    deny all other outbound routes. There is no general-purpose proxy.
    """
    if type(config) is not GatewayConfig or type(config.enabled) is not bool:
        raise Refused("invalid_egress_config")
    if not config.enabled:
        return None
    try:
        internal = ipaddress.IPv4Address(config.internal_address)
        control = ipaddress.IPv4Address(config.control_address)
        dns_address = ipaddress.IPv4Address(config.dns_address)
        if internal.is_multicast or control.is_multicast:
            raise ValueError
        if dns_address.is_unspecified:
            raise ValueError
    except (ValueError, TypeError, ipaddress.AddressValueError):
        raise Refused("invalid_egress_config") from None
    gateway = EgressGateway(
        providers=config.providers, broker_host=config.broker_host, host_target=config.host_target
    )
    servers, dns = [], None
    try:
        for handler, address, port in (
            (gateway.model, str(internal), 443),
            (gateway.broker, str(internal), 41199),
            (gateway.control, str(control), 41300),
        ):
            servers.append(await asyncio.start_server(handler, address, port, limit=16389))
        dns, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: FixedDNS(str(dns_address), config.providers | {"broker.session.internal"}),
            local_addr=(str(internal), 53),
        )
        return RunningGateway(tuple(servers), dns, gateway)
    except BaseException:
        for server in servers:
            server.close()
        if dns is not None:
            dns.close()
        for server in servers:
            await server.wait_closed()
        await gateway.close()
        raise
