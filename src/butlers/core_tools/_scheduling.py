"""Scheduling core tools: schedule_list, schedule_create, schedule_update,
schedule_delete, schedule_trigger (non-STAFFER only), schedule_costs."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from butlers.config import ButlerType
from butlers.core.scheduler import _parse_complexity_from_db_row
from butlers.core.scheduler import schedule_create as _schedule_create
from butlers.core.scheduler import schedule_delete as _schedule_delete
from butlers.core.scheduler import schedule_list as _schedule_list
from butlers.core.scheduler import schedule_update as _schedule_update
from butlers.core.sessions import schedule_costs as _schedule_costs
from butlers.core_tools._base import ToolContext


def _resolve_schedule_tool_id(
    task_id: str | None,
    legacy_id: str | None,
    tool_name: str,
) -> str:
    """Accept both task_id and legacy id fields for MCP compatibility."""
    if task_id and legacy_id and task_id != legacy_id:
        raise ValueError(f"{tool_name} received both task_id and id with different values")
    resolved = task_id or legacy_id
    if resolved is None:
        raise ValueError(f"{tool_name} requires task_id or id")
    return resolved


# Imported in notify tool - re-exported here for use by _notifications.py
# (the type alias must match what daemon.py defines)
try:
    from butlers.tools.switchboard.routing.contracts import parse_notify_request  # noqa: F401
except ImportError:
    pass


def register_scheduling_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register scheduling group tools."""
    daemon = ctx.daemon
    pool = ctx.pool
    butler_type = ctx.butler_type

    @_core_tool("scheduling")
    async def schedule_list() -> list[dict]:
        """List all scheduled tasks."""
        tasks = await _schedule_list(pool)
        for t in tasks:
            t["id"] = str(t["id"])
            if t.get("calendar_event_id") is not None:
                t["calendar_event_id"] = str(t["calendar_event_id"])
        return tasks

    @_core_tool("scheduling")
    async def schedule_create(
        name: str,
        cron: str | None = None,
        prompt: str | None = None,
        task_type: str = "cron",
        dispatch_mode: str = "prompt",
        job_name: str | None = None,
        job_args: dict[str, Any] | None = None,
        timezone: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        until_at: datetime | None = None,
        display_title: str | None = None,
        calendar_event_id: str | None = None,
        target_date: str | None = None,
        lead_time_days: int | None = None,
        alert_thresholds: list[dict[str, Any]] | None = None,
        depends_on: list[str] | None = None,
    ) -> dict:
        """Create a new runtime scheduled task (cron or deadline).

        For cron tasks, ``cron`` is required. For deadline tasks, set
        ``task_type='deadline'`` and provide ``target_date`` (YYYY-MM-DD),
        ``lead_time_days``, and ``alert_thresholds`` instead.
        """
        try:
            create_kwargs: dict[str, Any] = {
                "task_type": task_type,
                "dispatch_mode": dispatch_mode,
                "job_name": job_name,
                "job_args": job_args,
                "stagger_key": daemon.config.name,
            }
            if timezone is not None:
                create_kwargs["timezone"] = timezone
            if start_at is not None:
                create_kwargs["start_at"] = start_at
            if end_at is not None:
                create_kwargs["end_at"] = end_at
            if until_at is not None:
                create_kwargs["until_at"] = until_at
            if display_title is not None:
                create_kwargs["display_title"] = display_title
            if calendar_event_id is not None:
                create_kwargs["calendar_event_id"] = calendar_event_id
            if target_date is not None:
                create_kwargs["target_date"] = target_date
            if lead_time_days is not None:
                create_kwargs["lead_time_days"] = lead_time_days
            if alert_thresholds is not None:
                create_kwargs["alert_thresholds"] = alert_thresholds
            if depends_on is not None:
                create_kwargs["depends_on"] = depends_on
            task_id = await _schedule_create(
                pool,
                name,
                cron,
                prompt,
                **create_kwargs,
            )
            result: dict[str, Any] = {
                "id": str(task_id),
                "status": "created",
                "task_type": task_type,
            }
            if task_type == "deadline":
                result.update(
                    {
                        "name": name,
                        "target_date": target_date,
                        "lead_time_days": lead_time_days,
                        "alert_thresholds": alert_thresholds,
                        "depends_on": depends_on,
                    }
                )
            else:
                result.update(
                    {
                        "dispatch_mode": dispatch_mode,
                        "prompt": prompt,
                        "job_name": job_name,
                        "job_args": job_args,
                        "timezone": timezone,
                        "start_at": start_at.isoformat() if start_at else None,
                        "end_at": end_at.isoformat() if end_at else None,
                        "until_at": until_at.isoformat() if until_at else None,
                        "display_title": display_title,
                        "calendar_event_id": calendar_event_id,
                    }
                )
            return result
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

    @_core_tool("scheduling")
    async def schedule_update(
        task_id: str | None = None,
        id: str | None = None,
        name: str | None = None,
        cron: str | None = None,
        dispatch_mode: str | None = None,
        prompt: str | None = None,
        job_name: str | None = None,
        job_args: dict[str, Any] | None = None,
        enabled: bool | None = None,
        timezone: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        until_at: datetime | None = None,
        display_title: str | None = None,
        calendar_event_id: str | None = None,
    ) -> dict:
        """Update a scheduled task. Only provided fields are changed."""
        resolved_id = _resolve_schedule_tool_id(task_id, id, "schedule_update")
        update_fields = {
            "name": name,
            "cron": cron,
            "dispatch_mode": dispatch_mode,
            "prompt": prompt,
            "job_name": job_name,
            "job_args": job_args,
            "enabled": enabled,
            "timezone": timezone,
            "start_at": start_at,
            "end_at": end_at,
            "until_at": until_at,
            "display_title": display_title,
            "calendar_event_id": calendar_event_id,
        }
        fields = {k: v for k, v in update_fields.items() if v is not None}
        await _schedule_update(
            pool,
            uuid.UUID(resolved_id),
            stagger_key=daemon.config.name,
            **fields,
        )
        return {
            "id": resolved_id,
            "status": "updated",
            "dispatch_mode": dispatch_mode,
            "prompt": prompt,
            "job_name": job_name,
            "job_args": job_args,
            "timezone": timezone,
            "start_at": start_at.isoformat() if start_at else None,
            "end_at": end_at.isoformat() if end_at else None,
            "until_at": until_at.isoformat() if until_at else None,
            "display_title": display_title,
            "calendar_event_id": calendar_event_id,
        }

    @_core_tool("scheduling")
    async def schedule_delete(task_id: str | None = None, id: str | None = None) -> dict:
        """Delete a runtime scheduled task."""
        resolved_id = _resolve_schedule_tool_id(task_id, id, "schedule_delete")
        await _schedule_delete(pool, uuid.UUID(resolved_id))
        return {"id": resolved_id, "status": "deleted"}

    # Non-STAFFER only tools
    if butler_type != ButlerType.STAFFER:

        @_core_tool("scheduling")
        async def schedule_trigger(task_id: str | None = None, id: str | None = None) -> dict:
            """Trigger a scheduled task immediately (one-off dispatch).

            Dispatches the task via the same mechanism as the scheduler tick
            but does NOT advance next_run_at — this is a manual one-off run.
            Updates last_run_at and last_result.
            """
            resolved_id = _resolve_schedule_tool_id(task_id, id, "schedule_trigger")
            task_uuid = uuid.UUID(resolved_id)

            row = await pool.fetchrow(
                "SELECT id, name, dispatch_mode, prompt, job_name, job_args, complexity "
                "FROM scheduled_tasks WHERE id = $1",
                task_uuid,
            )
            if row is None:
                return {"id": resolved_id, "status": "error", "error": "Schedule not found"}

            name = row["name"]
            dispatch_mode = row["dispatch_mode"] or "prompt"
            prompt = row["prompt"]
            job_name = row["job_name"]
            raw_job_args = row["job_args"]
            job_args = json.loads(raw_job_args) if isinstance(raw_job_args, str) else raw_job_args
            task_complexity = _parse_complexity_from_db_row(row, name)

            now = datetime.now(UTC)
            try:
                if dispatch_mode == "job":
                    result = await daemon._dispatch_scheduled_task(
                        trigger_source=f"schedule:{name}",
                        job_name=job_name,
                        job_args=job_args,
                    )
                else:
                    result = await daemon._dispatch_scheduled_task(
                        trigger_source=f"schedule:{name}",
                        prompt=prompt,
                        complexity=task_complexity,
                    )

                # Serialize dispatch result for JSONB storage
                if result is None:
                    result_json = None
                elif hasattr(result, "__dict__") and not isinstance(result, type):
                    result_json = json.dumps(result.__dict__, default=str)
                elif isinstance(result, dict):
                    result_json = json.dumps(result, default=str)
                else:
                    result_json = json.dumps({"result": str(result)}, default=str)
                await pool.execute(
                    "UPDATE scheduled_tasks "
                    "SET last_run_at = $2, last_result = $3::jsonb, updated_at = now() "
                    "WHERE id = $1",
                    task_uuid,
                    now,
                    result_json,
                )
                return {"id": resolved_id, "status": "triggered", "name": name}
            except Exception as exc:
                import logging

                logging.getLogger(__name__).exception("Manual trigger failed for schedule %s", name)
                error_json = json.dumps({"error": str(exc)})
                await pool.execute(
                    "UPDATE scheduled_tasks "
                    "SET last_run_at = $2, last_result = $3::jsonb, updated_at = now() "
                    "WHERE id = $1",
                    task_uuid,
                    now,
                    error_json,
                )
                return {"id": resolved_id, "status": "error", "error": str(exc)}

        @_core_tool("scheduling")
        async def schedule_costs() -> dict:
            """Return per-schedule token usage aggregates."""
            return await _schedule_costs(pool)
