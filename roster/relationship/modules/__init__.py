"""Relationship module — wires relationship domain tools into the butler's MCP server.

Registers 60+ MCP tools that delegate to the existing implementations in
``butlers.tools.relationship``. The tool closures strip ``pool`` and internal
params (``memory_pool``, ``memory_tenant_id``) from the MCP-visible signature
and inject them from module state at call time.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class RelationshipModuleConfig(BaseModel):
    """Configuration for the Relationship module (empty -- no settings needed yet)."""


class RelationshipModule(Module):
    """Relationship module providing 60+ MCP tools for contacts, interactions,
    dates, gifts, groups, labels, life events, loans, notes, relationships,
    reminders, tasks, addresses, contact info, facts, feed, stay-in-touch,
    resolve, and vCard import/export.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "relationship"

    @property
    def config_schema(self) -> type[BaseModel]:
        return RelationshipModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # relationship tables already exist via separate migrations

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("RelationshipModule not initialised -- no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all relationship MCP tools."""
        self._db = db
        from .tools import register_tools

        register_tools(mcp, self)
