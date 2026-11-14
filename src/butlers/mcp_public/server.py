"""``butlers-public-mcp``: one process serving MCP on 8080 and metrics on 9090 (RFC 0039 D1).

Configuration is :meth:`HostSettings.from_env` plus the files under ``/etc/butlers-public-mcp``
(see :mod:`butlers.capability_host.config`); a missing or malformed setting exits non-zero
before any port opens. There is no roster entry, scheduler, spawner or state store.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import asyncpg
import uvicorn

from butlers.capability_host import (
    MCP_PORT,
    METRICS_PORT,
    CapabilityHost,
    ConfigError,
    HostSettings,
    create_pool,
)
from butlers.mcp_public.app import McpApp, MetricsApp
from butlers.mcp_public.catalogue import STATIC_HANDLERS, catalogue

logger = logging.getLogger(__name__)
BIND = "0.0.0.0"  # the pod's NetworkPolicies decide who reaches either port (D8)


def build_host(settings: HostSettings, pool: asyncpg.Pool | None) -> CapabilityHost:
    catalogue()  # fail at start, not on the first tools/list, if the catalogue drifted
    return CapabilityHost(settings, pool=pool, static=STATIC_HANDLERS)


def _server(app: McpApp | MetricsApp, port: int) -> uvicorn.Server:
    return uvicorn.Server(
        uvicorn.Config(
            app,
            host=BIND,
            port=port,
            lifespan="off",
            access_log=False,  # the host's audit line is the only request log (D15)
            server_header=False,
            proxy_headers=False,
            log_config=None,
        )
    )


async def serve(settings: HostSettings) -> None:
    pool = await create_pool(settings)
    try:
        host = build_host(settings, pool)
        await asyncio.gather(
            _server(McpApp(host), MCP_PORT).serve(),
            _server(MetricsApp(host), METRICS_PORT).serve(),
        )
    finally:
        await pool.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        settings = HostSettings.from_env(os.environ)
    except ConfigError as exc:
        logger.error("configuration refused: %s", exc)
        sys.exit(2)
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
