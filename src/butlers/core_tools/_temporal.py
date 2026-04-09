"""Temporal core tools: deadline_*, event_chain_*, seasonal_period_* (non-STAFFER only)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

from butlers.config import ButlerType
from butlers.core.temporal.deadlines import validate_deadline_input
from butlers.core.temporal.deadlines_db import deadline_create as _deadline_create
from butlers.core.temporal.deadlines_db import deadline_delete as _deadline_delete
from butlers.core.temporal.deadlines_db import deadline_list as _deadline_list
from butlers.core.temporal.deadlines_db import deadline_update as _deadline_update
from butlers.core_tools._base import ToolContext


def register_temporal_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register temporal group tools (non-STAFFER only)."""
    daemon = ctx.daemon
    pool = ctx.pool
    butler_name = ctx.butler_name
    butler_type = ctx.butler_type

    if butler_type != ButlerType.STAFFER:

        @_core_tool("temporal")
        async def deadline_create(
            name: Annotated[str, Field(description="Unique deadline name.")],
            prompt: Annotated[
                str,
                Field(
                    description=(
                        "Prompt dispatched when a threshold fires. "
                        "Should instruct the butler to notify the user about the deadline."
                    )
                ),
            ],
            target_date: Annotated[
                str,
                Field(description="Target due date in YYYY-MM-DD format. Must be in the future."),
            ],
            lead_time_days: Annotated[
                int,
                Field(
                    description=(
                        "Number of days before target_date to begin alerting. "
                        "All alert_thresholds.days_before must be <= lead_time_days."
                    )
                ),
            ],
            alert_thresholds: Annotated[
                list[dict[str, Any]],
                Field(
                    description=(
                        "Non-empty list of threshold dicts: "
                        '[{"days_before": int, "severity": "info|warning|critical"}, ...]. '
                        "Each days_before must be <= lead_time_days."
                    )
                ),
            ],
            depends_on: Annotated[
                list[str] | None,
                Field(
                    description=(
                        "Optional list of deadline task UUIDs that must reach 'completed' "
                        "status before this deadline's thresholds are evaluated."
                    )
                ),
            ] = None,
        ) -> dict:
            """Create a countdown-based deadline task.

            Deadlines alert the butler at configurable thresholds before a target date
            (e.g., 6 weeks, 2 weeks, 3 days before). Unlike cron schedules, deadlines
            count down from a fixed target date and fire once per threshold crossing.

            Returns the new deadline's UUID and creation status.
            """
            try:
                from datetime import date as _date

                parsed_date = _date.fromisoformat(target_date)
                validate_deadline_input(
                    target_date=parsed_date,
                    lead_time_days=lead_time_days,
                    alert_thresholds=alert_thresholds,
                )
                task_id = await _deadline_create(
                    pool,
                    name=name,
                    prompt=prompt,
                    target_date=parsed_date,
                    lead_time_days=lead_time_days,
                    alert_thresholds=alert_thresholds,
                    depends_on=depends_on,
                )
                return {
                    "id": str(task_id),
                    "status": "created",
                    "name": name,
                    "target_date": target_date,
                    "lead_time_days": lead_time_days,
                    "alert_thresholds": alert_thresholds,
                    "depends_on": depends_on,
                }
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def deadline_update(
            task_id: Annotated[str, Field(description="UUID of the deadline task to update.")],
            name: Annotated[str | None, Field(description="New name (optional).")] = None,
            prompt: Annotated[
                str | None,
                Field(description="New prompt template (optional)."),
            ] = None,
            target_date: Annotated[
                str | None,
                Field(
                    description=(
                        "New target date in YYYY-MM-DD format (optional). "
                        "Changing target_date resets fired_thresholds and "
                        "deadline_status to 'pending'."
                    )
                ),
            ] = None,
            lead_time_days: Annotated[
                int | None,
                Field(description="New lead time in days (optional)."),
            ] = None,
            alert_thresholds: Annotated[
                list[dict[str, Any]] | None,
                Field(description="New alert thresholds list (optional)."),
            ] = None,
            depends_on: Annotated[
                list[str] | None,
                Field(description="New dependency task UUID list (optional)."),
            ] = None,
            deadline_status: Annotated[
                str | None,
                Field(
                    description=(
                        "Explicit new status (optional). "
                        "Valid values: pending, alerted, escalated, completed, expired."
                    )
                ),
            ] = None,
            enabled: Annotated[
                bool | None,
                Field(description="Enable or disable the deadline (optional)."),
            ] = None,
        ) -> dict:
            """Update fields on an existing deadline task.

            Only provided fields are changed. Changing target_date automatically resets
            fired_thresholds to [] and deadline_status to 'pending' (unless
            deadline_status is explicitly provided).
            """
            try:
                from datetime import UTC as _UTC
                from datetime import date as _date
                from datetime import datetime as _datetime

                parsed_date: _date | None = None
                if target_date is not None:
                    parsed_date = _date.fromisoformat(target_date)
                    today = _datetime.now(_UTC).date()
                    if parsed_date <= today:
                        raise ValueError(
                            f"target_date must be in the future"
                            f" (got {parsed_date}; today is {today})"
                        )

                if lead_time_days is not None and lead_time_days <= 0:
                    raise ValueError(
                        f"lead_time_days must be a positive integer (got {lead_time_days})"
                    )

                if alert_thresholds is not None and not alert_thresholds:
                    raise ValueError("alert_thresholds must contain at least one threshold")

                if alert_thresholds is not None and lead_time_days is not None:
                    for t in alert_thresholds:
                        days_before = t.get("days_before")
                        if days_before is None:
                            raise ValueError(
                                "Each threshold must have a 'days_before' integer field"
                            )
                        if days_before > lead_time_days:
                            raise ValueError(
                                f"Threshold days_before={days_before} cannot"
                                f" exceed lead_time_days={lead_time_days}"
                            )

                await _deadline_update(
                    pool,
                    uuid.UUID(task_id),
                    name=name,
                    prompt=prompt,
                    target_date=parsed_date,
                    lead_time_days=lead_time_days,
                    alert_thresholds=alert_thresholds,
                    depends_on=depends_on,
                    deadline_status=deadline_status,
                    enabled=enabled,
                )
                return {
                    "id": task_id,
                    "status": "updated",
                }
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def deadline_list(
            status_filter: Annotated[
                str | None,
                Field(
                    description=(
                        "Optional status filter. "
                        "Valid values: pending, alerted, escalated, completed, expired. "
                        "Omit to list all deadlines."
                    )
                ),
            ] = None,
        ) -> list[dict]:
            """List all deadline tasks, optionally filtered by status.

            Returns deadlines sorted by target_date (soonest first).
            """
            deadlines = await _deadline_list(pool, status=status_filter)
            return deadlines

        @_core_tool("temporal")
        async def deadline_delete(
            task_id: Annotated[str, Field(description="UUID of the deadline task to delete.")],
        ) -> dict:
            """Delete a runtime deadline task.

            TOML-sourced deadlines cannot be deleted via this tool — remove them
            from butler.toml instead.
            """
            try:
                await _deadline_delete(pool, uuid.UUID(task_id))
                return {"id": task_id, "status": "deleted"}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def event_chain_create(
            name: str,
            trigger_type: str,
            actions: list[dict[str, Any]],
            trigger_reference: str | None = None,
        ) -> dict:
            """Create a new event chain.

            An event chain defines an automated sequence of actions that fires
            when a trigger event occurs (calendar event end, deadline expiry, or
            deadline threshold alert).

            Args:
                name: Unique name for this chain (scoped to this butler).
                trigger_type: When to fire. One of:
                    - 'calendar_event_end': fires when a calendar event ends.
                    - 'deadline_passed': fires when a deadline task expires/completes.
                    - 'deadline_threshold': fires when a deadline alert threshold fires.
                actions: Ordered list of action dicts. Each action must have:
                    - action_type: 'prompt' or 'job'
                    - delay_minutes: non-negative integer (cumulative offset from trigger)
                    - For prompt actions: 'prompt' (non-empty string)
                    - For job actions: 'job_name' (non-empty string);
                        optionally 'job_args' (dict)
                trigger_reference: Optional event_id or task_id this chain fires for.
                    When omitted, the chain fires for all events of the given type.

            Returns:
                The created event chain record.
            """
            from butlers.core.temporal.event_chains_db import event_chain_create as _ec_create

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                chain = await _ec_create(
                    _db_pool,
                    name=name,
                    trigger_type=trigger_type,
                    actions=actions,
                    butler_name=butler_name,
                    trigger_reference=trigger_reference,
                )
                return {"status": "created", "chain": chain}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def event_chain_update(
            chain_id: str,
            name: str | None = None,
            trigger_type: str | None = None,
            trigger_reference: str | None = None,
            actions: list[dict[str, Any]] | None = None,
            status: str | None = None,
        ) -> dict:
            """Update fields on an existing event chain.

            When *actions* is updated, status is automatically reset to 'active'
            (re-arms the chain) unless *status* is explicitly provided.

            Args:
                chain_id: UUID of the event chain to update.
                name: New name (optional).
                trigger_type: New trigger_type (optional).
                trigger_reference: New trigger_reference (optional; pass empty string
                    to clear the reference so the chain fires for all events).
                actions: New actions array (optional). Updating actions resets
                    status to 'active' so the chain can fire again.
                status: Explicit status override: 'active' | 'fired' | 'disabled'.
                    Use 'active' to re-arm a fired chain, 'disabled' to pause it.

            Returns:
                The updated event chain record.
            """
            from butlers.core.temporal.event_chains_db import event_chain_update as _ec_update

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                chain = await _ec_update(
                    _db_pool,
                    chain_id,
                    butler_name=butler_name,
                    name=name,
                    trigger_type=trigger_type,
                    trigger_reference=trigger_reference,
                    actions=actions,
                    status=status,
                )
                return {"status": "updated", "chain": chain}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def event_chain_list(
            trigger_type: str | None = None,
            status: str | None = None,
            limit: int = 100,
        ) -> dict:
            """List event chains for this butler.

            Args:
                trigger_type: Optional filter. One of: calendar_event_end |
                    deadline_passed | deadline_threshold. If omitted, all
                    trigger types are returned.
                status: Optional filter. One of: active | fired | disabled.
                    If omitted, all statuses are returned.
                limit: Maximum number of rows to return (default 100).

            Returns:
                Dict with 'chains' list ordered by created_at ascending.
            """
            from butlers.core.temporal.event_chains_db import event_chain_list as _ec_list

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                chains = await _ec_list(
                    _db_pool,
                    butler_name,
                    trigger_type=trigger_type,
                    status=status,
                    limit=limit,
                )
                return {"chains": chains, "count": len(chains)}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def event_chain_delete(chain_id: str) -> dict:
            """Delete an event chain.

            Args:
                chain_id: UUID of the event chain to delete.

            Returns:
                Dict with 'found' boolean.
            """
            from butlers.core.temporal.event_chains_db import event_chain_delete as _ec_delete

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                found = await _ec_delete(_db_pool, chain_id, butler_name=butler_name)
                return {"found": found, "status": "deleted" if found else "not_found"}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def seasonal_period_create(
            name: str,
            period_type: str = "annual",
            start_month: int = 1,
            start_day: int = 1,
            end_month: int = 12,
            end_day: int = 31,
            timezone: str = "UTC",
            metadata: dict[str, Any] | None = None,
            enabled: bool = True,
        ) -> dict[str, Any]:
            """Create a new seasonal period for this butler.

            Seasonal periods define recurring calendar windows (e.g., tax season,
            academic terms) that inject context into task dispatch prompts.

            Args:
                name: Unique name for this period (per butler).
                period_type: One of 'annual', 'academic', 'fiscal', 'custom'.
                start_month: Period start month (1-12).
                start_day: Period start day (1-31).
                end_month: Period end month (1-12).
                end_day: Period end day (1-31).
                timezone: IANA timezone string (default 'UTC').
                metadata: Optional dict with context hints and priority modifiers.
                enabled: Whether the period is active immediately (default true).

            Returns:
                Dict with 'id' (UUID string) of the created period.
            """
            from butlers.core.seasonal import seasonal_period_create as _sp_create

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                period_id = await _sp_create(
                    _db_pool,
                    butler_name,
                    name=name,
                    period_type=period_type,
                    start_month=start_month,
                    start_day=start_day,
                    end_month=end_month,
                    end_day=end_day,
                    timezone=timezone,
                    metadata=metadata,
                    enabled=enabled,
                )
                return {"id": str(period_id), "status": "created"}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def seasonal_period_update(
            period_id: str,
            name: str | None = None,
            period_type: str | None = None,
            start_month: int | None = None,
            start_day: int | None = None,
            end_month: int | None = None,
            end_day: int | None = None,
            timezone: str | None = None,
            metadata: dict[str, Any] | None = None,
            enabled: bool | None = None,
        ) -> dict[str, Any]:
            """Update an existing seasonal period.

            Only provided (non-null) fields are updated.  Month/day combinations
            are validated against the resulting final state.

            Args:
                period_id: UUID of the period to update.
                name: New name (must be unique per butler).
                period_type: New type ('annual', 'academic', 'fiscal', 'custom').
                start_month: New start month (1-12).
                start_day: New start day (1-31).
                end_month: New end month (1-12).
                end_day: New end day (1-31).
                timezone: New IANA timezone string.
                metadata: New metadata dict (replaces existing).
                enabled: New enabled flag.

            Returns:
                Dict with 'found' boolean.
            """
            from butlers.core.seasonal import seasonal_period_update as _sp_update

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                found = await _sp_update(
                    _db_pool,
                    butler_name,
                    period_id=period_id,
                    name=name,
                    period_type=period_type,
                    start_month=start_month,
                    start_day=start_day,
                    end_month=end_month,
                    end_day=end_day,
                    timezone=timezone,
                    metadata=metadata,
                    enabled=enabled,
                )
                return {"found": found, "status": "updated" if found else "not_found"}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def seasonal_period_list(
            include_disabled: bool = True,
        ) -> dict[str, Any]:
            """List all seasonal periods for this butler.

            Returns all seasonal periods with their current active status
            (whether today's date falls within each period's range).

            Args:
                include_disabled: If False, only return enabled periods
                    (default True — return all).

            Returns:
                Dict with 'periods' list, each entry including 'is_active' field.
            """
            from butlers.core.seasonal import seasonal_period_list as _sp_list

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                periods = await _sp_list(_db_pool, butler_name)
                if not include_disabled:
                    periods = [p for p in periods if p.get("enabled")]
                return {"periods": periods, "count": len(periods)}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def seasonal_period_delete(
            period_id: str,
        ) -> dict[str, Any]:
            """Delete a seasonal period.

            Args:
                period_id: UUID of the period to delete.

            Returns:
                Dict with 'found' boolean.
            """
            from butlers.core.seasonal import seasonal_period_delete as _sp_delete

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                found = await _sp_delete(_db_pool, butler_name, period_id=period_id)
                return {"found": found, "status": "deleted" if found else "not_found"}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}

        @_core_tool("temporal")
        async def seasonal_period_create_preset(
            preset: str,
            timezone: str = "UTC",
        ) -> dict[str, Any]:
            """Create a seasonal period from a built-in preset.

            Available presets:
            - 'us-tax-season': Jan 1 - Apr 15 (US tax filing season)
            - 'year-end-holidays': Dec 15 - Jan 5 (year-end holiday season)
            - 'back-to-school': Aug 1 - Sep 15 (back-to-school season)
            - 'spring-semester': Jan 15 - May 15 (spring academic semester)
            - 'fall-semester': Aug 25 - Dec 15 (fall academic semester)

            Args:
                preset: Preset name (e.g., 'us-tax-season').
                timezone: IANA timezone string (default 'UTC').

            Returns:
                Dict with 'id' (UUID string) of the created period.
            """
            from butlers.core.seasonal import seasonal_period_create_preset as _sp_preset

            _db_pool = daemon.db.pool if daemon.db is not None else None
            if _db_pool is None:
                return {"status": "error", "error": "Database not available."}
            try:
                period_id = await _sp_preset(
                    _db_pool, butler_name, preset=preset, timezone=timezone
                )
                return {"id": str(period_id), "status": "created", "preset": preset}
            except ValueError as exc:
                return {"status": "error", "error": str(exc)}
