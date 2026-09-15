"""dashboard_read module — read-only fleet telemetry tools for the Concierge staffer.

Wraps the sanctioned RFC 0030 cross-schema views (``concierge.v_fleet_sessions``
/ ``concierge.v_fleet_spend``) as MCP tools. See ``roster/concierge/migrations/
001_fleet_views.py`` for the view definitions and column allowlist, and
``about/legends-and-lore/rfcs/0030-system-plane-read-exception.md`` for the
governing exception.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.core.pricing import PricingConfig, load_pricing
from butlers.modules.base import Module

from .queries import ensure_views_available

logger = logging.getLogger(__name__)


class DashboardReadModuleConfig(BaseModel):
    """Configuration for the dashboard_read module (no settings needed yet)."""


class DashboardReadModule(Module):
    """Read-only fleet telemetry tools sourced from the RFC 0030 views."""

    def __init__(self) -> None:
        self._db: Any = None
        self._pricing: PricingConfig | None = None

    @property
    def name(self) -> str:
        return "dashboard_read"

    @property
    def config_schema(self) -> type[BaseModel]:
        return DashboardReadModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        # The two fleet views + grants live in roster/concierge/migrations,
        # which is the butler-owned "concierge" chain, not a module chain.
        return None

    def _get_pool(self) -> Any:
        if self._db is None:
            raise RuntimeError("DashboardReadModule not initialised — no DB available")
        pool = getattr(self._db, "pool", None)
        if pool is None:
            raise RuntimeError("DashboardReadModule not initialised — no DB pool available")
        return pool

    def _get_pricing(self) -> PricingConfig:
        if self._pricing is None:
            self._pricing = load_pricing()
        return self._pricing

    async def on_startup(
        self, config: Any, db: Any, credential_store: Any = None, blob_store: Any = None
    ) -> None:
        self._db = db
        self._pricing = load_pricing()
        pool = getattr(db, "pool", None) if db is not None else None
        if pool is not None:
            await ensure_views_available(pool)

    async def on_shutdown(self) -> None:
        self._db = None

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        self._db = db
        from .tools import register_tools

        register_tools(mcp, self)
