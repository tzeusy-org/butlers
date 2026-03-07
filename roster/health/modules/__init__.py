"""Health module — wires health domain tools into the butler's MCP server."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class HealthModuleConfig(BaseModel):
    """Configuration for the Health module (empty — no settings needed yet)."""


class HealthModule(Module):
    """Health module providing 20 MCP tools for measurements, medications,
    conditions, symptoms, diet/nutrition, reports, and research.
    """

    def __init__(self) -> None:
        self._db: Any = None
        self._calendar_module: Any = None

    def set_calendar_module(self, calendar_module: Any) -> None:
        """Inject the CalendarModule so meal_log can create calendar events."""
        self._calendar_module = calendar_module

    def _make_calendar_event_fn(self) -> Any:
        """Return a coroutine factory for calendar event creation, or None if unavailable."""
        if self._calendar_module is None:
            return None
        calendar_module = self._calendar_module

        async def _create(
            *, title: str, start_at: Any, end_at: Any, description: str | None = None
        ) -> None:
            await calendar_module.create_user_event(
                title=title, start_at=start_at, end_at=end_at, description=description
            )

        return _create

    @property
    def name(self) -> str:
        return "health"

    @property
    def config_schema(self) -> type[BaseModel]:
        return HealthModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # health tables already exist via separate migrations

    def on_all_modules_ready(self, module_map: dict[str, Any]) -> None:
        """Wire CalendarModule after all modules have registered their tools."""
        calendar_mod = module_map.get("calendar")
        if calendar_mod is not None:
            self.set_calendar_module(calendar_mod)

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("HealthModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all health MCP tools."""
        self._db = db

        from .tools import register_tools

        register_tools(mcp, self)
