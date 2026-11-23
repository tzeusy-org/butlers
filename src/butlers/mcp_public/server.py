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
    Tool,
    create_pool,
    default_tools,
)
from butlers.capability_host.host import PROBE_TIMEOUT_S
from butlers.mcp_public.app import McpApp, MetricsApp
from butlers.mcp_public.catalogue import STATIC_HANDLERS, catalogue
from butlers.mcp_public.proposals import propose_fact

logger = logging.getLogger(__name__)
BIND = "0.0.0.0"  # the pod's NetworkPolicies decide who reaches either port (D8)


def served_tools() -> dict[str, Tool]:
    """The host's readers plus ``propose_fact``, the one tool with its own write transaction."""
    return {
        **default_tools(),
        "propose_fact": Tool("propose_fact", "proposals", propose_fact, dlp=False, writes=True),
    }


def build_host(settings: HostSettings, pool: asyncpg.Pool | None) -> CapabilityHost:
    catalogue()  # fail at start, not on the first tools/list, if the catalogue drifted
    return CapabilityHost(settings, pool=pool, tools=served_tools(), static=STATIC_HANDLERS)


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


WARM_TIMEOUT_S = PROBE_TIMEOUT_S + 15  # one probe plus connect; never hold the ports shut longer


async def warm(host: CapabilityHost, limit_s: float = WARM_TIMEOUT_S) -> None:
    """Probe before the ports open so the first client burst finds a warm host (bu-swkpoo.1).

    Bounded and best-effort: a slow or failed warm-up is logged and serving starts anyway; the
    first call then probes as it always did.
    """
    try:
        async with asyncio.timeout(limit_s):
            await host.warm()
    except TimeoutError:
        logger.warning("public MCP warm-up timed out after %ss; serving cold", limit_s)


async def serve(settings: HostSettings) -> None:
    pool = await create_pool(settings)
    try:
        host = build_host(settings, pool)
        await warm(host)
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
