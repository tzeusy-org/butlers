"""Uncredentialed TCP relay for the restore-drill executor's internal network.

The credentialed executor can reach this process only through an internal
Compose network.  This relay has no password or shared application database
settings; it accepts a required resolved IPv4 PostgreSQL endpoint so the
supported launchers' host firewall can constrain its sole external route.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_LISTEN_HOST = "0.0.0.0"
_LISTEN_PORT = 5432


@dataclass(frozen=True)
class RestoreDrillProxyConfig:
    """The relay's deliberately small, non-secret external route."""

    db_host: str
    db_port: int


def _required_ipv4(name: str) -> str:
    value = os.environ.get(name, "").strip()
    try:
        parsed = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as exc:
        raise ValueError(f"{name} must be a resolved IPv4 address") from exc
    return str(parsed)


def _required_port(name: str) -> int:
    value = os.environ.get(name, "").strip()
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer in 1..65535") from exc
    if not 1 <= port <= 65535:
        raise ValueError(f"{name} must be an integer in 1..65535")
    return port


def load_restore_drill_proxy_config() -> RestoreDrillProxyConfig:
    """Read only the resolved endpoint supplied by the protected launcher."""
    return RestoreDrillProxyConfig(
        db_host=_required_ipv4("RESTORE_DRILL_PROXY_DB_HOST"),
        db_port=_required_port("RESTORE_DRILL_PROXY_DB_PORT"),
    )


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


async def relay_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    *,
    config: RestoreDrillProxyConfig,
) -> None:
    """Pass one raw PostgreSQL/TLS stream to the fixed remote endpoint."""
    try:
        remote_reader, remote_writer = await asyncio.open_connection(config.db_host, config.db_port)
    except OSError:
        logger.warning(
            "restore-drill relay could not connect to its configured PostgreSQL endpoint"
        )
        client_writer.close()
        await client_writer.wait_closed()
        return

    await asyncio.gather(
        _copy(client_reader, remote_writer),
        _copy(remote_reader, client_writer),
    )


async def run_restore_drill_proxy(config: RestoreDrillProxyConfig) -> None:
    """Serve the internal executor without exposing a host port."""
    server = await asyncio.start_server(
        lambda reader, writer: relay_connection(reader, writer, config=config),
        host=_LISTEN_HOST,
        port=_LISTEN_PORT,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    """Fail closed before opening a listener when the relay route is invalid."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    config = load_restore_drill_proxy_config()
    asyncio.run(run_restore_drill_proxy(config))


if __name__ == "__main__":  # pragma: no cover - exercised by the container entrypoint
    main()
