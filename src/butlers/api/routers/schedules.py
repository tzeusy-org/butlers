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

import hashlib
import json
import logging
import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastmcp.client.client import CallToolResult
from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from butlers.api.audit_emit import authenticated_principal
from butlers.api.db import DatabaseManager
from butlers.api.deps import ButlerUnreachableError, Db, MCPClientManager, get_mcp_manager
from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.models.schedule import (
    Schedule,
    ScheduleCreate,
    ScheduleToggleRequest,
    ScheduleToggleResult,
    ScheduleUpdate,
)
from butlers.api.routers.audit import append as audit_append
from butlers.api.routers.audit import log_audit_entry
from butlers.api.routers.model_settings import _validate_complexity_tier
from butlers.core.model_routing import coerce_complexity_tier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "schedules"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEDULE_COLUMNS = (
    "id, name, cron, dispatch_mode, prompt, job_name, job_args, "
    "complexity, timezone, start_at, end_at, until_at, display_title, calendar_event_id, "
    "source, enabled, next_run_at, last_run_at, created_at, updated_at"
)
_DISPATCH_MODE_PROMPT = "prompt"
_DISPATCH_MODE_JOB = "job"
_MUTATION_SUCCESS_STATUS = {
    "schedule.create": "created",
    "schedule.update": "updated",
    "schedule.delete": "deleted",
    "schedule.trigger": "triggered",
}


def _coerce_complexity(value: str | None) -> str:
    """Coerce a stored complexity value to a valid canonical tier on read.

    Delegates to the shared ``coerce_complexity_tier`` (the single source of
    truth for tier normalization, PR #3000) so that every retired tier —
    trivial/medium/high/extra_high/discretion/self_healing — is remapped to
    its canonical successor, not just ``medium``. Old rows persisted before
    migration core_092 (or from the pre-bu-fev4q ``medium`` fallback bug) are
    thus normalized instead of surfaced unchanged (bu-65nop: the frontend
    dropdown has no legacy entries).

    ``strict=False`` is used because this is a read path: missing/null values
    default to ``workhorse`` and any unrecognized junk degrades to ``workhorse``
    (with a logged warning) rather than raising — a list read must never crash
    or surface an invalid tier to callers.
    """
    return coerce_complexity_tier(value, strict=False).value


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
        complexity=_coerce_complexity(_row_value(row, "complexity")),
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
    *,
    meta: dict | None = None,
) -> dict:
    """Call an MCP tool on a butler, returning the parsed result.

    Raises HTTPException(503) if the butler is unreachable or the call fails.
    """
    try:
        client = await mgr.get_client(butler_name)
        result = (
            await client.call_tool(tool_name, arguments, meta=meta)
            if meta is not None
            else await client.call_tool(tool_name, arguments)
        )
    except ButlerUnreachableError as exc:
        if meta is not None:
            raise HTTPException(
                503, "Owner creation unavailable; retry the same operation"
            ) from None
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{butler_name}' is unreachable: {exc}",
        ) from exc
    except Exception as exc:
        if meta is not None:
            raise HTTPException(
                503, "Owner creation unavailable; retry the same operation"
            ) from None
        raise HTTPException(
            status_code=503,
            detail=f"MCP call to '{butler_name}' failed: {exc}",
        ) from exc

    unavailable = "Owner creation unavailable; retry the same operation"
    if isinstance(result, CallToolResult):
        if result.is_error:
            raise HTTPException(503, unavailable if meta is not None else "MCP tool call failed")
        blocks = result.content
    else:
        # Legacy clients return the content list directly.
        blocks = result
    if isinstance(blocks, list):
        for block in blocks:
            text = getattr(block, "text", None)
            if text:
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    if meta is not None:
                        raise HTTPException(503, unavailable) from None
                    return {"result": text}
                if meta is not None and not isinstance(payload, dict):
                    raise HTTPException(503, unavailable)
                return payload
    if meta is not None:
        raise HTTPException(503, unavailable)
    return {"result": str(blocks)}


async def _log_legacy_schedule_mutation(
    db: DatabaseManager,
    butler: str,
    operation: str,
    *,
    schedule_id: UUID | None = None,
    response: dict | None = None,
) -> None:
    """Record safe, owner-attributed telemetry after a schedule MCP call."""
    actor = authenticated_principal()
    if response is None:
        observed_status = "unavailable"
        audit_result, error = "error", "MCP call failed"
    elif response.get("status") == _MUTATION_SUCCESS_STATUS[operation]:
        observed_status = _MUTATION_SUCCESS_STATUS[operation]
        audit_result, error = "success", None
    elif response.get("status") == "error":
        observed_status = "error"
        audit_result, error = "error", "MCP_REFUSED"
    else:
        observed_status = "unexpected"
        audit_result, error = "error", "MCP_UNEXPECTED_RESULT"

    if operation == "schedule.create" and audit_result == "success":
        try:
            schedule_id = UUID(str(response["id"])) if response is not None else None
        except (KeyError, TypeError, ValueError):
            schedule_id = None

    target = f"schedule:{schedule_id}" if schedule_id else f"butler:{butler}/schedules"
    summary: dict[str, object] = {
        "butler": butler,
        "path": target,
        "observed_status": observed_status,
    }
    if schedule_id is not None:
        summary["schedule_id"] = str(schedule_id)
    await log_audit_entry(db, actor, operation, summary, result=audit_result, error=error)


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/schedules — list schedules
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/schedules",
    response_model=ApiResponse[list[Schedule]],
)
async def list_schedules(
    name: str,
    *,
    db: Db,
) -> ApiResponse[list[Schedule]]:
    """Return all scheduled tasks for a single butler."""
    try:
        pool = db.pool(name)
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Butler '{name}' database is not available",
        ) from exc

    rows = await pool.fetch(f"SELECT {_SCHEDULE_COLUMNS} FROM scheduled_tasks ORDER BY created_at")
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
    request: Request,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    *,
    db: Db,
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
    if body.complexity is not None:
        _validate_complexity_tier(body.complexity)
        arguments["complexity"] = body.complexity

    try:
        if body.interrupt_owner:
            from butlers.api.owner_auth.service import AuthError
            from butlers.core.owner_creation import META_KEY, argument_digest

            authority = getattr(request.state, "owner_authority", None)
            service = getattr(request.app.state, "owner_auth_service", None)
            operations = request.headers.getlist("x-butlers-creation-operation")
            try:
                if service is None or authority is None or len(operations) != 1:
                    raise AuthError("FORBIDDEN")
                operation = str(UUID(operations[0]))
                if operations[0] != operation:
                    raise AuthError("INVALID_REQUEST")
                nonce = secrets.token_urlsafe(32)
                digest = argument_digest(arguments)
                issued = await service.attention_call(
                    "issue",
                    authority,
                    request.headers.get("x-csrf-token", ""),
                    operation=operation,
                    target=name,
                    request_digest=digest,
                    nonce_digest=hashlib.sha256(nonce.encode()).hexdigest(),
                )
                if issued.get("state") == "completed":
                    # An unknown commit is resolved by its durable receipt,
                    # never by recreating a deleted task or stamping an edit.
                    result = {"id": issued["id"], "status": "created"}
                elif issued == {"state": "issued"}:
                    result = await _call_mcp_tool(
                        mgr,
                        name,
                        "schedule_create",
                        arguments,
                        meta={
                            META_KEY: {
                                "nonce": nonce,
                                "session_digest": authority.session_digest,
                                "operation": operation,
                                "request_digest": digest,
                                "target": name,
                            }
                        },
                    )
                else:
                    raise AuthError("AUTH_UNAVAILABLE")
                if result.get("status") != "created" or not isinstance(result.get("id"), str):
                    raise AuthError("AUTH_UNAVAILABLE")
                result = {"id": str(UUID(result["id"])), "status": "created"}
            except AuthError as exc:
                raise HTTPException(exc.status_code, exc.message) from None
            except (ValueError, TypeError):
                raise HTTPException(400, "Invalid owner creation request") from None
        else:
            result = await _call_mcp_tool(mgr, name, "schedule_create", arguments)
        await _log_legacy_schedule_mutation(db, name, "schedule.create", response=result)
        return ApiResponse[dict](data=result)
    except HTTPException:
        await _log_legacy_schedule_mutation(db, name, "schedule.create")
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
    *,
    db: Db,
) -> ApiResponse[dict]:
    """Update a scheduled task via MCP tool call to the butler."""
    if body.complexity is not None:
        _validate_complexity_tier(body.complexity)
    arguments: dict = {"id": str(schedule_id)}
    updates = body.model_dump(exclude_none=True)
    for key in ("start_at", "end_at", "until_at"):
        if key in updates:
            updates[key] = updates[key].isoformat()
    if "calendar_event_id" in updates:
        updates["calendar_event_id"] = str(updates["calendar_event_id"])
    arguments.update(updates)

    try:
        result = await _call_mcp_tool(mgr, name, "schedule_update", arguments)
        await _log_legacy_schedule_mutation(
            db, name, "schedule.update", schedule_id=schedule_id, response=result
        )
        return ApiResponse[dict](data=result)
    except HTTPException:
        await _log_legacy_schedule_mutation(db, name, "schedule.update", schedule_id=schedule_id)
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
    *,
    db: Db,
) -> ApiResponse[dict]:
    """Delete a scheduled task via MCP tool call to the butler."""
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_delete", {"id": str(schedule_id)})
        await _log_legacy_schedule_mutation(
            db, name, "schedule.delete", schedule_id=schedule_id, response=result
        )
        return ApiResponse[dict](data=result)
    except HTTPException:
        await _log_legacy_schedule_mutation(db, name, "schedule.delete", schedule_id=schedule_id)
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
    *,
    db: Db,
) -> ApiResponse[dict]:
    """Trigger a scheduled task immediately (one-off dispatch) via MCP."""
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_trigger", {"id": str(schedule_id)})
        await _log_legacy_schedule_mutation(
            db, name, "schedule.trigger", schedule_id=schedule_id, response=result
        )
        return ApiResponse[dict](data=result)
    except HTTPException:
        await _log_legacy_schedule_mutation(db, name, "schedule.trigger", schedule_id=schedule_id)
        raise


async def _log_schedule_toggle_audit(
    db: DatabaseManager,
    butler: str,
    schedule_id: UUID,
    summary: dict[str, object],
    *,
    result: str = "success",
    error: str | None = None,
) -> None:
    """Record the owner action with butler and schedule as target context."""
    actor = authenticated_principal()
    try:
        await audit_append(
            db.pool("switchboard"),
            actor,
            "schedule.toggle",
            target=f"schedule:{schedule_id}",
            metadata={"butler": butler, **summary},
            result=result,
            error=error,
        )
    except Exception:
        logger.warning("Failed to record schedule.toggle audit", exc_info=True)


@router.patch(
    "/{name}/schedules/{schedule_id}/toggle",
    response_model=ApiResponse[ScheduleToggleResult],
)
async def toggle_schedule(
    name: str,
    schedule_id: UUID,
    body: ScheduleToggleRequest,
    mgr: MCPClientManager = Depends(get_mcp_manager),
    *,
    db: Db,
) -> ApiResponse[ScheduleToggleResult] | JSONResponse:
    """Persist and report a requested schedule state via the canonical MCP action."""
    summary: dict[str, object] = {
        "schedule_id": str(schedule_id),
        "requested_enabled": body.enabled,
    }
    arguments: dict[str, object] = {"id": str(schedule_id), "enabled": body.enabled}
    try:
        result = await _call_mcp_tool(mgr, name, "schedule_toggle", arguments)
        if result.get("status") == "error":
            code = str(result.get("code") or "SCHEDULE_TOGGLE_FAILED")
            message = str(result.get("message") or result.get("error") or "Schedule toggle failed")
            summary["code"] = code
            await _log_schedule_toggle_audit(
                db, name, schedule_id, summary, result="error", error=code
            )
            return _schedule_toggle_error_response(code, message, name, schedule_id)

        typed_result = ScheduleToggleResult.model_validate(result)
        summary.update(
            {
                "observed_enabled": typed_result.observed_enabled,
                "changed": typed_result.changed,
                "outcome": typed_result.outcome,
            }
        )
        await _log_schedule_toggle_audit(db, name, schedule_id, summary)
        return ApiResponse[ScheduleToggleResult](data=typed_result)
    except HTTPException:
        await _log_schedule_toggle_audit(
            db, name, schedule_id, summary, result="error", error="MCP call failed"
        )
        raise


def _schedule_toggle_error_response(
    code: str,
    message: str,
    butler: str,
    schedule_id: UUID,
) -> JSONResponse:
    """Return the typed public refusal for a failed schedule toggle."""
    status_code = {
        "SCHEDULE_NOT_FOUND": 404,
        "SCHEDULE_TOML_MANAGED": 409,
        "SCHEDULE_MANAGED": 409,
    }.get(code, 400)
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=code,
                message=message,
                butler=butler,
                details={"schedule_id": str(schedule_id)},
            )
        ).model_dump(mode="json"),
    )


# Clause authoring is HTTP-only. A model's tool schema has no authoring path.
class BreakThroughClauseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: UUID | None = None
    sender_domain: str | None = None
    channel: Literal["telegram", "email", "whatsapp", "dashboard"] | None = None
    security_kind: (
        Literal[
            "new_sign_in",
            "password_changed",
            "mfa_changed",
            "recovery_changed",
            "deletion_scheduled",
        ]
        | None
    ) = None
    scope: Literal["always", "quiet_hours_only"]
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_clause(self):
        import re

        if not any((self.entity_id, self.sender_domain, self.channel, self.security_kind)):
            raise ValueError("At least one fixed matcher is required")
        if self.sender_domain is not None and not re.fullmatch(
            r"(?=.{1,253}\Z)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*",
            self.sender_domain,
        ):
            raise ValueError("A canonical sender domain is required")
        return self


async def _clause_authoring(request: Request, action: str, values: dict) -> dict:
    from butlers.api.owner_auth.service import AuthError

    authority = getattr(request.state, "owner_authority", None)
    service = getattr(request.app.state, "owner_auth_service", None)
    if authority is None or service is None:
        raise HTTPException(403, "Owner cookie authentication required")
    try:
        return await service.attention_call(
            action, authority, request.headers.get("x-csrf-token", ""), **values
        )
    except AuthError as exc:
        raise HTTPException(exc.status_code, exc.message) from None


@router.post("/attention/break-through-clauses", response_model=ApiResponse[dict], status_code=201)
async def create_break_through_clause(body: BreakThroughClauseCreate, request: Request):
    return ApiResponse(
        data=await _clause_authoring(request, "clause_create", body.model_dump(mode="json"))
    )


@router.delete("/attention/break-through-clauses/{clause_id}", response_model=ApiResponse[dict])
async def revoke_break_through_clause(clause_id: UUID, request: Request):
    return ApiResponse(
        data=await _clause_authoring(request, "clause_revoke", {"id": str(clause_id)})
    )
