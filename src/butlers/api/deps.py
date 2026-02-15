"""MCP client manager and butler discovery dependencies for the dashboard API.

Provides:
- ``MCPClientManager``: lazy FastMCP client connections to running butler daemons
  with graceful unreachable handling and connection caching.
- ``discover_butlers()``: scans the roster directory to find all configured butlers.
- FastAPI dependency functions for injecting the manager and butler configs
  into route handlers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp import Client as MCPClient

if TYPE_CHECKING:
    from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.pricing import PricingConfig, load_pricing
from butlers.config import ConfigError, load_config
from butlers.db import Database, db_params_from_env

logger = logging.getLogger(__name__)

# Default roster location relative to the repository root.
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[3] / "roster"


@dataclass(frozen=True)
class ButlerConnectionInfo:
    """Lightweight record of a butler's identity and MCP connection details."""

    name: str
    port: int
    description: str | None = None
    db_name: str | None = None

    @property
    def sse_url(self) -> str:
        """SSE endpoint URL for this butler's MCP server."""
        return f"http://localhost:{self.port}/sse"


class ButlerUnreachableError(Exception):
    """Raised when a butler MCP server cannot be reached."""

    def __init__(self, butler_name: str, cause: Exception | None = None) -> None:
        self.butler_name = butler_name
        self.cause = cause
        msg = f"Butler '{butler_name}' is unreachable"
        if cause:
            msg += f": {cause}"
        super().__init__(msg)


class MCPClientManager:
    """Manages lazy FastMCP client connections to running butler MCP servers.

    Clients are created on first access and cached for reuse. If a butler's
    MCP server is unreachable, the manager raises ``ButlerUnreachableError``
    with clear diagnostics.

    Usage::

        mgr = MCPClientManager()
        mgr.register("switchboard", ButlerConnectionInfo("switchboard", 8100))
        client = await mgr.get_client("switchboard")
        tools = await client.list_tools()
        await mgr.close()
    """

    def __init__(self) -> None:
        self._registry: dict[str, ButlerConnectionInfo] = {}
        self._clients: dict[str, MCPClient] = {}

    def register(self, butler_name: str, info: ButlerConnectionInfo) -> None:
        """Register a butler's connection info.

        Parameters
        ----------
        butler_name:
            The butler's name (used as lookup key).
        info:
            Connection details for the butler.
        """
        if butler_name in self._registry:
            logger.warning("Butler %s already registered; overwriting", butler_name)
        self._registry[butler_name] = info
        logger.debug("Registered butler: %s at port %d", butler_name, info.port)

    @property
    def butler_names(self) -> list[str]:
        """Return list of all registered butler names."""
        return list(self._registry.keys())

    def get_connection_info(self, butler_name: str) -> ButlerConnectionInfo | None:
        """Return connection info for a butler, or None if not registered."""
        return self._registry.get(butler_name)

    async def get_client(self, butler_name: str) -> MCPClient:
        """Get a connected MCP client for the given butler.

        Creates and connects a new client on first call, then caches it.
        If the butler is not registered or the connection fails, raises
        ``ButlerUnreachableError``.

        Parameters
        ----------
        butler_name:
            Name of the butler to connect to.

        Returns
        -------
        MCPClient
            A connected FastMCP client.

        Raises
        ------
        ButlerUnreachableError
            If the butler is not registered or connection fails.
        """
        # Return cached client if still connected
        if butler_name in self._clients:
            client = self._clients[butler_name]
            if client.is_connected():
                return client
            # Client exists but disconnected — clean it up
            logger.info("Client for %s disconnected; reconnecting", butler_name)
            await self._close_client(butler_name)

        info = self._registry.get(butler_name)
        if info is None:
            raise ButlerUnreachableError(
                butler_name,
                cause=KeyError(f"Butler '{butler_name}' is not registered"),
            )

        try:
            client = MCPClient(info.sse_url, name=f"dashboard-{butler_name}")
            await client.__aenter__()
            self._clients[butler_name] = client
            logger.info("Connected to butler %s at %s", butler_name, info.sse_url)
            return client
        except Exception as exc:
            raise ButlerUnreachableError(butler_name, cause=exc) from exc

    async def _close_client(self, butler_name: str) -> None:
        """Close a single client connection."""
        client = self._clients.pop(butler_name, None)
        if client is not None:
            try:
                await client.__aexit__(None, None, None)
                logger.debug("Closed client for butler: %s", butler_name)
            except Exception:
                logger.warning("Error closing client for butler: %s", butler_name, exc_info=True)

    async def close(self) -> None:
        """Close all managed client connections."""
        names = list(self._clients.keys())
        for name in names:
            await self._close_client(name)
        logger.info("MCPClientManager closed (%d clients)", len(names))


def discover_butlers(
    roster_dir: Path | None = None,
) -> list[ButlerConnectionInfo]:
    """Scan the roster directory and return connection info for all configured butlers.

    Each subdirectory of ``roster_dir`` containing a ``butler.toml`` is loaded
    via the existing config loader. Directories that fail to parse are logged
    as warnings and skipped.

    Parameters
    ----------
    roster_dir:
        Path to the roster directory. Defaults to ``<repo>/roster/``.

    Returns
    -------
    list[ButlerConnectionInfo]
        Sorted by butler name for deterministic ordering.
    """
    if roster_dir is None:
        roster_dir = _DEFAULT_ROSTER_DIR

    if not roster_dir.is_dir():
        logger.warning("Roster directory not found: %s", roster_dir)
        return []

    butlers: list[ButlerConnectionInfo] = []

    for entry in sorted(roster_dir.iterdir()):
        if not entry.is_dir():
            continue
        toml_path = entry / "butler.toml"
        if not toml_path.exists():
            continue

        try:
            config = load_config(entry)
            butlers.append(
                ButlerConnectionInfo(
                    name=config.name,
                    port=config.port,
                    description=config.description,
                    db_name=config.db_name or None,
                )
            )
        except ConfigError as exc:
            logger.warning("Skipping butler in %s: %s", entry.name, exc)

    logger.info("Discovered %d butler(s) from %s", len(butlers), roster_dir)
    return butlers


# ---------------------------------------------------------------------------
# Module-level singleton for FastAPI dependency injection
# ---------------------------------------------------------------------------

_mcp_manager: MCPClientManager | None = None
_butler_configs: list[ButlerConnectionInfo] | None = None


def init_dependencies(
    roster_dir: Path | None = None,
) -> tuple[MCPClientManager, list[ButlerConnectionInfo]]:
    """Initialize the module-level singletons.

    Called once during app startup (in the lifespan handler). Discovers
    butlers from the roster and pre-registers them in the MCP client manager.

    Returns the manager and config list for use in the lifespan handler.
    """
    global _mcp_manager, _butler_configs  # noqa: PLW0603

    configs = discover_butlers(roster_dir)
    manager = MCPClientManager()

    for info in configs:
        manager.register(info.name, info)

    _mcp_manager = manager
    _butler_configs = configs
    return manager, configs


async def shutdown_dependencies() -> None:
    """Clean up module-level singletons. Called during app shutdown."""
    global _mcp_manager, _butler_configs  # noqa: PLW0603

    if _mcp_manager is not None:
        await _mcp_manager.close()
        _mcp_manager = None
    _butler_configs = None


def get_mcp_manager() -> MCPClientManager:
    """FastAPI dependency: provides the MCPClientManager singleton.

    Usage::

        @router.get("/butlers/{name}/tools")
        async def butler_tools(mgr: MCPClientManager = Depends(get_mcp_manager)):
            client = await mgr.get_client(name)
            return await client.list_tools()
    """
    if _mcp_manager is None:
        raise RuntimeError("MCPClientManager not initialized — call init_dependencies() first")
    return _mcp_manager


def get_butler_configs() -> list[ButlerConnectionInfo]:
    """FastAPI dependency: provides the list of discovered butler configs.

    Usage::

        @router.get("/butlers")
        async def list_butlers(configs = Depends(get_butler_configs)):
            return [{"name": c.name, "port": c.port} for c in configs]
    """
    if _butler_configs is None:
        raise RuntimeError("Butler configs not initialized — call init_dependencies() first")
    return _butler_configs


# ---------------------------------------------------------------------------
# Pricing configuration singleton
# ---------------------------------------------------------------------------


_pricing_config: PricingConfig | None = None


def init_pricing(path: Path | None = None) -> PricingConfig:
    """Initialize the pricing configuration singleton.

    Called once during app startup.
    """
    global _pricing_config  # noqa: PLW0603
    _pricing_config = load_pricing(path)
    return _pricing_config


def get_pricing() -> PricingConfig:
    """FastAPI dependency: provides the PricingConfig singleton."""
    if _pricing_config is None:
        raise RuntimeError("PricingConfig not initialized — call init_pricing() first")
    return _pricing_config


# ---------------------------------------------------------------------------
# DatabaseManager singleton
# ---------------------------------------------------------------------------

_db_manager: DatabaseManager | None = None


def _db_params_from_env() -> dict[str, str | int | None]:
    """Read DB connection params from environment variables."""
    return db_params_from_env()


async def init_db_manager(
    butler_configs: list[ButlerConnectionInfo],
) -> DatabaseManager:
    """Create the DatabaseManager singleton and add pools for each butler.

    Called once during app startup (in the lifespan handler).
    """
    global _db_manager  # noqa: PLW0603

    params = _db_params_from_env()
    mgr = DatabaseManager(**params)

    for cfg in butler_configs:
        try:
            db = Database.from_env(cfg.db_name or f"butler_{cfg.name}")
            await db.provision()
            await mgr.add_butler(cfg.name, db_name=cfg.db_name)
        except Exception:
            logger.warning("Failed to add DB pool for butler %s", cfg.name, exc_info=True)

    _db_manager = mgr
    return mgr


async def shutdown_db_manager() -> None:
    """Close the DatabaseManager singleton. Called during app shutdown."""
    global _db_manager  # noqa: PLW0603
    if _db_manager is not None:
        await _db_manager.close()
        _db_manager = None


def get_db_manager() -> DatabaseManager:
    """FastAPI dependency: provides the DatabaseManager singleton."""
    if _db_manager is None:
        raise RuntimeError("DatabaseManager not initialized — call init_db_manager() first")
    return _db_manager


def wire_db_dependencies(app: FastAPI, dynamic_modules: list | None = None) -> None:
    """Override all router-level ``_get_db_manager`` stubs with the singleton.

    Parameters
    ----------
    app:
        The FastAPI application instance.
    dynamic_modules:
        Optional list of dynamically-loaded router modules (from roster/{butler}/api/).
        Each module is scanned for a _get_db_manager stub function.
    """
    from butlers.api.routers import (
        audit,
        butlers,
        general,
        health,
        memory,
        notifications,
        relationship,
        schedules,
        search,
        sessions,
        state,
        switchboard_views,
        timeline,
        traces,
    )

    # Wire static routers (existing core routers)
    for module in [
        audit,
        butlers,
        general,
        health,
        memory,
        notifications,
        relationship,
        schedules,
        search,
        sessions,
        state,
        switchboard_views,
        timeline,
        traces,
    ]:
        app.dependency_overrides[module._get_db_manager] = get_db_manager

    # Wire dynamically-loaded butler routers
    if dynamic_modules:
        for module in dynamic_modules:
            if hasattr(module, "_get_db_manager"):
                app.dependency_overrides[module._get_db_manager] = get_db_manager
                logger.debug(
                    "Wired DB dependency for dynamic module: %s",
                    module.__name__,
                )
