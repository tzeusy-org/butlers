"""General MCP tool registrations.

All ``@mcp.tool()`` closures extracted from ``GeneralModule.register_tools``.
"""

from __future__ import annotations

import uuid
from typing import Any


def register_tools(mcp: Any, module: Any) -> None:
    """Register all general MCP tools on *mcp*, using *module* for pool access."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from datetime import UTC, datetime, timedelta

    from butlers import context_bus as _ctx
    from butlers.tools.general import collections as _coll
    from butlers.tools.general import items as _items

    # =============================================================
    # Situational context-bus tools (RFC 0009)
    #
    # Explicit, user-initiated signals (primarily dnd/sick) that no
    # deterministic producer can infer. Writes go through the general butler,
    # which RFC 0009 authorizes for every signal type; permission and
    # vocabulary are enforced by context_bus.set_context.
    # =============================================================

    @mcp.tool()
    async def check_context() -> list[dict[str, Any]]:
        """Return the owner's currently-active situational context signals.

        Deterministic read of the shared context bus (e.g. traveling, sleeping,
        meeting, dnd). Use it before acting to adapt tone or defer non-urgent
        prompts. Returns an empty list when no signals are active.
        """
        signals = await _ctx.get_active_context(module._get_pool())
        return [
            {
                "signal_type": s.signal_type,
                "value": s.value,
                "set_by_butler": s.set_by_butler,
                "set_at": s.set_at.isoformat(),
                "expires_at": s.expires_at.isoformat(),
                "confidence": s.confidence,
            }
            for s in signals
        ]

    @mcp.tool()
    async def set_context(
        signal_type: str,
        value: str | None = None,
        hours: float | None = None,
    ) -> dict[str, Any]:
        """Assert a situational context signal on the owner's behalf.

        For explicit, user-stated context the butlers cannot infer — most
        commonly ``dnd`` (do not disturb) and ``sick``. ``signal_type`` must be
        a valid vocabulary member (see RFC 0009); ``hours`` overrides the
        default TTL (clamped to the per-signal maximum). Confidence is 1.0
        (explicit). Raises on an invalid or unauthorized signal type.
        """
        expires_at = datetime.now(UTC) + timedelta(hours=hours) if hours is not None else None
        await _ctx.set_context(
            module._get_pool(),
            butler_name="general",
            signal_type=signal_type,
            value=value,
            expires_at=expires_at,
            confidence=1.0,
        )
        return {"status": "set", "signal_type": signal_type, "value": value}

    @mcp.tool()
    async def clear_context(signal_type: str) -> dict[str, Any]:
        """Clear a context signal the general butler previously set (e.g. dnd)."""
        await _ctx.clear_context(module._get_pool(), butler_name="general", signal_type=signal_type)
        return {"status": "cleared", "signal_type": signal_type}

    # =============================================================
    # Collection tools
    # =============================================================

    @mcp.tool()
    async def collection_create(name: str, description: str | None = None) -> uuid.UUID:
        """Create a new collection."""
        return await _coll.collection_create(module._get_pool(), name, description=description)

    @mcp.tool()
    async def collection_list() -> list[dict[str, Any]]:
        """List all collections."""
        return await _coll.collection_list(module._get_pool())

    @mcp.tool()
    async def collection_delete(
        collection_id: uuid.UUID,
    ) -> None:
        """Delete a collection and all its items (CASCADE)."""
        await _coll.collection_delete(module._get_pool(), collection_id)

    @mcp.tool()
    async def collection_export(
        collection_name: str,
    ) -> list[dict[str, Any]]:
        """Export all items from a collection as a list of dicts."""
        return await _coll.collection_export(module._get_pool(), collection_name)

    # =============================================================
    # Item tools
    # =============================================================

    @mcp.tool()
    async def item_create(
        collection_name: str,
        data: dict[str, Any],
        tags: list[str] | None = None,
    ) -> uuid.UUID:
        """Create an item in a collection, creating the collection if needed."""
        return await _items.item_create(
            module._get_pool(),
            collection_name,
            data,
            tags=tags,
        )

    @mcp.tool()
    async def item_get(
        item_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Get an item by ID."""
        return await _items.item_get(module._get_pool(), item_id)

    @mcp.tool()
    async def item_update(
        item_id: uuid.UUID,
        data: dict[str, Any],
        tags: list[str] | None = None,
    ) -> None:
        """Update an item with deep merge for data, full replace
        for tags.

        Fetches current data, deep merges in Python, then writes
        back. If tags is provided, it fully replaces the existing
        tags array.
        """
        await _items.item_update(
            module._get_pool(),
            item_id,
            data,
            tags=tags,
        )

    @mcp.tool()
    async def item_search(
        collection_name: str | None = None,
        query: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search items using JSONB containment (@>).

        Optionally filter by collection name, JSONB query, and/or
        tags. Tag filtering uses JSONB containment: each tag must
        be present in the tags array.
        """
        return await _items.item_search(
            module._get_pool(),
            collection_name=collection_name,
            query=query,
            tags=tags,
        )

    @mcp.tool()
    async def item_delete(item_id: uuid.UUID) -> None:
        """Delete an item."""
        await _items.item_delete(module._get_pool(), item_id)
