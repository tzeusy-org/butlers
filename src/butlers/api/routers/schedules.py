"""Schedule CRUD endpoints for butler scheduled tasks.

Provides:

- ``router`` — butler-scoped schedule endpoints at
  ``/api/butlers/{name}/schedules``

Read operations query the butler's database directly via ``DatabaseManager``.
Write operations (create, update, delete, toggle) are proxied through MCP
tool calls to the butler daemon, preserving the architectural constraint that
only the butler itself mutates its own database.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, MCPClientManager, get_mcp_manager
from butlers.api.models import ApiResponse
from butlers.api.models.schedule import Schedule, ScheduleCreate, ScheduleUpdate
from butlers.api.routers.audit import log_audit_entry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "schedules"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEDULE_COLUMNS = (
    "id, name, cron, dispatch_mode, prompt, job_name, job_args, "
    "timezone, start_at, end_at, until_at, display_title, calendar_event_id, "
    "source, enabled, next_run_at, last_run_at, created_at, updated_at"
)
_SCHEDULE_COLUMNS_WITHOUT_LINKAGE = (
    "id, name, cron, dispatch_mode, prompt, job_name, job_args, "
    "source, enabled, next_run_at, last_run_at, created_at, updated_at"
)
_SCHEDULE_COLUMNS_LEGACY = (
    "id, name, cron, prompt, source, enabled, next_run_at, last_run_at, created_at, updated_at"
)
_DISPATCH_MODE_PROMPT = "prompt"
_DISPATCH_MODE_JOB = "job"


def _row_value(row, key: str, default=None):
    """Read a mapping key with compatibility for asyncpg Record and plain dict."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _normalize_job_args(value):
    """Normalize JSONB-like job args to a dict-or-null."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Ignoring malformed schedule.job_args payload")
            return None
        if isinstance(decoded, dict):
            return decoded
    logger.warning("Ignoring non-object schedule.job_args payload type=%s", type(value).__name__)
    return None


def _row_to_schedule(row) -> Schedule:
    """Convert an asyncpg Record to a Schedule model."""
    raw_mode = _row_value(row, "dispatch_mode", _DISPATCH_MODE_PROMPT)
    dispatch_mode = (
        str(raw_mode).strip().lower()
        if str(raw_mode).strip().lower() in {_DISPATCH_MODE_PROMPT, _DISPATCH_MODE_JOB}
        else _DISPATCH_MODE_PROMPT
    )
    return Schedule(
        id=row["id"],
        name=row["name"],
        cron=row["cron"],
        dispatch_mode=dispatch_mode,
        prompt=_row_value(row, "prompt"),
        job_name=_row_value(row, "job_name"),
        job_args=_normalize_job_args(_row_value(row, "job_args")),
        timezone=_row_value(row, "timezone"),
        start_at=_row_value(row, "start_at"),
        end_at=_row_value(row, "end_at"),
        until_at=_row_value(row, "until_at"),
        display_title=_row_value(row, "display_title"),
        calendar_event_id=_row_value(row, "calendar_event_id"),
        source=_row_value(row, "source", "db"),
        enabled=bool(_row_value(row, "enabled", True)),
        next_run_at=_row_value(row, "next_run_at"),
        last_run_at=_row_value(row, "last_run_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _call_mcp_tool(
    mgr: MCPClientManager,
    butler_name: str,
    tool_name: str,
    arguments: dict,
) -> dict:
    """Call an MCP tool on a butler, returning the parsed result.

    Raises HTTPException(503) if the butler is unreachable or the call fails.
    """
    try:
        client = await mgr.get_client(butler_name)
        result = await client.call_tool(tool_name, arguments)
    except ButlerUnreachableError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{butler_name}' is unreachable: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"MCP call to '{butler_name}' failed: {exc}",
        ) from exc

    # FastMCP call_tool returns a list of content blocks; extract text
    if result and hasattr(result, "__iter__"):
        for block in result:
            text = getattr(block, "text", None)
            if text:
                try:
                    return json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    return {"result": text}
    return {"result": str(result)}


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/schedules — list schedules
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/schedules",
    response_model=ApiResponse[list[Schedule]],
)
async def list_schedules(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[Schedule]]:
    """Return all scheduled tasks for a single butler."""
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        )

    try:
        rows = await pool.fetch(
            f"SELECT {_SCHEDULE_COLUMNS} FROM scheduled_tasks ORDER BY created_at"
        )
    except asyncpg.UndefinedColumnError:
        try:
            rows = await pool.fetch(
                f"SELECT {_SCHEDULE_COLUMNS_WITHOUT_LINKAGE} "
                "FROM scheduled_tasks ORDER BY created_at"
            )
        except asyncpg.UndefinedColumnError:
            rows = await pool.fetch(
                f"SELECT {_SCHEDULE_COLUMNS_LEGACY} FROM scheduled_tasks ORDER BY created_at"
            )
    schedules = [_row_to_schedule(row) for row in rows]
    return ApiResponse[list[Schedule]](data=schedules)


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/schedules — create schedule (MCP proxy)
# ---------------------------------------------------------------------------


@router.post(
    "/{name}/schedules",
    response_model=ApiResponse[dict],
    status_code=201,
)
async def create_schedule(
    name: str,
    body: ScheduleCreate,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Create a new scheduled task via MCP tool call to the butler."""
    arguments: dict = {"name": body.name, "cron": body.cron}
    if body.dispatch_mode == _DISPATCH_MODE_JOB:
        arguments["dispatch_mode"] = body.dispatch_mode
        arguments["job_name"] = body.job_name
        if body.job_args is not None:
            arguments["job_args"] = body.job_args
    else:
        arguments["prompt"] = body.prompt
    if body.timezone is not None:
        arguments["timezone"] = body.timezone
    if body.start_at is not None:
        arguments["start_at"] = body.start_at.isoformat()
    if body.end_at is not None:
        arguments["end_at"] = body.end_at.isoformat()
    if body.until_at is not None:
        arguments["until_at"] = body.until_at.isoformat()
    if body.display_title is not None:
        arguments["display_title"] = body.display_title
    if body.calendar_event_id is not None:
        arguments["calendar_event_id"] = str(body.calendar_event_id)

    summary = {"name": body.name, "cron": body.cron, "dispatch_mode": body.dispatch_mode}
    if body.job_name is not None:
        summary["job_name"] = body.job_name
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_create", arguments)
        await log_audit_entry(db, name, "schedule.create", summary)
        return ApiResponse[dict](data=result)
    except HTTPException:
        await log_audit_entry(
            db, name, "schedule.create", summary, result="error", error="MCP call failed"
        )
        raise


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/schedules/{schedule_id} — update schedule (MCP proxy)
# ---------------------------------------------------------------------------


@router.put(
    "/{name}/schedules/{schedule_id}",
    response_model=ApiResponse[dict],
)
async def update_schedule(
    name: str,
    schedule_id: UUID,
    body: ScheduleUpdate,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Update a scheduled task via MCP tool call to the butler."""
    arguments: dict = {"id": str(schedule_id)}
    updates = body.model_dump(exclude_none=True)
    for key in ("start_at", "end_at", "until_at"):
        if key in updates:
            updates[key] = updates[key].isoformat()
    if "calendar_event_id" in updates:
        updates["calendar_event_id"] = str(updates["calendar_event_id"])
    arguments.update(updates)

    summary = {"schedule_id": str(schedule_id), **updates}
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_update", arguments)
        await log_audit_entry(db, name, "schedule.update", summary)
        return ApiResponse[dict](data=result)
    except HTTPException:
        await log_audit_entry(
            db, name, "schedule.update", summary, result="error", error="MCP call failed"
        )
        raise


# ---------------------------------------------------------------------------
# DELETE /api/butlers/{name}/schedules/{schedule_id} — delete schedule (MCP proxy)
# ---------------------------------------------------------------------------


@router.delete(
    "/{name}/schedules/{schedule_id}",
    response_model=ApiResponse[dict],
)
async def delete_schedule(
    name: str,
    schedule_id: UUID,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Delete a scheduled task via MCP tool call to the butler."""
    summary = {"schedule_id": str(schedule_id)}
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_delete", {"id": str(schedule_id)})
        await log_audit_entry(db, name, "schedule.delete", summary)
        return ApiResponse[dict](data=result)
    except HTTPException:
        await log_audit_entry(
            db, name, "schedule.delete", summary, result="error", error="MCP call failed"
        )
        raise


# ---------------------------------------------------------------------------
# PATCH /api/butlers/{name}/schedules/{schedule_id}/toggle — toggle (MCP proxy)
# ---------------------------------------------------------------------------


@router.post(
    "/{name}/schedules/{schedule_id}/trigger",
    response_model=ApiResponse[dict],
)
async def trigger_schedule(
    name: str,
    schedule_id: UUID,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Trigger a scheduled task immediately (one-off dispatch) via MCP."""
    summary = {"schedule_id": str(schedule_id)}
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_trigger", {"id": str(schedule_id)})
        await log_audit_entry(db, name, "schedule.trigger", summary)
        return ApiResponse[dict](data=result)
    except HTTPException:
        await log_audit_entry(
            db, name, "schedule.trigger", summary, result="error", error="MCP call failed"
        )
        raise


@router.patch(
    "/{name}/schedules/{schedule_id}/toggle",
    response_model=ApiResponse[dict],
)
async def toggle_schedule(
    name: str,
    schedule_id: UUID,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Toggle a scheduled task's enabled/disabled state via MCP."""
    summary = {"schedule_id": str(schedule_id)}
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_toggle", {"id": str(schedule_id)})
        await log_audit_entry(db, name, "schedule.toggle", summary)
        return ApiResponse[dict](data=result)
    except HTTPException:
        await log_audit_entry(
            db, name, "schedule.toggle", summary, result="error", error="MCP call failed"
        )
        raise
