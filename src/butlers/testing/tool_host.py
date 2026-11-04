"""A butler's tools on a real migrated clone, with no daemon lifecycle (bu-rvgzmf.2).

``booted_daemon`` runs the whole startup, about 2.3 s a test in CI. Many tests are
about one tool's SQL, not the boot, so the root ``conftest.py`` offers the cheaper
``tool_host`` fixture. ``host = await tool_host(config_dir)`` does three things:

- clones the template for the butler's chains;
- connects the butler's ``Database`` under its production role;
- registers the core tools on a fresh ``FastMCP`` with the daemon's own
  registration code.

No lifecycle phase runs and none is patched. There is no spawner, Switchboard
client, MCP server, scheduler or module startup. A test that needs any of those
needs a boot instead. Patch collaborators the tool calls (``switchboard_client``,
identity resolution) with ``monkeypatch``.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class ToolHost:
    """One butler's registered tools, bound to its own clone."""

    def __init__(self, daemon: Any, admin_url: str, setup_seconds: float) -> None:
        self.daemon = daemon
        self.admin_url = admin_url
        self.setup_seconds = setup_seconds

    @property
    def mcp(self) -> Any:
        return self.daemon.mcp

    @property
    def pool(self) -> Any:
        return self.daemon.db.pool

    async def tool(self, name: str) -> Callable[..., Any]:
        """The registered handler for *name*, as the MCP server would call it."""
        return (await self.mcp.get_tool(name)).fn

    async def call(self, name: str, **arguments: Any) -> Any:
        return await (await self.tool(name))(**arguments)


class _GivenDatabase:
    """A ``Database`` stand-in that only carries a test's DB-free pool."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.db_name = "db-free"

    async def close(self) -> None:
        return None


class ToolHosts:
    """Creates hosts on fresh clones for one test and closes their pools."""

    def __init__(self, postgres_container: Any) -> None:
        self._container = postgres_container
        self._hosts: list[ToolHost] = []

    async def __call__(
        self,
        config_dir: Path,
        *chains: str,
        chain_schemas: dict[str, str] | None = None,
        pool: Any = None,
    ) -> ToolHost:
        """Host *config_dir*'s tools on a fresh clone of *chains* (default ``core``).

        With *pool* (a DB-free fake, for handler tests whose subject is not SQL) no
        clone is made and the tools get that pool instead.
        """

        from butlers.config import load_config
        from butlers.db import Database
        from butlers.testing.db import migrated_url

        began = time.monotonic()
        config = load_config(config_dir)
        schema = config.db_schema
        if schema is None:
            raise ValueError(f"{config_dir}/butler.toml needs a [butler.db] schema")
        config.switchboard_url = None
        if pool is not None:
            return self._host(config_dir, config, _GivenDatabase(pool), "", began)
        schemas = dict.fromkeys(chains or ("core",), schema)
        schemas.update(chain_schemas or {})
        # A clean context, as for boots: no inherited butler context reaches the
        # template build's worker thread (bu-rvgzmf.12).
        build = functools.partial(migrated_url, self._container, *schemas, schemas=schemas)
        url = await asyncio.get_running_loop().run_in_executor(
            None, contextvars.Context().run, build
        )
        config.db_name = urlparse(url).path.lstrip("/")
        db = Database(
            db_name=config.db_name,
            schema=schema,
            role=f"butler_{schema}_rw",
            host=self._container.get_container_host_ip(),
            port=int(self._container.get_exposed_port(5432)),
            user=self._container.username,
            password=self._container.password,
            min_pool_size=1,
            max_pool_size=3,
        )
        await db.connect()
        try:
            return self._host(config_dir, config, db, url, began)
        except BaseException:
            await db.close()
            raise

    def _host(self, config_dir: Path, config: Any, db: Any, url: str, began: float) -> ToolHost:
        from fastmcp import FastMCP

        from butlers.daemon import ButlerDaemon

        daemon = ButlerDaemon(config_dir, db=db)
        daemon.config = config
        daemon.mcp = FastMCP(config.name)
        daemon._register_core_tools()
        host = ToolHost(daemon, url, time.monotonic() - began)
        self._hosts.append(host)
        return host

    async def close(self) -> None:
        hosts, self._hosts = self._hosts, []
        for host in hosts:
            await host.daemon.db.close()
