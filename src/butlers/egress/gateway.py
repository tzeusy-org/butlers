"""Per-workload HTTPS request execution (RFC0038 S7).

The gateway receives allowed HTTP requests transiently. It derives origin Host
and verified TLS identity from one exact profile destination, and refuses every
redirect before exposing response headers. Paths, headers, bodies and exception
text never enter audit logs. There is no CONNECT or opaque upgraded TCP route;
the separate WSS route validates its handshake and bounded RFC6455 frames.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import logging
import socket
import ssl
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h11

from butlers.egress.profiles import RENDERED_PATH, SCHEMA

AUDIT_LOGGER = "butlers.egress.audit"
HEALTH_PORT = 3100
_HEAD_LIMIT = 8192
_HEAD_TIMEOUT_S = 10.0
_CONNECT_TIMEOUT_S = 10.0
_IDLE_TIMEOUT_S = 300.0
_MAX_TUNNELS_PER_WORKLOAD = 128
_UPLOAD_LIMIT = 1024 * 1024 * 1024
_DOWNLOAD_LIMIT = 1024 * 1024 * 1024
_MAX_ACTIVE_REQUESTS = 32
_EXCHANGE_TIMEOUT_S = 3600.0
_PREFIX = b"/_butlers/egress/v1/https/"
_HOP_HEADERS = frozenset(
    {
        b"connection",
        b"keep-alive",
        b"proxy-authenticate",
        b"proxy-authorization",
        b"proxy-connection",
        b"te",
        b"trailer",
        b"transfer-encoding",
        b"upgrade",
        b"expect",
    }
)

audit_log = logging.getLogger(AUDIT_LOGGER)
log = logging.getLogger(__name__)

Resolver = Callable[[str, int], Awaitable[list[str]]]
Opener = Callable[
    [str, int, str, ssl.SSLContext],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


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


def request_origin(
    request: h11.Request, route: Route, *, websocket: bool = False
) -> tuple[str, int, bytes]:
    """Validate the closed internal wire target; preserve origin path/query bytes."""
    if request.http_version != b"1.1":
        raise Refused("malformed_request", 400)
    if request.method not in {b"GET", b"HEAD", b"POST", b"PUT", b"PATCH", b"DELETE", b"OPTIONS"}:
        raise Refused("unsupported_method", 405)
    prefix = b"/_butlers/egress/v1/wss/" if websocket else _PREFIX
    target = request.target
    if not target.startswith(prefix) or b"#" in target or b"\\" in target:
        raise Refused("malformed_target", 400)
    parts = target[len(prefix) :].split(b"/", 2)
    if len(parts) != 3:
        raise Refused("malformed_target", 400)
    try:
        host = parts[0].decode("ascii")
        port_text = parts[1].decode("ascii")
    except UnicodeDecodeError as exc:
        raise Refused("malformed_target", 400) from exc
    if _is_ip_literal(host):
        raise Refused("ip_literal")
    if not port_text.isascii() or not port_text.isdecimal() or port_text.startswith("0"):
        raise Refused("malformed_authority", 400)
    port = int(port_text) if len(port_text) < 6 else 0
    if (host, port) not in route.destinations or host != host.lower():
        raise Refused("undeclared_destination")
    headers = list(request.headers)
    if [v for k, v in headers if k == b"host"] != [f"egress-gateway:{route.port}".encode()]:
        raise Refused("host_header_mismatch")
    for name, value in headers:
        if (
            (
                name in _HOP_HEADERS
                and name
                not in (
                    {b"connection", b"upgrade"}
                    if websocket
                    else {b"connection", b"transfer-encoding"}
                )
            )
            or (name == b"connection" and value.lower() != (b"upgrade" if websocket else b"close"))
            or name == b"forwarded"
            or name.startswith((b"x-forwarded-", b"x-egress-", b"x-butlers-"))
        ):
            raise Refused("forbidden_header")
    return host, port, b"/" + parts[2]


def _check_framing(head: bytes, *, response: bool = False) -> None:
    # h11 performs HTTP parsing. Inspect raw multiplicity before its deliberate
    # normalization of repeated equal Content-Length values loses that fact.
    names = [line.partition(b":")[0].lower() for line in head.split(b"\r\n")[1:] if line]
    if (
        (not response and names.count(b"host") != 1)
        or names.count(b"content-length") > 1
        or (b"content-length" in names and b"transfer-encoding" in names)
    ):
        raise Refused(
            "ambiguous_response_framing" if response else "ambiguous_framing",
            502 if response else 400,
        )


async def _head(reader: asyncio.StreamReader) -> bytes:
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), _HEAD_TIMEOUT_S)
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError) as exc:
        raise Refused("malformed_headers", 400) from exc
    if len(head) > _HEAD_LIMIT:
        raise Refused("headers_too_large", 400)
    return head


async def _event(
    connection: h11.Connection, reader: asyncio.StreamReader, *, read_budget: float | None = None
):
    while True:
        event = connection.next_event()
        if event is not h11.NEED_DATA:
            return event
        data = await asyncio.wait_for(
            reader.read(16384), _IDLE_TIMEOUT_S if read_budget is None else read_budget
        )
        connection.receive_data(data)


async def _write(
    writer: asyncio.StreamWriter, data: bytes | None, *, write_budget: float | None = None
) -> None:
    if data:
        writer.write(data)
        await asyncio.wait_for(
            writer.drain(), _IDLE_TIMEOUT_S if write_budget is None else write_budget
        )


async def _finish(future: asyncio.Future) -> None:
    """Join owned cleanup despite cancellation; propagate it after settlement."""
    interrupted = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            interrupted = True
    future.result()
    if interrupted:
        raise asyncio.CancelledError


async def _cancel_and_join(*tasks: asyncio.Task) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await _finish(asyncio.gather(*tasks, return_exceptions=True))


async def _origin_head(
    reader: asyncio.StreamReader, uploaded: asyncio.Event, header_budget: float
) -> bytes:
    # A healthy origin may wait for a long, progressing upload. Start its header
    # budget on the first response byte or upload completion, whichever is first.
    first = asyncio.create_task(reader.readexactly(1))
    completed = asyncio.create_task(uploaded.wait())
    try:
        await asyncio.wait({first, completed}, return_when=asyncio.FIRST_COMPLETED)
        async with asyncio.timeout(header_budget):
            initial = await first
            head = initial + await reader.readuntil(b"\r\n\r\n")
        if len(head) > _HEAD_LIMIT:
            raise Refused("headers_too_large", 502)
        return head
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError) as exc:
        raise Refused("malformed_headers", 502) from exc
    finally:
        await _cancel_and_join(first, completed)


async def _upload_and_response(
    connection: h11.Connection,
    reader: asyncio.StreamReader,
    upload: Awaitable[None],
    *,
    header_timeout: float | None = None,
    upload_errors: tuple[type[Exception], ...] = (OSError,),
):
    """One h11 duplex exchange for origin execution and the selected HTTPX hop."""
    uploaded = asyncio.Event()

    early_final = False

    async def receive():
        nonlocal early_final
        for _ in range(5):
            head = await _origin_head(
                reader, uploaded, _HEAD_TIMEOUT_S if header_timeout is None else header_timeout
            )
            _check_framing(head, response=True)
            connection.receive_data(head)
            response = connection.next_event()
            if isinstance(response, h11.InformationalResponse) and response.status_code in (
                100,
                103,
            ):
                continue
            if not isinstance(response, h11.Response):
                raise Refused("unsupported_response", 502)
            early_final = True
            # Stop the sender in the same turn that changes h11's send state;
            # it must not resume a body write after a final response is parsed.
            if not sending.done():
                sending.cancel()
            return response
        raise Refused("unsupported_response", 502)

    sending = asyncio.ensure_future(upload)
    sending.add_done_callback(lambda _task: uploaded.set())
    receiving = asyncio.create_task(receive())
    try:
        await asyncio.wait({sending, receiving}, return_when=asyncio.FIRST_COMPLETED)
        if sending.done() and (not sending.cancelled() or not early_final):
            try:
                sending.result()
            except upload_errors as upload_error:
                # A peer can close its read side immediately after a final
                # response. Do not let that racing write failure hide the
                # response already in flight. Completion arms the bounded
                # response budget; absent a valid response, keep the write error.
                try:
                    return await receiving
                except Exception:
                    raise upload_error from None
        return await receiving
    finally:
        await _cancel_and_join(sending, receiving)


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


async def open_tls(address: str, port: int, host: str, context: ssl.SSLContext):
    """Dial the validated numeric address; only TLS verification uses the origin name."""
    return await asyncio.open_connection(
        address,
        port,
        ssl=context,
        server_hostname=host,
        ssl_handshake_timeout=_CONNECT_TIMEOUT_S,
        limit=16384,
    )


async def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    try:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), _CONNECT_TIMEOUT_S)
    except asyncio.CancelledError:
        writer.transport.abort()
        raise
    except Exception:  # noqa: BLE001 - force-close failed/timed-out teardown, no exception text
        writer.transport.abort()


async def _settle_writers(*writers: asyncio.StreamWriter | None) -> None:
    await _finish(asyncio.gather(*(_close(writer) for writer in writers), return_exceptions=True))


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
        await asyncio.wait_for(writer.drain(), _IDLE_TIMEOUT_S)
    except (ConnectionError, TimeoutError):
        pass


async def handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    route: Route,
    *,
    resolve: Resolver = system_resolver,
    open_upstream: Opener = open_tls,
    tls_context: ssl.SSLContext | None = None,
    slots: asyncio.Semaphore | None = None,
    global_slots: asyncio.Semaphore | None = None,
) -> None:
    """Execute exactly one bounded, framed request on the workload's listener."""
    started = time.monotonic()
    host: str | None = None
    port: int | None = None
    up = down = 0
    upstream: asyncio.StreamWriter | None = None
    exposed = acquired = global_acquired = False
    reason, verdict = "gateway_error", "refused"
    try:
        async with asyncio.timeout(_EXCHANGE_TIMEOUT_S):
            if global_slots is not None:
                if global_slots.locked():
                    raise Refused("busy", 503)
                await global_slots.acquire()
                global_acquired = True
            if slots is not None:
                if slots.locked():
                    raise Refused("busy", 503)
                await slots.acquire()
                acquired = True
            head = await _head(reader)
            client = h11.Connection(h11.SERVER, max_incomplete_event_size=_HEAD_LIMIT)
            _check_framing(head)
            client.receive_data(head)
            request = client.next_event()
            if not isinstance(request, h11.Request):
                raise Refused("malformed_request", 400)
            from butlers.egress import websocket as ws

            is_websocket = request.target.startswith(ws.PREFIX)
            host, port, target = request_origin(request, route, websocket=is_websocket)
            if is_websocket:
                ws.validate_request(request)
            try:
                addresses = await asyncio.wait_for(resolve(host, port), _CONNECT_TIMEOUT_S)
            except (OSError, TimeoutError) as exc:
                raise Refused("dns_failed", 502) from exc
            if not addresses or not all(admissible_address(a) for a in addresses):
                raise Refused("non_public_address")
            context = tls_context if tls_context is not None else ssl.create_default_context()
            if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
                raise Refused("tls_verification_required", 502)
            origin_reader = None
            for address in addresses:
                try:
                    origin_reader, upstream = await asyncio.wait_for(
                        open_upstream(address, port, host, context), _CONNECT_TIMEOUT_S
                    )
                    break
                except (OSError, TimeoutError):
                    continue
            if upstream is None or origin_reader is None:
                raise Refused("upstream_unreachable", 502)
            if is_websocket:
                handshake = await ws.handshake(origin_reader, upstream, request, host, port, target)
                origin, response = handshake.connection, handshake.response
                if handshake.head is not None:
                    await _write(writer, handshake.head)
                    exposed = True

                    def uploaded(count):
                        nonlocal up
                        up += count
                        if up > _UPLOAD_LIMIT:
                            raise Refused("upload_limit", 400)

                    def downloaded(count):
                        nonlocal down
                        down += count
                        if down > _DOWNLOAD_LIMIT:
                            raise Refused("download_limit", 502)

                    await ws.relay(
                        reader,
                        writer,
                        origin_reader,
                        upstream,
                        upload=uploaded,
                        download=downloaded,
                    )
                    verdict, reason = "allowed", "closed"
                    return
            else:
                origin = h11.Connection(h11.CLIENT, max_incomplete_event_size=_HEAD_LIMIT)
                headers = [
                    (k, v)
                    for k, v in request.headers
                    if k not in _HOP_HEADERS | {b"host", b"expect"}
                ]
                authority = host if port == 443 else f"{host}:{port}"
                headers += [(b"host", authority.encode()), (b"connection", b"close")]
                if not any(k == b"content-length" for k, _ in headers):
                    chunked = any(k == b"transfer-encoding" for k, _ in request.headers)
                    headers.append(
                        (b"transfer-encoding", b"chunked") if chunked else (b"content-length", b"0")
                    )
                await _write(
                    upstream,
                    origin.send(h11.Request(method=request.method, target=target, headers=headers)),
                )

                async def upload():
                    nonlocal up
                    while True:
                        event = await _event(client, reader)
                        if isinstance(event, h11.EndOfMessage):
                            if event.headers:
                                raise Refused("trailers_refused", 400)
                            await _write(upstream, origin.send(h11.EndOfMessage()))
                            return
                        if not isinstance(event, h11.Data):
                            raise Refused("malformed_body", 400)
                        up += len(event.data)
                        if up > _UPLOAD_LIMIT:
                            raise Refused("upload_limit", 400)
                        await _write(upstream, origin.send(h11.Data(data=event.data)))

                response = await _upload_and_response(origin, origin_reader, upload())
            if 300 <= response.status_code <= 399:
                raise Refused("redirect_refused")
            nominated = {
                token.strip().lower()
                for name, value in response.headers
                if name == b"connection"
                for token in value.split(b",")
            }
            headers = [
                (k, v)
                for k, v in response.headers
                if k not in _HOP_HEADERS | nominated | {b"content-length", b"x-egress-refusal"}
            ]
            headers.append((b"connection", b"close"))
            await _write(
                writer, client.send(h11.Response(status_code=response.status_code, headers=headers))
            )
            exposed = True
            while True:
                event = await _event(origin, origin_reader)
                if isinstance(event, h11.EndOfMessage):
                    if event.headers:
                        raise Refused("trailers_refused", 502)
                    await _write(writer, client.send(h11.EndOfMessage()))
                    break
                if not isinstance(event, h11.Data):
                    raise Refused("malformed_response", 502)
                down += len(event.data)
                if down > _DOWNLOAD_LIMIT:
                    raise Refused("download_limit", 502)
                await _write(writer, client.send(h11.Data(data=event.data)))
            verdict, reason = "allowed", "closed"
    except Refused as refusal:
        reason = refusal.reason
        if not exposed:
            await _refuse(writer, refusal)
    except (h11.RemoteProtocolError, h11.LocalProtocolError):
        reason = "malformed_http"
        if not exposed:
            await _refuse(writer, Refused(reason, 400))
    except TimeoutError:
        reason = "timeout"
        if not exposed:
            await _refuse(writer, Refused(reason, 502))
    except asyncio.CancelledError:
        reason = "cancelled"
        raise
    except Exception:  # noqa: BLE001 - content-free failure; never log exception/payload
        if not exposed:
            await _refuse(writer, Refused(reason, 502))
    finally:
        try:
            await _settle_writers(upstream, writer)
        finally:
            if acquired and slots is not None:
                slots.release()
            if global_acquired and global_slots is not None:
                global_slots.release()
            _audit(route, host, port, verdict, reason, up, down, started)


async def serve(routes: Mapping[int, Route], host: str = "0.0.0.0") -> None:
    servers = []
    active = asyncio.Semaphore(_MAX_ACTIVE_REQUESTS)
    for port, route in routes.items():
        slots = asyncio.Semaphore(_MAX_TUNNELS_PER_WORKLOAD)

        async def on_connect(reader, writer, route=route, slots=slots):
            await handle_request(reader, writer, route, slots=slots, global_slots=active)

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
