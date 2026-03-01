"""Messenger MCP tool registrations.

All ``@mcp.tool()`` closures live here, extracted from the module class
so that ``__init__.py`` stays focused on the Module boilerplate.
"""

from __future__ import annotations

from typing import Any


def register_tools(mcp: Any, module: Any) -> None:
    """Register all messenger MCP tools on *mcp*.

    Each closure captures *module* and uses ``module._get_pool()`` to
    obtain the asyncpg pool at call time.
    """

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.messenger import delivery as _delivery
    from butlers.tools.messenger import operations as _ops

    # =============================================================
    # Delivery Tracking tools
    # =============================================================

    @mcp.tool()
    async def messenger_delivery_status(
        delivery_id: str,
    ) -> dict[str, Any]:
        """Return the current terminal or in-flight status of a
        single delivery."""
        return await _delivery.messenger_delivery_status(module._get_pool(), delivery_id)

    @mcp.tool()
    async def messenger_delivery_search(
        origin_butler: str | None = None,
        channel: str | None = None,
        intent: str | None = None,
        status: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search delivery history with filters."""
        return await _delivery.messenger_delivery_search(
            module._get_pool(),
            origin_butler=origin_butler,
            channel=channel,
            intent=intent,
            status=status,
            since=since,
            until=until,
            limit=limit,
        )

    @mcp.tool()
    async def messenger_delivery_attempts(
        delivery_id: str,
    ) -> dict[str, Any]:
        """Return the full attempt log for a delivery."""
        return await _delivery.messenger_delivery_attempts(module._get_pool(), delivery_id)

    @mcp.tool()
    async def messenger_delivery_trace(
        request_id: str,
    ) -> dict[str, Any]:
        """Reconstruct full lineage for a request."""
        return await _delivery.messenger_delivery_trace(module._get_pool(), request_id)

    # =============================================================
    # Dead Letter tools
    # =============================================================

    @mcp.tool()
    async def messenger_dead_letter_list(
        channel: str | None = None,
        origin_butler: str | None = None,
        error_class: str | None = None,
        since: str | None = None,
        limit: int = 50,
        include_discarded: bool = False,
    ) -> dict[str, Any]:
        """List dead-lettered deliveries with filters."""
        return await _delivery.messenger_dead_letter_list(
            module._get_pool(),
            channel=channel,
            origin_butler=origin_butler,
            error_class=error_class,
            since=since,
            limit=limit,
            include_discarded=include_discarded,
        )

    @mcp.tool()
    async def messenger_dead_letter_inspect(
        dead_letter_id: str,
    ) -> dict[str, Any]:
        """Return the full dead letter record."""
        return await _delivery.messenger_dead_letter_inspect(module._get_pool(), dead_letter_id)

    @mcp.tool()
    async def messenger_dead_letter_replay(
        dead_letter_id: str,
    ) -> dict[str, Any]:
        """Re-submit a dead-lettered delivery through the standard
        delivery pipeline."""
        return await _delivery.messenger_dead_letter_replay(module._get_pool(), dead_letter_id)

    @mcp.tool()
    async def messenger_dead_letter_discard(
        dead_letter_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Permanently mark a dead letter as discarded."""
        return await _delivery.messenger_dead_letter_discard(
            module._get_pool(), dead_letter_id, reason
        )

    # =============================================================
    # Validation and Dry-Run tools
    # =============================================================

    @mcp.tool()
    async def messenger_validate_notify(
        notify_request: dict[str, Any],
    ) -> dict[str, Any]:
        """Run full validation pipeline without executing delivery."""
        return await _ops.messenger_validate_notify(notify_request)

    @mcp.tool()
    async def messenger_dry_run(
        notify_request: dict[str, Any],
    ) -> dict[str, Any]:
        """Full validation plus target resolution and rate-limit
        headroom check. Does not execute provider call or persist
        anything."""
        # rate_limiter is a runtime infrastructure object; pass None
        # when not available (the tool handles None gracefully).
        return await _ops.messenger_dry_run(notify_request, rate_limiter=None)

    # =============================================================
    # Operational Health tools
    # =============================================================

    @mcp.tool()
    async def messenger_circuit_status(
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Return circuit breaker state per channel/provider."""
        # circuit_breakers is a runtime infrastructure object; pass
        # None when not available (the tool handles None gracefully).
        return await _ops.messenger_circuit_status(circuit_breakers=None, channel=channel)

    @mcp.tool()
    async def messenger_rate_limit_status(
        channel: str | None = None,
        identity_scope: str | None = None,
    ) -> dict[str, Any]:
        """Return current rate-limit headroom per channel and
        identity scope."""
        # rate_limiter is a runtime infrastructure object; pass None
        # when not available (the tool handles None gracefully).
        return await _ops.messenger_rate_limit_status(
            rate_limiter=None,
            channel=channel,
            identity_scope=identity_scope,
        )

    @mcp.tool()
    async def messenger_queue_depth(
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Return count of in-flight deliveries, optionally
        filtered by channel."""
        return await _ops.messenger_queue_depth(module._get_pool(), channel=channel)

    @mcp.tool()
    async def messenger_delivery_stats(
        since: str | None = None,
        until: str | None = None,
        group_by: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate delivery metrics over a time window."""
        return await _ops.messenger_delivery_stats(
            module._get_pool(),
            since=since,
            until=until,
            group_by=group_by,
        )
