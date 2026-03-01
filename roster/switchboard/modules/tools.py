"""Switchboard MCP tool registrations.

All ``@mcp.tool()`` closures extracted from the monolithic ``module.py``.
Called by ``SwitchboardModule.register_tools`` via
``register_tools(mcp, module)``.
"""

from __future__ import annotations

import uuid
from typing import Any


def register_tools(mcp: Any, module: Any) -> None:  # noqa: C901
    """Register all switchboard MCP tools as closures over *module*."""

    # Import sub-modules (deferred to avoid import-time side effects)
    from butlers.tools.switchboard.backfill import controls as _backfill_ctl
    from butlers.tools.switchboard.dead_letter import capture as _dl_capture
    from butlers.tools.switchboard.dead_letter import replay as _dl_replay
    from butlers.tools.switchboard.extraction import audit_log as _extraction
    from butlers.tools.switchboard.notification import deliver as _notify
    from butlers.tools.switchboard.operator import controls as _operator
    from butlers.tools.switchboard.registry import registry as _registry
    from butlers.tools.switchboard.routing import route as _route

    # =================================================================
    # Registry tools
    # =================================================================

    @mcp.tool()
    async def list_butlers(routable_only: bool = False) -> list[dict[str, Any]]:
        """List registered butlers, optionally filtered by routing eligibility."""
        return await _registry.list_butlers(module._get_pool(), routable_only=routable_only)

    # =================================================================
    # Routing tools
    # =================================================================

    @mcp.tool()
    async def route(
        target_butler: str,
        tool_name: str,
        args: dict[str, Any],
        source_butler: str = "switchboard",
        allow_stale: bool = False,
        allow_quarantined: bool = False,
    ) -> dict[str, Any]:
        """Route a tool call to a target butler via its MCP endpoint."""
        return await _route.route(
            module._get_pool(),
            target_butler,
            tool_name,
            args,
            source_butler=source_butler,
            allow_stale=allow_stale,
            allow_quarantined=allow_quarantined,
        )

    @mcp.tool()
    async def post_mail(
        target_butler: str,
        sender: str,
        sender_channel: str,
        body: str,
        subject: str | None = None,
        priority: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deliver a message to another butler's mailbox via the Switchboard."""
        return await _route.post_mail(
            module._get_pool(),
            target_butler,
            sender,
            sender_channel,
            body,
            subject=subject,
            priority=priority,
            metadata=metadata,
        )

    # =================================================================
    # Notification delivery tools
    # =================================================================

    @mcp.tool()
    async def deliver(
        channel: str | None = None,
        message: str | None = None,
        recipient: str | None = None,
        metadata: dict[str, Any] | None = None,
        source_butler: str = "switchboard",
        notify_request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deliver a notification through the specified channel."""
        return await _notify.deliver(
            module._get_pool(),
            channel=channel,
            message=message,
            recipient=recipient,
            metadata=metadata,
            source_butler=source_butler,
            notify_request=notify_request,
        )

    # =================================================================
    # Extraction audit tools
    # =================================================================

    @mcp.tool()
    async def log_extraction(
        extraction_type: str,
        tool_name: str,
        tool_args: dict[str, Any],
        target_contact_id: str | None = None,
        confidence: str | None = None,
        source_message_preview: str | None = None,
        source_channel: str | None = None,
    ) -> str:
        """Log an extraction-originated write to the audit log."""
        return await _extraction.log_extraction(
            module._get_pool(),
            extraction_type,
            tool_name,
            tool_args,
            target_contact_id=target_contact_id,
            confidence=confidence,
            source_message_preview=source_message_preview,
            source_channel=source_channel,
        )

    @mcp.tool()
    async def extraction_log_list(
        contact_id: str | None = None,
        extraction_type: str | None = None,
        since: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List extraction log entries with optional filtering."""
        return await _extraction.extraction_log_list(
            module._get_pool(),
            contact_id=contact_id,
            extraction_type=extraction_type,
            since=since,
            limit=limit,
        )

    @mcp.tool()
    async def extraction_log_undo(log_id: str) -> dict[str, Any]:
        """Undo an extraction by reversing the original tool call."""
        return await _extraction.extraction_log_undo(module._get_pool(), log_id)

    # =================================================================
    # Backfill management tools (dashboard-facing)
    # =================================================================

    @mcp.tool()
    async def create_backfill_job(
        connector_type: str,
        endpoint_identity: str,
        target_categories: list[str],
        date_from: str,
        date_to: str,
        rate_limit_per_hour: int = 100,
        daily_cost_cap_cents: int = 500,
    ) -> dict[str, Any]:
        """Create a new backfill job in pending state."""
        return await _backfill_ctl.create_backfill_job(
            module._get_pool(),
            connector_type=connector_type,
            endpoint_identity=endpoint_identity,
            target_categories=target_categories,
            date_from=date_from,
            date_to=date_to,
            rate_limit_per_hour=rate_limit_per_hour,
            daily_cost_cap_cents=daily_cost_cap_cents,
        )

    @mcp.tool()
    async def backfill_pause(job_id: str) -> dict[str, Any]:
        """Pause an active backfill job."""
        return await _backfill_ctl.backfill_pause(module._get_pool(), job_id=job_id)

    @mcp.tool()
    async def backfill_cancel(job_id: str) -> dict[str, Any]:
        """Cancel a backfill job."""
        return await _backfill_ctl.backfill_cancel(module._get_pool(), job_id=job_id)

    @mcp.tool()
    async def backfill_resume(job_id: str) -> dict[str, Any]:
        """Resume a paused or cost-capped backfill job."""
        return await _backfill_ctl.backfill_resume(module._get_pool(), job_id=job_id)

    @mcp.tool()
    async def backfill_list(
        connector_type: str | None = None,
        endpoint_identity: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List backfill jobs with optional filtering."""
        return await _backfill_ctl.backfill_list(
            module._get_pool(),
            connector_type=connector_type,
            endpoint_identity=endpoint_identity,
            status=status,
            limit=limit,
        )

    # =================================================================
    # Operator control tools (conn-based — acquire from pool)
    # =================================================================

    @mcp.tool()
    async def manual_reroute_request(
        request_id: str,
        new_target_butler: str,
        operator_identity: str,
        reason: str,
    ) -> dict[str, Any]:
        """Manually reroute a request to a different butler."""
        async with module._get_pool().acquire() as conn:
            return await _operator.manual_reroute_request(
                conn,
                request_id=uuid.UUID(request_id),
                new_target_butler=new_target_butler,
                operator_identity=operator_identity,
                reason=reason,
            )

    @mcp.tool()
    async def cancel_request(
        request_id: str,
        operator_identity: str,
        reason: str,
    ) -> dict[str, Any]:
        """Cancel an in-flight request (safe cancellation)."""
        async with module._get_pool().acquire() as conn:
            return await _operator.cancel_request(
                conn,
                request_id=uuid.UUID(request_id),
                operator_identity=operator_identity,
                reason=reason,
            )

    @mcp.tool()
    async def abort_request(
        request_id: str,
        operator_identity: str,
        reason: str,
    ) -> dict[str, Any]:
        """Abort an in-flight request (forceful termination)."""
        async with module._get_pool().acquire() as conn:
            return await _operator.abort_request(
                conn,
                request_id=uuid.UUID(request_id),
                operator_identity=operator_identity,
                reason=reason,
            )

    @mcp.tool()
    async def force_complete_request(
        request_id: str,
        operator_identity: str,
        reason: str,
        completion_summary: str,
    ) -> dict[str, Any]:
        """Force-complete a request with explicit operator annotation."""
        async with module._get_pool().acquire() as conn:
            return await _operator.force_complete_request(
                conn,
                request_id=uuid.UUID(request_id),
                operator_identity=operator_identity,
                reason=reason,
                completion_summary=completion_summary,
            )

    # =================================================================
    # Dead-letter queue tools (conn-based — acquire from pool)
    # =================================================================

    @mcp.tool()
    async def replay_dead_letter_request(
        dead_letter_id: str,
        operator_identity: str,
        reason: str,
    ) -> dict[str, Any]:
        """Replay a dead-lettered request with lineage preservation."""
        async with module._get_pool().acquire() as conn:
            return await _dl_replay.replay_dead_letter_request(
                conn,
                dead_letter_id=uuid.UUID(dead_letter_id),
                operator_identity=operator_identity,
                reason=reason,
            )

    @mcp.tool()
    async def list_replay_eligible_requests(
        limit: int = 100,
        failure_category: str | None = None,
    ) -> list[dict[str, Any]]:
        """List dead-letter requests eligible for replay."""
        async with module._get_pool().acquire() as conn:
            return await _dl_replay.list_replay_eligible_requests(
                conn,
                limit=limit,
                failure_category=failure_category,
            )

    @mcp.tool()
    async def get_dead_letter_stats(
        since: str | None = None,
    ) -> dict[str, Any]:
        """Get dead-letter queue statistics."""
        async with module._get_pool().acquire() as conn:
            return await _dl_capture.get_dead_letter_stats(
                conn,
                since=since,
            )
