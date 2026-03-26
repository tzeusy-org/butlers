"""Insight broker module — proactive insight candidate management and delivery.

Registers the ``propose_insight_candidate`` MCP tool on the Switchboard butler,
allowing any downstream butler to submit ranked insight candidates that compete
for delivery during the scheduled insight-delivery-cycle job.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class InsightBrokerConfig(BaseModel):
    """Configuration for the InsightBrokerModule (no required settings)."""


class InsightBrokerModule(Module):
    """Module that registers the propose_insight_candidate MCP tool.

    This module wires the Switchboard's insight broker into the MCP server,
    enabling downstream butlers to propose insight candidates for delivery.
    The insight-delivery-cycle scheduled job (cron ``0 8 * * *``) orchestrates
    the delivery pipeline — filtering, deduplication, budget enforcement, and
    notification dispatch.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "insight_broker"

    @property
    def config_schema(self) -> type[BaseModel]:
        return InsightBrokerConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        # insight tables are created via the shared Alembic migration for
        # public.insight_candidates / insight_settings / insight_cooldowns /
        # insight_engagement — no separate branch label needed here.
        return None

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        """Store the database reference for pool access at tool call time."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self) -> Any:
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("InsightBrokerModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register the propose_insight_candidate MCP tool."""
        self._db = db
        from butlers.tools.switchboard.insight.broker import (
            propose_insight_candidate as _propose,
        )

        @mcp.tool()
        async def propose_insight_candidate(
            origin_butler: str,
            priority: int,
            category: str,
            dedup_key: str,
            message: str,
            expires_at: str,
            cooldown_days: int | None = None,
            channel: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, str]:
            """Submit a proactive insight candidate for the next delivery cycle.

            Validates the candidate and stages it in ``public.insight_candidates``.
            Candidates compete for delivery slots during the daily
            ``insight-delivery-cycle`` job based on priority and global budget.

            Parameters
            ----------
            origin_butler:
                Name of the butler producing this insight.
            priority:
                Delivery priority, 1-100. Higher = more important.
                90-100: time-critical; 70-89: actionable-soon;
                50-69: informational; 30-49: low-urgency; 1-29: background.
            category:
                Domain category (e.g. ``"birthday"``, ``"spending-anomaly"``).
            dedup_key:
                Semantic dedup key. Format:
                ``{category}:{entity}:{time-scope}`` (cross-butler) or
                ``{butler}:{category}:{entity}:{time-scope}`` (butler-specific).
            message:
                Human-readable insight text to deliver.
            expires_at:
                ISO 8601 datetime after which the candidate is expired
                if not yet delivered.
            cooldown_days:
                Optional override for the default cooldown period after delivery.
            channel:
                Optional preferred delivery channel (e.g. ``"telegram"``).
            metadata:
                Optional butler-specific structured data (stored as JSONB).

            Returns
            -------
            dict
                ``{"status": "accepted", "reason": "candidate queued for delivery cycle"}``
                on success, ``{"status": "filtered", ...}`` when verbosity is off,
                or ``{"status": "error", "reason": "<description>"}`` on validation
                failure.
            """
            return await _propose(
                self._get_pool(),
                origin_butler=origin_butler,
                priority=priority,
                category=category,
                dedup_key=dedup_key,
                message=message,
                expires_at=expires_at,
                cooldown_days=cooldown_days,
                channel=channel,
                metadata=metadata,
            )
