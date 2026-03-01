"""Switchboard module — wires switchboard domain tools into the butler's MCP server.

Registers MCP tools for routing, operator controls, notification delivery,
extraction audit, backfill management, and dead-letter queue operations.
The tool closures strip infrastructure arguments (pool, conn) from the
MCP-visible signature and inject them from module state at call time.

The Switchboard is an infrastructure butler. Many of its tools take either
``pool: asyncpg.Pool`` or ``conn: asyncpg.Connection`` as the first argument.
For conn-based tools, the module acquires a connection from the pool within
the closure.

Internal daemon infrastructure functions (ingest pipeline, heartbeat ingestion,
triage evaluation, eligibility sweeps, identity resolution, connector-facing
backfill tools, telemetry, and parse/validation utilities) are NOT registered
as MCP tools — they are called directly by the daemon or connectors.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class SwitchboardModuleConfig(BaseModel):
    """Configuration for the Switchboard module (empty — no settings needed yet)."""


class SwitchboardModule(Module):
    """Switchboard module providing MCP tools for routing, operator controls,
    notification delivery, extraction audit, backfill management, and dead-letter
    queue operations.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "switchboard"

    @property
    def config_schema(self) -> type[BaseModel]:
        return SwitchboardModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # switchboard tables already exist via separate migrations

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("SwitchboardModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all switchboard MCP tools."""
        self._db = db
        from .tools import register_tools

        register_tools(mcp, self)
