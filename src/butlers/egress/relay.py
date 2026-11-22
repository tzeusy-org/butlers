"""Carry the workload's loopback request-v1 stream to its authenticated gateway port."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import ssl
from pathlib import Path

from butlers.egress import gateway

_LOOPBACK = "127.0.0.1"
_TLS_DIR = Path("/var/run/butlers-egress-tls")
_DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_GATEWAY_NAME = re.compile(rf"egress-gateway\.{_DNS_LABEL}\.svc\.{_DNS_LABEL}(?:\.{_DNS_LABEL})*")
_CHUNK = 64 * 1024

log = logging.getLogger(__name__)


def _client_tls(directory: Path) -> ssl.SSLContext:
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    tls.minimum_version = ssl.TLSVersion.TLSv1_3
    tls.hostname_checks_common_name = False
    tls.load_verify_locations(cafile=directory / "ca.crt")
    tls.load_cert_chain(directory / "tls.crt", directory / "tls.key", password=lambda: b"")
    return tls


def _require_gateway_name(upstream: asyncio.StreamWriter, name: str) -> None:
    certificate = upstream.get_extra_info("peercert") or {}
    # The TLS handshake verifies the chain and hostname. This profile requires a
    # literal DNS SAN as well, since standard hostname matching accepts wildcards.
    if not any(
        kind == "DNS" and value.lower() == name
        for kind, value in certificate.get("subjectAltName", ())
    ):
        raise ssl.SSLCertVerificationError("gateway DNS SAN mismatch")


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while chunk := await reader.read(_CHUNK):
        writer.write(chunk)
        await asyncio.wait_for(writer.drain(), gateway._IDLE_TIMEOUT_S)


async def _serve(name: str, port: int, tls: ssl.SSLContext) -> None:
    slots = asyncio.Semaphore(gateway._MAX_TUNNELS_PER_WORKLOAD)

    async def forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        streams: tuple[asyncio.Task[None], ...] = ()
        if slots.locked():
            log.warning("egress relay connection limit reached")
            await gateway._settle_writers(writer)
            return
        async with slots:
            try:
                async with asyncio.timeout(gateway._CONNECT_TIMEOUT_S):
                    upstream_reader, upstream = await asyncio.open_connection(
                        name + ".",
                        port,
                        ssl=tls,
                        server_hostname=name,
                        ssl_shutdown_timeout=gateway._CONNECT_TIMEOUT_S,
                    )
                    _require_gateway_name(upstream, name)
                async with asyncio.timeout(gateway._EXCHANGE_TIMEOUT_S):
                    sending = asyncio.create_task(_copy(reader, upstream))
                    receiving = asyncio.create_task(_copy(upstream_reader, writer))
                    streams = (sending, receiving)
                    done, _ = await asyncio.wait(streams, return_when=asyncio.FIRST_COMPLETED)
                    if sending in done:
                        try:
                            sending.result()
                        except OSError:
                            log.warning("egress relay upload interrupted")
                    # TLS has no independent write EOF. Keep the response side open
                    # when the client finishes sending or the gateway stops reading.
                    await receiving
            except ssl.SSLCertVerificationError:
                log.warning("egress relay gateway identity refused")
            except (OSError, TimeoutError):
                log.warning("egress relay transport unavailable")
            finally:
                try:
                    if streams:
                        await gateway._cancel_and_join(*streams)
                finally:
                    await gateway._settle_writers(upstream, writer)

    async def health(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()

    async with await asyncio.start_server(forward, _LOOPBACK, port):
        async with await asyncio.start_server(health, _LOOPBACK, gateway.HEALTH_PORT) as server:
            await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Connect one loopback workload port to the gateway."
    )
    parser.add_argument("--gateway-name", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--tls-dir", type=Path, default=_TLS_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if (
        not 1024 <= args.port <= 65535
        or args.port == gateway.HEALTH_PORT
        or len(args.gateway_name) > 253
        or not _GATEWAY_NAME.fullmatch(args.gateway_name)
    ):
        log.error("egress relay gateway service or port is invalid")
        return 1
    try:
        tls = _client_tls(args.tls_dir)
    except (OSError, ValueError):
        log.error("egress relay TLS identity configuration is invalid or unavailable")
        return 1
    try:
        asyncio.run(_serve(args.gateway_name, args.port, tls))
    except OSError:
        log.error("could not start egress relay listeners")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
