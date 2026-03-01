"""Messenger module — wires messenger domain tools into the butler's MCP server.

Registers 14 MCP tools that delegate to the existing implementations in
``butlers.tools.messenger``. The tool closures strip ``pool``,
``rate_limiter``, and ``circuit_breakers`` from the MCP-visible signature
and inject them from module state at call time.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class MessengerModuleConfig(BaseModel):
    """Configuration for the Messenger module (empty — no settings needed yet)."""


class MessengerModule(Module):
    """Messenger module providing 14 MCP tools for delivery tracking,
    dead letter management, validation, dry-run, and operational health.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "messenger"

    @property
    def config_schema(self) -> type[BaseModel]:
        return MessengerModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # messenger tables already exist via separate migrations

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("MessengerModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all messenger MCP tools."""
        self._db = db

        from .tools import register_tools

        register_tools(mcp, self)
