"""Travel module — wires travel domain tools into the butler's MCP server.

Registers 7 MCP tools that delegate to the existing implementations in
``butlers.tools.travel``. The tool closures strip ``pool`` from the
MCP-visible signature and inject it from module state at call time.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.core.tool_catalog import RecipientParameters
from butlers.modules.base import Module, ToolMeta

logger = logging.getLogger(__name__)


class TravelModuleConfig(BaseModel):
    """Configuration for the Travel module (empty — no settings needed yet)."""


class TravelModule(Module):
    """Travel module providing tools for trips, bookings, and preparation."""

    def __init__(self) -> None:
        self._db: Any = None
        self._switchboard_client: Any = None

    @property
    def name(self) -> str:
        return "travel"

    @property
    def config_schema(self) -> type[BaseModel]:
        return TravelModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def tool_metadata(self) -> dict[str, ToolMeta]:
        return {
            "document_expiry_inventory": ToolMeta(
                arg_sensitivities={},
                canonical_name="document_expiry_inventory",
                module_name="travel",
                group_name="travel",
                namespace="travel.travel",
                llm_presentable=True,
                load_posture="deferred",
                effect_class="read",
                output_trust="untrusted",
                output_sensitivity="sensitive",
                resource_params=(),
                safety_args=(),
                idempotency="none",
                owner_authority=False,
                recipient_params=RecipientParameters(),
                read_stores=("travel.documents", "travel.trips"),
                write_stores=(),
                unknown_output_source=True,
            )
        }

    def migration_revisions(self) -> str | None:
        return None  # travel tables already exist via separate migrations

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None
        self._switchboard_client = None

    def wire_runtime(
        self,
        spawner: Any,
        repo_root: Any,
        switchboard_client: Any = None,
    ) -> None:
        """Receive the daemon's Switchboard MCP client for cross-butler reads."""
        del spawner, repo_root
        self._switchboard_client = switchboard_client

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("TravelModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register all travel MCP tools."""
        self._db = db

        from .tools import register_tools

        register_tools(mcp, self)
