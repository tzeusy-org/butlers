"""General module — wires general domain tools into the butler's MCP server."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class GeneralModuleConfig(BaseModel):
    """Configuration for the General module (empty — no settings needed yet)."""


class GeneralModule(Module):
    """General module providing 9 MCP tools for collections and entities."""

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "general"

    @property
    def config_schema(self) -> type[BaseModel]:
        return GeneralModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("GeneralModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all general MCP tools."""
        self._db = db

        from .tools import register_tools

        register_tools(mcp, self)
