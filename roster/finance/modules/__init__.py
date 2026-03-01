"""Finance module — wires finance domain tools into the butler's MCP server.

Registers 6 MCP tools that delegate to the existing implementations in
``butlers.tools.finance``. The tool closures strip ``pool`` from the
MCP-visible signature and inject it from module state at call time.

Type conversions at the MCP boundary:
- ``posted_at``: accepted as ISO-8601 string, converted to ``datetime`` via
  ``fromisoformat()`` before passing to the implementation.
- Amount fields: accepted as ``float`` from MCP, implementations accept
  ``Decimal | float | int`` so no conversion needed.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class FinanceModuleConfig(BaseModel):
    """Configuration for the Finance module (empty -- no settings needed yet)."""


class FinanceModule(Module):
    """Finance module providing 6 MCP tools for transactions, subscriptions,
    bills, and spending analysis.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "finance"

    @property
    def config_schema(self) -> type[BaseModel]:
        return FinanceModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # finance tables already exist via separate migrations

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("FinanceModule not initialised -- no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all finance MCP tools."""
        self._db = db

        from .tools import register_tools

        register_tools(mcp, self)
