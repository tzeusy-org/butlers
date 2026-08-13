"""General MCP tool registrations.

All ``@mcp.tool()`` closures extracted from ``GeneralModule.register_tools``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastmcp import Context

_DND_ACTION_NAMESPACE = uuid.UUID("6c2b0e6a-0aa5-4eaa-9adf-3c6ed29c9a0f")


def _ambient_dnd_action_identity(ctx: Context | None) -> uuid.UUID | None:
    """Derive a stable DND action UUID from the MCP request, never its content.

    FastMCP's originating request ID is stable for an exact transport retry and
    is unique per tool action.  The durable boundary receives only the derived
    UUID, never this request ID or user-controlled tool text.
    """
    if ctx is None:
        return None
    try:
        request_id = ctx.origin_request_id
    except RuntimeError:
        return None
    if not isinstance(request_id, str) or not request_id.strip():
        return None
    normalized_request_id = request_id.strip()
    return uuid.uuid5(_DND_ACTION_NAMESPACE, f"general.context.dnd.v1:{normalized_request_id}")


def _dnd_receipt_payload(receipt: Any) -> dict[str, Any]:
    """Expose durable DND ordering evidence without raw DND audit content."""
    return {
        "mutation_id": str(receipt.mutation_id),
        "generation": receipt.generation,
        "writer": receipt.writer,
        "operation": receipt.operation,
        "correlation_id": receipt.correlation_id,
        "requested_expires_at": (
            receipt.requested_expires_at.isoformat()
            if receipt.requested_expires_at is not None
            else None
        ),
        "effective_expires_at": (
            receipt.effective_expires_at.isoformat()
            if receipt.effective_expires_at is not None
            else None
        ),
        "committed_at": receipt.committed_at.isoformat(),
    }


def register_tools(mcp: Any, module: Any) -> None:
    """Register all general MCP tools on *mcp*, using *module* for pool access."""

    # Import sub-modules (deferred to avoid import-time side effects)
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
        requested_expires_at: datetime | None = None,
        mutation_id: uuid.UUID | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Assert a situational context signal on the owner's behalf.

        For explicit, user-stated context the butlers cannot infer — most
        commonly ``dnd`` (do not disturb) and ``sick``. ``signal_type`` must be
        a valid vocabulary member (see RFC 0009); ``hours`` overrides the
        default TTL (clamped to the per-signal maximum) for non-DND signals.
        DND uses its database default when no expiry is supplied. For a custom
        DND TTL, pass a stable absolute ``requested_expires_at`` from the
        routed action and carry it unchanged on retry; relative ``hours`` would
        silently regenerate an expiry on retry and is rejected. Confidence is
        1.0 (explicit). DND requires a stable opaque UUID ``mutation_id`` for
        replay; when omitted, it is derived from the injected MCP request
        context. The durable correlation is derived only from that UUID, so
        callers cannot put user text into its receipt/audit. Raises on an
        invalid or unauthorized signal type.
        """
        if signal_type == "dnd":
            if hours is not None:
                raise ValueError(
                    "DND custom TTL requires stable requested_expires_at, not relative hours"
                )
            expires_at = requested_expires_at
        else:
            if requested_expires_at is not None:
                raise ValueError("requested_expires_at is only valid for DND")
            expires_at = datetime.now(UTC) + timedelta(hours=hours) if hours is not None else None
        set_kwargs: dict[str, Any] = {
            "butler_name": "general",
            "signal_type": signal_type,
            "value": value,
            "expires_at": expires_at,
            "confidence": 1.0,
        }
        if signal_type == "dnd":
            mutation_id = mutation_id or _ambient_dnd_action_identity(ctx)
            if mutation_id is None:
                raise ValueError(
                    "DND requires a stable opaque mutation_id or originating MCP request"
                )
            set_kwargs.update(mutation_id=mutation_id)
        receipt = await _ctx.set_context(module._get_pool(), **set_kwargs)
        result: dict[str, Any] = {"status": "set", "signal_type": signal_type}
        if signal_type != "dnd":
            result["value"] = value
        if signal_type == "dnd" and isinstance(receipt, _ctx.DndMutationReceipt):
            result["mutation"] = _dnd_receipt_payload(receipt)
        return result

    @mcp.tool()
    async def clear_context(
        signal_type: str,
        mutation_id: uuid.UUID | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Clear a context signal the general butler previously set (e.g. dnd).

        DND requires a stable opaque UUID ``mutation_id`` for replay; when it
        is omitted, it is derived from the injected MCP request context. The
        durable correlation is always derived from that UUID.
        """
        clear_kwargs: dict[str, Any] = {
            "butler_name": "general",
            "signal_type": signal_type,
        }
        if signal_type == "dnd":
            mutation_id = mutation_id or _ambient_dnd_action_identity(ctx)
            if mutation_id is None:
                raise ValueError(
                    "DND requires a stable opaque mutation_id or originating MCP request"
                )
            clear_kwargs.update(mutation_id=mutation_id)
        receipt = await _ctx.clear_context(module._get_pool(), **clear_kwargs)
        result: dict[str, Any] = {"status": "cleared", "signal_type": signal_type}
        if signal_type == "dnd" and isinstance(receipt, _ctx.DndMutationReceipt):
            result["mutation"] = _dnd_receipt_payload(receipt)
        return result

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
