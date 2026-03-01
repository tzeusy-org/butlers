"""Travel MCP tool registrations.

All ``@mcp.tool()`` closures live here, extracted from the module class
so that ``__init__.py`` stays focused on the Module boilerplate.
"""

from __future__ import annotations

from typing import Any


def register_tools(mcp: Any, module: Any) -> None:
    """Register all travel MCP tools on *mcp*.

    Each closure captures *module* and uses ``module._get_pool()`` to
    obtain the asyncpg pool at call time.
    """

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
