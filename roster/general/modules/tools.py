"""General MCP tool registrations.

All ``@mcp.tool()`` closures extracted from ``GeneralModule.register_tools``.
"""

from __future__ import annotations

import uuid
from typing import Any


def register_tools(mcp: Any, module: Any) -> None:
    """Register all general MCP tools on *mcp*, using *module* for pool access."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.general import collections as _coll
    from butlers.tools.general import entities as _ent

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
        """Delete a collection and all its entities (CASCADE)."""
        await _coll.collection_delete(module._get_pool(), collection_id)

    @mcp.tool()
    async def collection_export(
        collection_name: str,
    ) -> list[dict[str, Any]]:
        """Export all entities from a collection as a list of dicts."""
        return await _coll.collection_export(module._get_pool(), collection_name)

    # =============================================================
    # Entity tools
    # =============================================================

    @mcp.tool()
    async def entity_create(
        collection_name: str,
        data: dict[str, Any],
        tags: list[str] | None = None,
    ) -> uuid.UUID:
        """Create an entity in a collection (by collection name).

        Raises ValueError if collection not found.
        """
        return await _ent.entity_create(
            module._get_pool(),
            collection_name,
            data,
            tags=tags,
        )

    @mcp.tool()
    async def entity_get(
        entity_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Get an entity by ID."""
        return await _ent.entity_get(module._get_pool(), entity_id)

    @mcp.tool()
    async def entity_update(
        entity_id: uuid.UUID,
        data: dict[str, Any],
        tags: list[str] | None = None,
    ) -> None:
        """Update an entity with deep merge for data, full replace
        for tags.

        Fetches current data, deep merges in Python, then writes
        back. If tags is provided, it fully replaces the existing
        tags array.
        """
        await _ent.entity_update(
            module._get_pool(),
            entity_id,
            data,
            tags=tags,
        )

    @mcp.tool()
    async def entity_search(
        collection_name: str | None = None,
        query: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search entities using JSONB containment (@>).

        Optionally filter by collection name, JSONB query, and/or
        tags. Tag filtering uses JSONB containment: each tag must
        be present in the tags array.
        """
        return await _ent.entity_search(
            module._get_pool(),
            collection_name=collection_name,
            query=query,
            tags=tags,
        )

    @mcp.tool()
    async def entity_delete(entity_id: uuid.UUID) -> None:
        """Delete an entity."""
        await _ent.entity_delete(module._get_pool(), entity_id)
