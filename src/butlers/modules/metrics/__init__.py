"""Metrics module — opt-in Prometheus integration for butler instances.

Lets any butler define and emit named metrics (counter, gauge, histogram)
via MCP tools, and query historical data via PromQL against the Prometheus
HTTP API.

Write-side emission uses the OTEL SDK via ``get_meter()`` (reuses the
existing MeterProvider/OTLP pipeline). Read-side queries hit the Prometheus
HTTP API via ``httpx``. Metric definitions are persisted to the butler's
existing state store (KV JSONB) for restart durability — no migrations.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class MetricsModuleConfig(BaseModel):
    """Configuration for the Metrics module."""

    model_config = ConfigDict(extra="forbid")

    prometheus_query_url: str = Field(
        description=(
            "Base URL of the Prometheus-compatible HTTP API endpoint "
            "(e.g. 'http://lgtm:9090'). Used for PromQL read queries only."
        )
    )


class MetricsModule(Module):
    """Opt-in module providing Prometheus metrics define/emit/query MCP tools.

    Metric definitions are persisted to the butler's state store under keys
    ``metrics_catalogue:<name>`` so they survive daemon restarts. Instruments
    are rebuilt from the state store during ``on_startup``.

    Hard cap: 1,000 defined metrics per butler.
    """

    def __init__(self) -> None:
        self._config: MetricsModuleConfig | None = None
        self._db: Any = None

    @property
    def name(self) -> str:
        return "metrics"

    @property
    def config_schema(self) -> type[BaseModel]:
        return MetricsModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register metrics MCP tools on the butler's FastMCP server.

        Tool registration is a no-op on this stub; concrete tool wiring is
        implemented in a follow-on task (butlers-lxiq.2 onwards).
        """
        self._config = self._coerce_config(config)
        self._db = db

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store config and DB reference.

        Instrument cache population from the persisted state store is
        implemented in a follow-on task (butlers-lxiq.3).
        """
        self._config = self._coerce_config(config)
        self._db = db
        logger.debug("MetricsModule: startup complete (no instruments to restore yet)")

    async def on_shutdown(self) -> None:
        """Release state references."""
        self._config = None
        self._db = None
        logger.debug("MetricsModule: shutdown complete")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_config(config: Any) -> MetricsModuleConfig | None:
        """Coerce raw dict config to MetricsModuleConfig, or return None."""
        if config is None:
            return None
        if isinstance(config, MetricsModuleConfig):
            return config
        if isinstance(config, dict):
            return MetricsModuleConfig(**config)
        raise TypeError(f"Unsupported config type for MetricsModule: {type(config).__name__}")


__all__ = [
    "MetricsModule",
    "MetricsModuleConfig",
]
