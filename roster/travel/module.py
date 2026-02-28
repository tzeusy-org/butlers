"""Travel module — wires travel domain tools into the butler's MCP server.

Registers 6 MCP tools that delegate to the existing implementations in
``butlers.tools.travel``. The tool closures strip ``pool`` from the
MCP-visible signature and inject it from module state at call time.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class TravelModuleConfig(BaseModel):
    """Configuration for the Travel module (empty — no settings needed yet)."""


class TravelModule(Module):
    """Travel module providing 6 MCP tools for trips, bookings, and documents."""

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "travel"

    @property
    def config_schema(self) -> type[BaseModel]:
        return TravelModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None  # travel tables already exist via separate migrations

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the Database reference for later pool access."""
        self._db = db

    async def on_shutdown(self) -> None:
        """Clear state references."""
        self._db = None

    def _get_pool(self):
        """Return the asyncpg pool, raising if not initialised."""
        if self._db is None:
            raise RuntimeError("TravelModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all travel MCP tools."""
        self._db = db
        module = self  # capture for closures

        # Import sub-modules (deferred to avoid import-time side effects)
        from butlers.tools.travel import bookings as _bookings
        from butlers.tools.travel import documents as _documents
        from butlers.tools.travel import trips as _trips

        # =================================================================
        # Trip query tools
        # =================================================================

        @mcp.tool()
        async def upcoming_travel(
            within_days: int = 14,
            include_pretrip_actions: bool = True,
        ) -> dict[str, Any]:
            """Scan for trips departing within the next N days and surface pre-trip action gaps."""
            return await _trips.upcoming_travel(
                module._get_pool(),
                within_days=within_days,
                include_pretrip_actions=include_pretrip_actions,
            )

        @mcp.tool()
        async def list_trips(
            status: str | None = None,
            from_date: str | None = None,
            to_date: str | None = None,
            limit: int = 20,
            offset: int = 0,
        ) -> dict[str, Any]:
            """List trip containers filtered by lifecycle status and/or date range."""
            return await _trips.list_trips(
                module._get_pool(),
                status=status,
                from_date=from_date,
                to_date=to_date,
                limit=limit,
                offset=offset,
            )

        @mcp.tool()
        async def trip_summary(
            trip_id: str,
            include_documents: bool = True,
            include_timeline: bool = True,
        ) -> dict[str, Any]:
            """Get a complete trip snapshot with all linked entities, timeline, and alerts."""
            return await _trips.trip_summary(
                module._get_pool(),
                trip_id,
                include_documents=include_documents,
                include_timeline=include_timeline,
            )

        # =================================================================
        # Booking tools
        # =================================================================

        @mcp.tool()
        async def record_booking(payload: dict[str, Any]) -> dict[str, Any]:
            """Parse a booking confirmation and persist it into the trip container model."""
            return await _bookings.record_booking(module._get_pool(), payload)

        @mcp.tool()
        async def update_itinerary(
            trip_id: str,
            patch: dict[str, Any],
            reason: str | None = None,
        ) -> dict[str, Any]:
            """Apply itinerary changes to a trip — rebookings, delays, seat/gate updates."""
            return await _bookings.update_itinerary(
                module._get_pool(),
                trip_id,
                patch,
                reason=reason,
            )

        # =================================================================
        # Document tools
        # =================================================================

        @mcp.tool()
        async def add_document(
            trip_id: str,
            type: str,
            blob_ref: str | None = None,
            expiry_date: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Attach a travel document reference to a trip."""
            return await _documents.add_document(
                module._get_pool(),
                trip_id,
                type,
                blob_ref=blob_ref,
                expiry_date=expiry_date,
                metadata=metadata,
            )
