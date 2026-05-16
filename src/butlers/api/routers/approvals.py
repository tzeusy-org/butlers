"""Approvals dashboard API endpoints.

Provides REST API access to the approvals subsystem for dashboard integration:
- Pending action queue with filtering and pagination
- Decision endpoints (approve/reject/defer)
- Standing approval rules CRUD
- Approvals policy (quiet hours)
- Metrics for monitoring approval workflows
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.models import (
    ApiResponse,
    PaginatedResponse,
    PaginationMeta,
)
from butlers.api.models.approval import (
    ApprovalAction,
    ApprovalActionApproveRequest,
    ApprovalActionRejectRequest,
    ApprovalApproveRequest,
    ApprovalDeferRequest,
    ApprovalDenyRequest,
    ApprovalDetail,
    ApprovalMetrics,
    ApprovalRule,
    ApprovalRuleCreateRequest,
    ApprovalRuleFromActionRequest,
    ApprovalsPolicy,
    ApprovalSummary,
    AutonomySuggestion,
    AutonomySuggestionDismissRequest,
    ExpireStaleActionsResponse,
    RuleConstraintSuggestion,
    TargetContact,
)
from butlers.api.routers import audit as audit_router
from butlers.modules.approvals import operations as approvals_ops
from butlers.modules.approvals.models import (
    ApprovalRule as ApprovalRuleModel,
)
from butlers.modules.approvals.models import (
    PendingAction,
)
from butlers.modules.approvals.sensitivity import redact_constraints, redact_tool_args

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/approvals", tags=["approvals"])

# Cache mapping (butler_name, table_name) -> has_table to avoid repeated system catalog queries
_TABLE_CACHE: dict[tuple[str, str], bool] = {}


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _clear_table_cache():
    """Clear the table discovery cache. Used in tests to avoid cross-test pollution."""
    _TABLE_CACHE.clear()


async def _find_approvals_pool(db_mgr: DatabaseManager, table_name: str = "pending_actions"):
    """Find a butler pool that has the specified approvals table.

    Returns the first pool that has the table accessible via its search_path,
    or None if no butler has it.
    Uses a cache to avoid repeated catalog queries on hot paths.
    """
    pools = await _find_all_approvals_pools(db_mgr, table_name)
    return pools[0] if pools else None


async def _find_all_approvals_pools(
    db_mgr: DatabaseManager, table_name: str = "pending_actions"
) -> list[asyncpg.Pool]:
    """Find ALL butler pools that have the specified approvals table.

    Uses ``to_regclass`` which respects each connection's ``search_path``,
    so only pools where the table is actually accessible are returned.
    This is critical in the one-db/multi-schema topology where different
    butlers (e.g. switchboard vs home) may each have their own copy of the
    table in their respective schemas.
    """
    return [pool for _name, pool in await _find_named_approvals_pools(db_mgr, table_name)]


async def _find_named_approvals_pools(
    db_mgr: DatabaseManager, table_name: str = "pending_actions"
) -> list[tuple[str, asyncpg.Pool]]:
    """Find ALL butler pools that have the specified approvals table, with butler names.

    Returns a list of ``(butler_name, pool)`` pairs so callers can associate
    each result row with the owning butler.  Uses the same ``to_regclass``
    cache as ``_find_all_approvals_pools``.
    """
    named_pools: list[tuple[str, asyncpg.Pool]] = []
    seen: set[int] = set()  # track pool identity to avoid duplicates
    for butler_name in db_mgr.butler_names:
        cache_key = (butler_name, table_name)

        # Check cache first
        if cache_key in _TABLE_CACHE:
            if _TABLE_CACHE[cache_key]:
                try:
                    p = db_mgr.pool(butler_name)
                    if id(p) not in seen:
                        named_pools.append((butler_name, p))
                        seen.add(id(p))
                except KeyError:
                    del _TABLE_CACHE[cache_key]
            continue

        # Not in cache — use to_regclass which respects the connection's search_path
        try:
            pool = db_mgr.pool(butler_name)
            async with pool.acquire() as conn:
                table_check = await conn.fetchval(
                    "SELECT to_regclass($1) IS NOT NULL",
                    table_name,
                )
                _TABLE_CACHE[cache_key] = table_check
                if table_check and id(pool) not in seen:
                    named_pools.append((butler_name, pool))
                    seen.add(id(pool))
        except KeyError:
            continue
    return named_pools


async def _find_action_pool(
    db_mgr: DatabaseManager, action_id: UUID
) -> tuple[str, asyncpg.Pool] | None:
    """Find the pool that contains a specific pending_action by ID.

    Searches all pools that have the pending_actions table and returns a
    ``(butler_name, pool)`` pair for the first pool where the action exists,
    or None if not found.
    """
    named_pools = await _find_named_approvals_pools(db_mgr, "pending_actions")
    for butler_name, pool in named_pools:
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pending_actions WHERE id = $1)",
                    action_id,
                )
                if exists:
                    return (butler_name, pool)
        except Exception:
            continue
    return None


def _pending_action_to_api(
    action: PendingAction,
    butler_name: str,
    target_contact: TargetContact | None = None,
) -> ApprovalAction:
    """Convert a PendingAction to API representation with redacted sensitive data."""
    return ApprovalAction(
        id=str(action.id),
        butler=butler_name,
        tool_name=action.tool_name,
        tool_args=redact_tool_args(action.tool_name, action.tool_args),
        status=action.status.value,
        requested_at=action.requested_at,
        agent_summary=action.agent_summary,
        session_id=str(action.session_id) if action.session_id else None,
        expires_at=action.expires_at,
        decided_by=action.decided_by,
        decided_at=action.decided_at,
        execution_result=action.execution_result,
        approval_rule_id=str(action.approval_rule_id) if action.approval_rule_id else None,
        target_contact=target_contact,
        why=action.why,
        evidence=action.evidence,
    )


def _pending_action_to_detail(action: PendingAction, butler_name: str) -> ApprovalDetail:
    """Convert a PendingAction to the full Dispatch dossier ApprovalDetail."""
    title = f"{action.tool_name.replace('_', ' ').title()} ({butler_name})"
    proposed_action = {
        "tool_name": action.tool_name,
        "tool_args": redact_tool_args(action.tool_name, action.tool_args),
        "agent_summary": action.agent_summary,
    }
    return ApprovalDetail(
        id=str(action.id),
        title=title,
        butler=butler_name,
        created_at=action.requested_at,
        expires_at=action.expires_at,
        why=action.why,
        evidence=action.evidence,
        proposed_action=proposed_action,
        status=action.status.value,
        decided_by=action.decided_by,
        decided_at=action.decided_at,
    )


def _pending_action_to_summary(action: PendingAction, butler_name: str) -> ApprovalSummary:
    """Convert a PendingAction to a compact ApprovalSummary for the flat-list endpoint."""
    return ApprovalSummary(
        id=str(action.id),
        butler=butler_name,
        tool_name=action.tool_name,
        status=action.status.value,
        created_at=action.requested_at,
        expires_at=action.expires_at,
        why=action.why,
    )


async def _resolve_target_contact(
    db_mgr: DatabaseManager,
    action: PendingAction,
) -> TargetContact | None:
    """Resolve target_contact from contact_id in action tool_args.

    Looks up public.contacts when tool_args contains a non-empty 'contact_id' key.
    Returns None if not found, pool unavailable, or contact_id is not present.
    """
    contact_id_raw = action.tool_args.get("contact_id")
    if not contact_id_raw:
        return None

    try:
        from uuid import UUID

        contact_uuid = UUID(str(contact_id_raw))
    except (ValueError, AttributeError):
        return None

    # Find a pool that has public.contacts (try all butlers)
    for butler_name in db_mgr.butler_names:
        try:
            pool = db_mgr.pool(butler_name)
            row = await pool.fetchrow(
                """
                SELECT id, name, COALESCE(roles, '{}') AS roles
                FROM public.contacts
                WHERE id = $1
                """,
                contact_uuid,
            )
            if row is not None:
                raw_roles = row["roles"]
                roles = list(raw_roles) if raw_roles else []
                return TargetContact(
                    id=str(row["id"]),
                    name=row["name"] or "",
                    roles=roles,
                )
        except Exception:  # noqa: BLE001
            continue

    return None


def _approval_rule_to_api(rule: ApprovalRuleModel) -> ApprovalRule:
    """Convert an ApprovalRule to API representation with redacted sensitive data."""
    return ApprovalRule(
        id=str(rule.id),
        tool_name=rule.tool_name,
        arg_constraints=redact_constraints(rule.tool_name, rule.arg_constraints),
        description=rule.description,
        created_from=str(rule.created_from) if rule.created_from else None,
        created_at=rule.created_at,
        expires_at=rule.expires_at,
        max_uses=rule.max_uses,
        use_count=rule.use_count,
        active=rule.active,
    )


# ---------------------------------------------------------------------------
# Actions endpoints
# ---------------------------------------------------------------------------


@router.get("/actions")
async def list_actions(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    status: str | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    butler: str | None = Query(default=None),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ApprovalAction]:
    """List pending actions with filtering and pagination.

    When ``butler`` is supplied, only that butler's actions are returned.
    Without it, actions are aggregated across all butlers that have the
    ``pending_actions`` table.

    Every returned ``ApprovalAction`` includes a ``butler`` field indicating
    which butler owns the action.
    """
    # Resolve target (butler_name, pool) pairs — filter to one butler when set.
    # Short-circuit: if butler param is given and not a known butler, return empty immediately
    # without scanning all pools. Avoids catalog load proportional to roster size.
    if butler is not None and butler not in db_mgr.butler_names:
        return PaginatedResponse(
            data=[],
            meta=PaginationMeta(total=0, offset=offset, limit=limit),
        )
    named_pools = await _find_named_approvals_pools(db_mgr, "pending_actions")
    if butler is not None:
        named_target_pools = [(n, p) for n, p in named_pools if n == butler]
    else:
        named_target_pools = named_pools

    if not named_target_pools:
        return PaginatedResponse(
            data=[],
            meta=PaginationMeta(total=0, offset=offset, limit=limit),
        )

    # Build query with filters
    conditions = []
    args = []
    idx = 1

    if status not in (None, ""):
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    if tool_name is not None:
        conditions.append(f"tool_name = ${idx}")
        args.append(tool_name)
        idx += 1

    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since)
            conditions.append(f"requested_at >= ${idx}")
            args.append(since_dt)
            idx += 1
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid since timestamp: {since}")

    if until is not None:
        try:
            until_dt = datetime.fromisoformat(until)
            conditions.append(f"requested_at <= ${idx}")
            args.append(until_dt)
            idx += 1
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid until timestamp: {until}")

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    # Aggregate across target pools, tracking butler name per row
    all_rows: list[tuple[str, asyncpg.Record]] = []
    total = 0
    for butler_name, pool in named_target_pools:
        try:
            async with pool.acquire() as conn:
                total += await conn.fetchval(
                    f"SELECT COUNT(*) FROM pending_actions{where_clause}",
                    *args,
                )
                rows = await conn.fetch(
                    f"SELECT * FROM pending_actions{where_clause} ORDER BY requested_at DESC",
                    *args,
                )
                all_rows.extend((butler_name, row) for row in rows)
        except Exception:
            logger.warning("Failed to query pending_actions from a pool", exc_info=True)

    # Sort combined results and apply pagination in Python
    all_rows.sort(key=lambda pair: pair[1]["requested_at"], reverse=True)
    page_rows = all_rows[offset : offset + limit]

    actions = []
    for butler_name, row in page_rows:
        pa = PendingAction.from_row(row)
        tc = await _resolve_target_contact(db_mgr, pa)
        actions.append(_pending_action_to_api(pa, butler_name, tc))

    return PaginatedResponse(
        data=actions,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.get("/actions/executed")
async def list_executed_actions(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    tool_name: str | None = Query(default=None),
    rule_id: str | None = Query(default=None),
    since: str | None = Query(default=None),
    until: str | None = Query(default=None),
    butler: str | None = Query(default=None),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ApprovalAction]:
    """List executed actions for audit review.

    When ``butler`` is supplied, only that butler's executed actions are returned.
    Without it, executed actions are aggregated across all butlers.
    """
    if butler is not None and butler not in db_mgr.butler_names:
        return PaginatedResponse(
            data=[],
            meta=PaginationMeta(total=0, offset=offset, limit=limit),
        )
    all_named_pools = await _find_named_approvals_pools(db_mgr, "pending_actions")
    named_target_pools = (
        [(n, p) for n, p in all_named_pools if n == butler]
        if butler is not None
        else all_named_pools
    )

    if not named_target_pools:
        return PaginatedResponse(
            data=[],
            meta=PaginationMeta(total=0, offset=offset, limit=limit),
        )

    conditions = ["status = 'executed'"]
    args = []
    idx = 1

    if tool_name is not None:
        conditions.append(f"tool_name = ${idx}")
        args.append(tool_name)
        idx += 1

    if rule_id is not None:
        try:
            parsed_rule_id = UUID(rule_id)
            conditions.append(f"approval_rule_id = ${idx}")
            args.append(parsed_rule_id)
            idx += 1
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid rule_id: {rule_id}")

    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since)
            conditions.append(f"decided_at >= ${idx}")
            args.append(since_dt)
            idx += 1
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid since timestamp: {since}")

    if until is not None:
        try:
            until_dt = datetime.fromisoformat(until)
            conditions.append(f"decided_at <= ${idx}")
            args.append(until_dt)
            idx += 1
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid until timestamp: {until}")

    where_clause = " WHERE " + " AND ".join(conditions)
    all_rows: list[tuple[str, asyncpg.Record]] = []
    total = 0
    for butler_name, pool in named_target_pools:
        try:
            async with pool.acquire() as conn:
                total += await conn.fetchval(
                    f"SELECT COUNT(*) FROM pending_actions{where_clause}",
                    *args,
                )
                rows = await conn.fetch(
                    f"SELECT * FROM pending_actions{where_clause} ORDER BY decided_at DESC",
                    *args,
                )
                all_rows.extend((butler_name, row) for row in rows)
        except Exception:
            logger.warning("Failed to query executed actions from a pool", exc_info=True)

    all_rows.sort(
        key=lambda pair: pair[1]["decided_at"] or datetime.min.replace(tzinfo=UTC), reverse=True
    )
    page_rows = all_rows[offset : offset + limit]

    actions = []
    for butler_name, row in page_rows:
        pa = PendingAction.from_row(row)
        tc = await _resolve_target_contact(db_mgr, pa)
        actions.append(_pending_action_to_api(pa, butler_name, tc))

    return PaginatedResponse(
        data=actions,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.get("/actions/{action_id}")
async def get_action(
    action_id: str,
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalAction]:
    """Get details for a single pending action."""

    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    named_pools = await _find_named_approvals_pools(db_mgr, "pending_actions")
    if not named_pools:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    row = None
    found_butler = ""
    for butler_name, pool in named_pools:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
            if row is not None:
                found_butler = butler_name
                break
        except Exception:
            continue

    if row is None:
        raise HTTPException(status_code=404, detail=f"Action not found: {action_id}")

    pa = PendingAction.from_row(row)
    tc = await _resolve_target_contact(db_mgr, pa)
    action = _pending_action_to_api(pa, found_butler, tc)
    return ApiResponse(data=action)


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    request: ApprovalActionApproveRequest = Body(default=ApprovalActionApproveRequest()),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[ApprovalAction]:
    """Approve a pending action and dispatch it for execution."""
    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    found = await _find_action_pool(db_mgr, parsed_id)
    if found is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")
    action_butler, target_pool = found

    # Read the action before approval so we have tool_name/args for dispatch
    async with target_pool.acquire() as conn:
        action_row = await conn.fetchrow(
            "SELECT tool_name, tool_args FROM pending_actions WHERE id = $1", parsed_id
        )

    async with target_pool.acquire() as conn:
        result = await approvals_ops.approve_action(
            conn,
            action_id=action_id,
            create_rule=request.create_rule,
        )

    if "error" in result:
        error_msg = result["error"]
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        if "cannot transition" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    # Dispatch the approved action via MCP call_tool on a running butler
    if action_row is not None:
        tool_name = action_row["tool_name"]
        raw_args = action_row["tool_args"]
        tool_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)

        dispatch_result = await _dispatch_approved_action(
            mcp_mgr, db_mgr, target_pool, action_id, tool_name, tool_args
        )
        if dispatch_result is not None:
            result = dispatch_result

    # Build the ApprovalAction from the result dict, injecting the butler name
    result.setdefault("butler", action_butler)
    action_resp = ApprovalAction(
        **{k: result[k] for k in ApprovalAction.model_fields if k in result}
    )
    return ApiResponse(data=action_resp)


_MCP_DISPATCH_TIMEOUT_S = 30.0


async def _dispatch_approved_action(
    mcp_mgr: MCPClientManager,
    db_mgr: DatabaseManager,
    pool: asyncpg.Pool,
    action_id: str,
    tool_name: str,
    tool_args: dict,
) -> dict | None:
    """Dispatch an approved action via MCP and mark it as executed.

    For ``notify`` actions, calls the switchboard's ``deliver`` tool directly
    to bypass the daemon-side email guard (the action was already approved by
    a human — re-running it through notify() would just re-park it).

    For other tools, calls the tool by name on any available butler daemon.

    If the daemon is unreachable or the call fails, the action remains in
    'approved' state for later retry.

    Returns the updated action dict on success, or None if dispatch failed.
    """
    # For notify actions, call switchboard deliver directly to bypass the
    # email guard. notify() would re-check the recipient against contacts
    # and park it again.
    if tool_name == "notify":
        dispatch_tool = "deliver"
        dispatch_args = dict(tool_args)
        # deliver expects source_butler; notify tool_args don't include it
        dispatch_args.setdefault("source_butler", "switchboard")
        target_butlers = ["switchboard"]
    else:
        dispatch_tool = tool_name
        dispatch_args = tool_args
        target_butlers = ["switchboard"] + [n for n in mcp_mgr.butler_names if n != "switchboard"]

    for butler_name in target_butlers:
        try:
            client = await asyncio.wait_for(
                mcp_mgr.get_client(butler_name),
                timeout=_MCP_DISPATCH_TIMEOUT_S,
            )
            mcp_result = await asyncio.wait_for(
                client.call_tool(dispatch_tool, dispatch_args),
                timeout=_MCP_DISPATCH_TIMEOUT_S,
            )

            # Parse the MCP result
            exec_result: dict = {"success": True}
            if mcp_result.content:
                for block in mcp_result.content:
                    if hasattr(block, "text"):
                        try:
                            exec_result["result"] = json.loads(block.text)
                        except (json.JSONDecodeError, TypeError):
                            exec_result["result"] = {"value": block.text}
                        break

            if mcp_result.is_error:
                exec_result["success"] = False
                exec_result["error"] = exec_result.get("result", {}).get(
                    "error", "MCP tool call returned error"
                )

            # Mark as executed in DB
            async with pool.acquire() as conn:
                final = await approvals_ops.mark_executed(
                    conn,
                    action_id=action_id,
                    execution_result=exec_result,
                    success=exec_result["success"],
                )
            return final

        except Exception:
            logger.warning(
                "Failed to dispatch approved action %s via butler %s",
                action_id,
                butler_name,
                exc_info=True,
            )
            continue

    logger.warning(
        "Could not dispatch approved action %s — no reachable butler; "
        "action remains in 'approved' state for retry",
        action_id,
    )
    return None


@router.post("/actions/{action_id}/retry")
async def retry_action(
    action_id: str,
    db_mgr: DatabaseManager = Depends(_get_db_manager),
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[ApprovalAction]:
    """Retry dispatch for an approved action that was not yet executed."""
    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    found = await _find_action_pool(db_mgr, parsed_id)
    if found is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")
    action_butler, target_pool = found

    async with target_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)

    if row is None:
        raise HTTPException(status_code=404, detail=f"Action {action_id} not found")

    status = row["status"]
    if status != "approved":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Cannot retry action with status '{status}'; "
                "only 'approved' actions can be retried"
            ),
        )

    if row.get("execution_result") is not None:
        raise HTTPException(status_code=409, detail="Action already has an execution result")

    tool_name = row["tool_name"]
    raw_args = row["tool_args"]
    tool_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)

    dispatch_result = await _dispatch_approved_action(
        mcp_mgr, db_mgr, target_pool, action_id, tool_name, tool_args
    )

    if dispatch_result is None:
        raise HTTPException(status_code=502, detail="No reachable butler to dispatch action")

    # Re-read the row to get the final state after execution
    async with target_pool.acquire() as conn:
        updated_row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    pa = PendingAction.from_row(updated_row or row)
    tc = await _resolve_target_contact(db_mgr, pa)
    return ApiResponse(data=_pending_action_to_api(pa, action_butler, tc))


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: str,
    request: ApprovalActionRejectRequest = Body(default=ApprovalActionRejectRequest()),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalAction]:
    """Reject a pending action with optional reason."""
    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    found = await _find_action_pool(db_mgr, parsed_id)
    if found is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")
    action_butler, target_pool = found

    async with target_pool.acquire() as conn:
        result = await approvals_ops.reject_action(
            conn,
            action_id=action_id,
            reason=request.reason,
        )

    if "error" in result:
        error_msg = result["error"]
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        if "cannot transition" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    result.setdefault("butler", action_butler)
    action = ApprovalAction(**{k: result[k] for k in ApprovalAction.model_fields if k in result})
    return ApiResponse(data=action)


@router.post("/actions/expire-stale")
async def expire_stale_actions(
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ExpireStaleActionsResponse]:
    """Mark expired actions that are past their expires_at timestamp."""
    target_pools = await _find_all_approvals_pools(db_mgr, "pending_actions")

    if not target_pools:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    now = datetime.now(UTC)
    expired_ids: list[str] = []

    for pool in target_pools:
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "UPDATE pending_actions SET status = 'expired', decided_by = 'system:expiry', "
                    "decided_at = $1 WHERE status = 'pending' AND expires_at IS NOT NULL "
                    "AND expires_at < $1 RETURNING id",
                    now,
                )
                expired_ids.extend(str(row["id"]) for row in rows)
        except Exception:
            logger.warning("Failed to expire stale actions from a pool", exc_info=True)

    response = ExpireStaleActionsResponse(
        expired_count=len(expired_ids),
        expired_ids=expired_ids,
    )
    return ApiResponse(data=response)


# ---------------------------------------------------------------------------
# Rules endpoints
# ---------------------------------------------------------------------------


@router.post("/rules")
async def create_rule(
    request: ApprovalRuleCreateRequest = Body(...),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalRule]:
    """Create a new standing approval rule."""
    target_pool = await _find_approvals_pool(db_mgr, "approval_rules")
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    async with target_pool.acquire() as conn:
        result = await approvals_ops.create_approval_rule(
            conn,
            tool_name=request.tool_name,
            arg_constraints=request.arg_constraints,
            description=request.description,
            expires_at=request.expires_at,
            max_uses=request.max_uses,
        )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    rule = ApprovalRule(**{k: result[k] for k in ApprovalRule.model_fields if k in result})
    return ApiResponse(data=rule)


@router.post("/rules/from-action")
async def create_rule_from_action(
    request: ApprovalRuleFromActionRequest = Body(...),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalRule]:
    """Create a standing rule from a pending action."""
    try:
        parsed_id = UUID(request.action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {request.action_id}")

    target_pool = await _find_action_pool(db_mgr, parsed_id)
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    async with target_pool.acquire() as conn:
        result = await approvals_ops.create_rule_from_action(
            conn,
            action_id=request.action_id,
            constraint_overrides=request.constraint_overrides,
        )

    if "error" in result:
        error_msg = result["error"]
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    rule = ApprovalRule(**{k: result[k] for k in ApprovalRule.model_fields if k in result})
    return ApiResponse(data=rule)


@router.get("/rules")
async def list_rules(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    tool_name: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ApprovalRule]:
    """List standing approval rules with filtering and pagination."""
    target_pools = await _find_all_approvals_pools(db_mgr, "approval_rules")

    if not target_pools:
        return PaginatedResponse(
            data=[],
            meta=PaginationMeta(total=0, offset=offset, limit=limit),
        )

    conditions = []
    args = []
    idx = 1

    if active_only:
        conditions.append("active = true")

    if tool_name is not None:
        conditions.append(f"tool_name = ${idx}")
        args.append(tool_name)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    all_rows: list[asyncpg.Record] = []
    total = 0
    for pool in target_pools:
        try:
            async with pool.acquire() as conn:
                total += await conn.fetchval(
                    f"SELECT COUNT(*) FROM approval_rules{where_clause}",
                    *args,
                )
                rows = await conn.fetch(
                    f"SELECT * FROM approval_rules{where_clause} ORDER BY created_at DESC",
                    *args,
                )
                all_rows.extend(rows)
        except Exception:
            logger.warning("Failed to query approval_rules from a pool", exc_info=True)

    all_rows.sort(key=lambda r: r["created_at"], reverse=True)
    page_rows = all_rows[offset : offset + limit]

    rules = [_approval_rule_to_api(ApprovalRuleModel.from_row(row)) for row in page_rows]

    return PaginatedResponse(
        data=rules,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.get("/rules/{rule_id}")
async def get_rule(
    rule_id: str,
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalRule]:
    """Get details for a single standing approval rule."""

    try:
        parsed_id = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid rule_id: {rule_id}")

    target_pools = await _find_all_approvals_pools(db_mgr, "approval_rules")
    if not target_pools:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    row = None
    for pool in target_pools:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM approval_rules WHERE id = $1", parsed_id)
            if row is not None:
                break
        except Exception:
            continue

    if row is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")

    rule = _approval_rule_to_api(ApprovalRuleModel.from_row(row))
    return ApiResponse(data=rule)


@router.post("/rules/{rule_id}/revoke")
async def revoke_rule(
    rule_id: str,
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalRule]:
    """Revoke (deactivate) a standing approval rule."""
    try:
        parsed_id = UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid rule_id: {rule_id}")

    target_pools = await _find_all_approvals_pools(db_mgr, "approval_rules")
    if not target_pools:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    # Find the pool containing this rule
    target_pool = None
    for pool in target_pools:
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM approval_rules WHERE id = $1)",
                    parsed_id,
                )
                if exists:
                    target_pool = pool
                    break
        except Exception:
            continue

    if target_pool is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")

    async with target_pool.acquire() as conn:
        result = await approvals_ops.revoke_approval_rule(
            conn,
            rule_id=rule_id,
        )

    if "error" in result:
        error_msg = result["error"]
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        if "already revoked" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    rule = ApprovalRule(**{k: result[k] for k in ApprovalRule.model_fields if k in result})
    return ApiResponse(data=rule)


@router.get("/rules/suggestions/{action_id}")
async def get_rule_suggestions(
    action_id: str,
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[RuleConstraintSuggestion]:
    """Preview suggested constraints for creating a rule from a pending action."""

    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    target_pool = await _find_action_pool(db_mgr, parsed_id)
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    async with target_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)

    if row is None:
        raise HTTPException(status_code=404, detail=f"Action not found: {action_id}")

    action = PendingAction.from_row(row)

    from butlers.modules.approvals.sensitivity import suggest_constraints

    suggested = suggest_constraints(action.tool_name, action.tool_args)

    suggestion = RuleConstraintSuggestion(
        action_id=str(action.id),
        tool_name=action.tool_name,
        tool_args=redact_tool_args(action.tool_name, action.tool_args),
        suggested_constraints=redact_constraints(action.tool_name, suggested),
    )

    return ApiResponse(data=suggestion)


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def get_metrics(
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalMetrics]:
    """Get aggregate metrics for the approvals dashboard."""
    action_pools = await _find_all_approvals_pools(db_mgr, "pending_actions")
    rule_pools = await _find_all_approvals_pools(db_mgr, "approval_rules")

    if not action_pools:
        return ApiResponse(data=ApprovalMetrics())

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    total_pending = 0
    total_approved_today = 0
    total_rejected_today = 0
    total_auto_approved_today = 0
    total_expired_today = 0
    total_decisions_today = 0
    failure_count_today = 0
    latency_sum = 0.0
    latency_count = 0

    for pool in action_pools:
        try:
            async with pool.acquire() as conn:
                total_pending += (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'"
                    )
                    or 0
                )

                total_approved_today += (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM pending_actions "
                        "WHERE status IN ('approved', 'executed') AND decided_at >= $1",
                        today_start,
                    )
                    or 0
                )

                total_rejected_today += (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM pending_actions "
                        "WHERE status = 'rejected' AND decided_at >= $1",
                        today_start,
                    )
                    or 0
                )

                total_auto_approved_today += (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM pending_actions "
                        "WHERE status IN ('approved', 'executed') AND approval_rule_id IS NOT NULL "
                        "AND decided_at >= $1",
                        today_start,
                    )
                    or 0
                )

                total_expired_today += (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM pending_actions "
                        "WHERE status = 'expired' AND decided_at >= $1",
                        today_start,
                    )
                    or 0
                )

                row = await conn.fetchrow(
                    "SELECT AVG(EXTRACT(EPOCH FROM (decided_at - requested_at))) as avg_latency, "
                    "COUNT(*) as cnt "
                    "FROM pending_actions "
                    "WHERE decided_at >= $1 AND decided_at IS NOT NULL",
                    today_start,
                )
                pool_cnt = row["cnt"] or 0
                if pool_cnt > 0 and row["avg_latency"] is not None:
                    latency_sum += float(row["avg_latency"]) * pool_cnt
                    latency_count += pool_cnt

                total_decisions_today += (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM pending_actions WHERE decided_at >= $1",
                        today_start,
                    )
                    or 0
                )

                failure_count_today += (
                    await conn.fetchval(
                        "SELECT COUNT(*) FROM pending_actions "
                        "WHERE status = 'executed' AND decided_at >= $1 "
                        "AND execution_result->>'error' IS NOT NULL",
                        today_start,
                    )
                    or 0
                )
        except Exception:
            logger.warning("Failed to collect metrics from a pool", exc_info=True)

    avg_decision_latency_seconds = (latency_sum / latency_count) if latency_count > 0 else None

    auto_approval_rate = (
        (total_auto_approved_today / total_decisions_today) if total_decisions_today > 0 else 0.0
    )

    rejection_rate = (
        (total_rejected_today / total_decisions_today) if total_decisions_today > 0 else 0.0
    )

    active_rules_count = 0
    for pool in rule_pools:
        try:
            async with pool.acquire() as conn:
                active_rules_count += (
                    await conn.fetchval("SELECT COUNT(*) FROM approval_rules WHERE active = true")
                    or 0
                )
        except Exception:
            logger.warning("Failed to count active rules from a pool", exc_info=True)

    metrics = ApprovalMetrics(
        total_pending=total_pending,
        total_approved_today=total_approved_today,
        total_rejected_today=total_rejected_today,
        total_auto_approved_today=total_auto_approved_today,
        total_expired_today=total_expired_today,
        avg_decision_latency_seconds=avg_decision_latency_seconds,
        auto_approval_rate=auto_approval_rate,
        rejection_rate=rejection_rate,
        failure_count_today=failure_count_today,
        active_rules_count=active_rules_count,
    )

    return ApiResponse(data=metrics)


# ---------------------------------------------------------------------------
# New Dispatch-language endpoints (§8.1-§8.7)
# ---------------------------------------------------------------------------

_DECIDED_STATUSES = {"approved", "rejected", "expired", "executed"}
_WAITING_STATUSES = {"pending"}

_ACTOR_DASHBOARD = "dashboard:rest-api"


@router.get("")
async def list_approvals_flat(
    state: str = Query(default="all", description="waiting|decided|all"),
    limit: int = Query(default=100, ge=1, le=500),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ApprovalSummary]]:
    """Flat list of approvals — GET /api/approvals?state=waiting|decided|all.

    Complements the existing ``GET /api/approvals/actions`` paginated endpoint.
    Returns up to ``limit`` summaries ordered ``created_at DESC``.
    """
    named_pools = await _find_named_approvals_pools(db_mgr, "pending_actions")
    if not named_pools:
        return ApiResponse(data=[])

    status_filter: list[str]
    if state == "waiting":
        status_filter = list(_WAITING_STATUSES)
    elif state == "decided":
        status_filter = list(_DECIDED_STATUSES)
    else:
        status_filter = []

    all_rows: list[tuple[str, asyncpg.Record]] = []
    for butler_name, pool in named_pools:
        try:
            async with pool.acquire() as conn:
                if status_filter:
                    rows = await conn.fetch(
                        "SELECT * FROM pending_actions "
                        "WHERE status = ANY($1::text[]) "
                        "ORDER BY requested_at DESC LIMIT $2",
                        status_filter,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM pending_actions ORDER BY requested_at DESC LIMIT $1",
                        limit,
                    )
                all_rows.extend((butler_name, row) for row in rows)
        except Exception:
            logger.warning("Failed to query pending_actions for flat list", exc_info=True)

    all_rows.sort(key=lambda pair: pair[1]["requested_at"], reverse=True)
    page_rows = all_rows[:limit]

    summaries = []
    for butler_name, row in page_rows:
        pa = PendingAction.from_row(row)
        summaries.append(_pending_action_to_summary(pa, butler_name))

    return ApiResponse(data=summaries)


@router.get("/history")
async def list_approvals_history(
    since: str | None = Query(default=None, description="ISO 8601 timestamp"),
    limit: int = Query(default=30, ge=1, le=500),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ApprovalSummary]]:
    """Decided approvals history — GET /api/approvals/history?since=.

    Returns up to ``limit`` decided (approved|rejected|expired|executed) approvals
    ordered ``decided_at DESC``.
    """
    named_pools = await _find_named_approvals_pools(db_mgr, "pending_actions")
    if not named_pools:
        return ApiResponse(data=[])

    since_dt: datetime | None = None
    if since is not None:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid since timestamp: {since}")

    decided_statuses = list(_DECIDED_STATUSES)
    all_rows: list[tuple[str, asyncpg.Record]] = []

    for butler_name, pool in named_pools:
        try:
            async with pool.acquire() as conn:
                if since_dt is not None:
                    rows = await conn.fetch(
                        "SELECT * FROM pending_actions "
                        "WHERE status = ANY($1::text[]) AND decided_at >= $2 "
                        "ORDER BY decided_at DESC LIMIT $3",
                        decided_statuses,
                        since_dt,
                        limit,
                    )
                else:
                    rows = await conn.fetch(
                        "SELECT * FROM pending_actions "
                        "WHERE status = ANY($1::text[]) "
                        "ORDER BY decided_at DESC LIMIT $2",
                        decided_statuses,
                        limit,
                    )
                all_rows.extend((butler_name, row) for row in rows)
        except Exception:
            logger.warning("Failed to query history from a pool", exc_info=True)

    all_rows.sort(
        key=lambda pair: pair[1]["decided_at"] or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    page_rows = all_rows[:limit]

    summaries = [
        _pending_action_to_summary(PendingAction.from_row(row), name) for name, row in page_rows
    ]
    return ApiResponse(data=summaries)


@router.get("/policy")
async def get_approvals_policy(
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalsPolicy]:
    """Read the quiet-hours policy singleton — GET /api/approvals/policy."""
    pool = await _find_approvals_pool(db_mgr, "pending_actions")
    if pool is None:
        return ApiResponse(data=ApprovalsPolicy())

    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM public.approvals_policy WHERE id = 1")
    except Exception:
        logger.warning("Failed to read approvals_policy", exc_info=True)
        return ApiResponse(data=ApprovalsPolicy())

    if row is None:
        return ApiResponse(data=ApprovalsPolicy())

    return ApiResponse(
        data=ApprovalsPolicy(
            quiet_start_hour=row["quiet_start_hour"],
            quiet_end_hour=row["quiet_end_hour"],
            timezone=row["timezone"] or "UTC",
        )
    )


@router.put("/policy")
async def update_approvals_policy(
    request: ApprovalsPolicy = Body(...),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalsPolicy]:
    """Update the quiet-hours policy singleton — PUT /api/approvals/policy."""
    pool = await _find_approvals_pool(db_mgr, "pending_actions")
    if pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO public.approvals_policy
                    (id, quiet_start_hour, quiet_end_hour, timezone, updated_at)
                VALUES (1, $1, $2, $3, now())
                ON CONFLICT (id) DO UPDATE
                    SET quiet_start_hour = EXCLUDED.quiet_start_hour,
                        quiet_end_hour   = EXCLUDED.quiet_end_hour,
                        timezone         = EXCLUDED.timezone,
                        updated_at       = now()
                """,
                request.quiet_start_hour,
                request.quiet_end_hour,
                request.timezone,
            )
            try:
                await audit_router.append(conn, _ACTOR_DASHBOARD, "approvals.policy")
            except audit_router.AuditTableNotAvailableError:
                logger.warning("audit_log table not available; skipping audit for policy update")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update policy: {exc}") from exc

    return ApiResponse(data=request)


# ---------------------------------------------------------------------------
# Autonomy suggestions endpoints
# ---------------------------------------------------------------------------

_SUGGESTIONS_TABLE = "autonomy_suggestions"


def _generate_scope_description(tool_name: str, representative_args: dict) -> str:
    """Produce a human-readable scope description for an autonomy suggestion.

    Lists every (arg_key, arg_value) pair with exact match semantics, e.g.:
    "Auto-approve send_telegram when chat_id = 'mom_123' AND text = 'Good morning'"
    """
    if not representative_args:
        return f"Auto-approve {tool_name} (no argument constraints)"
    parts = [f"{k} = {v!r}" for k, v in sorted(representative_args.items())]
    return f"Auto-approve {tool_name} when {' AND '.join(parts)}"


def _row_to_autonomy_suggestion(row: dict) -> AutonomySuggestion:
    """Convert a database row to an AutonomySuggestion API model."""
    representative_args = row.get("representative_args") or {}
    if isinstance(representative_args, str):
        import json as _json

        try:
            representative_args = _json.loads(representative_args)
        except _json.JSONDecodeError:
            logger.warning(
                "Failed to decode representative_args JSON for autonomy suggestion row; "
                "falling back to empty dict. Raw value: %r",
                representative_args,
            )
            representative_args = {}

    # Redact sensitive fields before exposing them in the API response.
    redacted_args = redact_tool_args(row["tool_name"], representative_args)

    # Generate scope_description from the redacted view to avoid leaking secrets.
    scope_description = _generate_scope_description(row["tool_name"], redacted_args)

    return AutonomySuggestion(
        id=str(row["id"]),
        suggestion_type=row.get("suggestion_type") or "promotion",
        pattern_fingerprint=row["pattern_fingerprint"],
        tool_name=row["tool_name"],
        representative_args=redacted_args,
        status=row["status"],
        approval_count_at_creation=row.get("approval_count_at_creation") or 0,
        scope_description=scope_description,
        created_at=row["created_at"],
        decided_at=row.get("decided_at"),
        decided_by=row.get("decided_by"),
        resulting_rule_id=str(row["resulting_rule_id"]) if row.get("resulting_rule_id") else None,
        cooldown_until=row.get("cooldown_until"),
        dismissal_reason=row.get("dismissal_reason"),
        velocity=None,  # Velocity data fetched separately from state store when available
    )


@router.get("/suggestions")
async def list_suggestions(
    status: str | None = Query(default="pending"),
    suggestion_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[AutonomySuggestion]:
    """List autonomy suggestions with filtering and pagination.

    Returns promotion and demotion suggestions. Filters on status (default:
    ``pending``) and suggestion_type (``promotion`` or ``demotion``).
    When the autonomy_suggestions table does not yet exist, returns an empty
    list so the dashboard degrades gracefully.
    """
    suggestion_pools = await _find_all_approvals_pools(db_mgr, _SUGGESTIONS_TABLE)

    if not suggestion_pools:
        return PaginatedResponse(
            data=[],
            meta=PaginationMeta(total=0, offset=offset, limit=limit),
        )

    conditions: list[str] = []
    args: list = []
    idx = 1

    if status not in (None, "", "all"):
        conditions.append(f"status = ${idx}")
        args.append(status)
        idx += 1

    if suggestion_type not in (None, ""):
        conditions.append(f"suggestion_type = ${idx}")
        args.append(suggestion_type)
        idx += 1

    where_clause = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    all_rows: list[dict] = []
    total = 0
    for pool in suggestion_pools:
        try:
            async with pool.acquire() as conn:
                total += (
                    await conn.fetchval(
                        f"SELECT COUNT(*) FROM {_SUGGESTIONS_TABLE}{where_clause}",
                        *args,
                    )
                    or 0
                )
                rows = await conn.fetch(
                    f"SELECT * FROM {_SUGGESTIONS_TABLE}{where_clause} ORDER BY created_at DESC",
                    *args,
                )
                all_rows.extend(dict(r) for r in rows)
        except Exception:
            logger.warning("Failed to query autonomy_suggestions from a pool", exc_info=True)

    all_rows.sort(key=lambda r: r["created_at"], reverse=True)
    page_rows = all_rows[offset : offset + limit]

    suggestions = [_row_to_autonomy_suggestion(row) for row in page_rows]

    return PaginatedResponse(
        data=suggestions,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


@router.post("/suggestions/{suggestion_id}/confirm")
async def confirm_suggestion(
    suggestion_id: str,
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[AutonomySuggestion]:
    """Confirm an autonomy suggestion, creating a standing approval rule.

    For promotion suggestions, creates a new standing rule with exact constraints
    from the suggestion's representative_args. For demotion suggestions, revokes
    the referenced standing rule.

    Requires a valid UUID suggestion_id. Returns 404 if not found and 409 if
    the suggestion has already been decided.
    """
    try:
        parsed_id = UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid suggestion_id: {suggestion_id}")

    suggestion_pools = await _find_all_approvals_pools(db_mgr, _SUGGESTIONS_TABLE)
    if not suggestion_pools:
        raise HTTPException(status_code=503, detail="Autonomy suggestions subsystem unavailable")

    # Find the pool containing this suggestion
    target_pool = None
    row = None
    for pool in suggestion_pools:
        try:
            async with pool.acquire() as conn:
                found = await conn.fetchrow(
                    f"SELECT * FROM {_SUGGESTIONS_TABLE} WHERE id = $1",
                    parsed_id,
                )
                if found is not None:
                    target_pool = pool
                    row = dict(found)
                    break
        except Exception:
            continue

    if row is None:
        raise HTTPException(status_code=404, detail=f"Suggestion not found: {suggestion_id}")

    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Suggestion has already been decided (status: {row['status']})",
        )

    assert target_pool is not None

    representative_args = row.get("representative_args") or {}
    if isinstance(representative_args, str):
        import json as _json

        try:
            representative_args = _json.loads(representative_args)
        except _json.JSONDecodeError:
            logger.warning(
                "Failed to decode representative_args JSON in confirm_suggestion; "
                "falling back to empty dict. Raw value: %r",
                representative_args,
            )
            representative_args = {}

    now = datetime.now(UTC)
    actor = "dashboard:rest-api"

    async with target_pool.acquire() as conn:
        if row.get("suggestion_type") == "demotion":
            # Revoke the referenced standing rule via the operations layer so that
            # the RULE_REVOKED audit event is recorded consistently.
            # Use the already-parsed representative_args dict (not raw row value) to
            # avoid AttributeError when the DB stores args as JSON text.
            rule_id = row.get("resulting_rule_id") or representative_args.get("rule_id")
            if rule_id:
                revoke_result = await approvals_ops.revoke_approval_rule(
                    conn,
                    rule_id=str(rule_id),
                    actor_id=actor,
                )
                if "error" in revoke_result:
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to revoke approval rule for demotion suggestion: "
                        f"{revoke_result['error']}",
                    )

            updated = await conn.fetchrow(
                f"UPDATE {_SUGGESTIONS_TABLE} "
                "SET status = 'confirmed', decided_at = $1, decided_by = $2 "
                "WHERE id = $3 RETURNING *",
                now,
                actor,
                parsed_id,
            )
        else:
            # Promotion: create exact standing rule from representative_args via the
            # operations layer so that the RULE_CREATED audit event is recorded.
            arg_constraints = {
                k: {"type": "exact", "value": v} for k, v in representative_args.items()
            }
            tool_name = row["tool_name"]
            scope_desc = _generate_scope_description(tool_name, representative_args)

            create_result = await approvals_ops.create_approval_rule(
                conn,
                tool_name=tool_name,
                arg_constraints=arg_constraints,
                description=scope_desc,
                actor_id=actor,
            )
            if "error" in create_result:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create approval rule from suggestion: "
                    f"{create_result['error']}",
                )
            new_rule_id = create_result.get("id")

            updated = await conn.fetchrow(
                f"UPDATE {_SUGGESTIONS_TABLE} "
                "SET status = 'confirmed', decided_at = $1, decided_by = $2, "
                "resulting_rule_id = $3 "
                "WHERE id = $4 RETURNING *",
                now,
                actor,
                new_rule_id,
                parsed_id,
            )

    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to confirm suggestion")

    suggestion = _row_to_autonomy_suggestion(dict(updated))
    return ApiResponse(data=suggestion)


@router.post("/suggestions/{suggestion_id}/dismiss")
async def dismiss_suggestion(
    suggestion_id: str,
    request: AutonomySuggestionDismissRequest = Body(default=AutonomySuggestionDismissRequest()),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[AutonomySuggestion]:
    """Dismiss an autonomy suggestion with an optional reason and cooldown.

    Transitions the suggestion to ``dismissed`` status and sets ``cooldown_until``
    so the pattern won't resurface until the cooldown expires.

    Returns 404 if not found and 409 if already decided.
    """
    try:
        parsed_id = UUID(suggestion_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid suggestion_id: {suggestion_id}")

    suggestion_pools = await _find_all_approvals_pools(db_mgr, _SUGGESTIONS_TABLE)
    if not suggestion_pools:
        raise HTTPException(status_code=503, detail="Autonomy suggestions subsystem unavailable")

    target_pool = None
    row = None
    for pool in suggestion_pools:
        try:
            async with pool.acquire() as conn:
                found = await conn.fetchrow(
                    f"SELECT * FROM {_SUGGESTIONS_TABLE} WHERE id = $1",
                    parsed_id,
                )
                if found is not None:
                    target_pool = pool
                    row = dict(found)
                    break
        except Exception:
            continue

    if row is None:
        raise HTTPException(status_code=404, detail=f"Suggestion not found: {suggestion_id}")

    if row["status"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Suggestion has already been decided (status: {row['status']})",
        )

    assert target_pool is not None

    from datetime import timedelta

    now = datetime.now(UTC)
    cooldown_until = now + timedelta(days=request.cooldown_days)
    actor = "dashboard"

    async with target_pool.acquire() as conn:
        updated = await conn.fetchrow(
            f"UPDATE {_SUGGESTIONS_TABLE} "
            "SET status = 'dismissed', decided_at = $1, decided_by = $2, "
            "cooldown_until = $3, dismissal_reason = $4 "
            "WHERE id = $5 RETURNING *",
            now,
            actor,
            cooldown_until,
            request.reason,
            parsed_id,
        )

    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to dismiss suggestion")

    suggestion = _row_to_autonomy_suggestion(dict(updated))
    return ApiResponse(data=suggestion)


# ---------------------------------------------------------------------------
# Dispatch dossier — dynamic routes (must follow all literal paths)
# These must be declared LAST so they do not shadow literal routes such as
# /suggestions, /history, /policy, /metrics, etc.
# ---------------------------------------------------------------------------


@router.get("/{action_id}")
async def get_approval_detail(
    action_id: str,
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalDetail]:
    """Full dossier for one approval — GET /api/approvals/{id}."""
    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    named_pools = await _find_named_approvals_pools(db_mgr, "pending_actions")
    if not named_pools:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    for butler_name, pool in named_pools:
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
            if row is not None:
                pa = PendingAction.from_row(row)
                return ApiResponse(data=_pending_action_to_detail(pa, butler_name))
        except Exception:
            continue

    raise HTTPException(status_code=404, detail=f"Approval not found: {action_id}")


@router.post("/{action_id}/approve")
async def approve_approval(
    action_id: str,
    request: ApprovalApproveRequest = Body(default=ApprovalApproveRequest()),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
) -> ApiResponse[ApprovalAction]:
    """Approve a pending action — POST /api/approvals/{id}/approve {edits?: object}.

    Applies any ``edits`` to the tool args before executing, then audits the action.
    """
    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    found = await _find_action_pool(db_mgr, parsed_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Approval not found: {action_id}")
    action_butler, target_pool = found

    # Use a single connection for the read, optional edits update, approve, and audit
    # so that an edits UPDATE cannot succeed while the approve transition fails.
    async with target_pool.acquire() as conn:
        action_row = await conn.fetchrow(
            "SELECT tool_name, tool_args FROM pending_actions WHERE id = $1", parsed_id
        )

        # Apply edits to tool args before approval (same connection, no partial update risk)
        if request.edits and action_row is not None:
            raw_args = action_row["tool_args"]
            tool_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            tool_args.update(request.edits)
            await conn.execute(
                "UPDATE pending_actions SET tool_args = $1 WHERE id = $2",
                json.dumps(tool_args),
                parsed_id,
            )

        result = await approvals_ops.approve_action(
            conn,
            action_id=action_id,
            create_rule=False,
        )
        try:
            edits_note = json.dumps(request.edits) if request.edits else None
            await audit_router.append(
                conn, _ACTOR_DASHBOARD, "approval.approve", target=action_id, note=edits_note
            )
        except audit_router.AuditTableNotAvailableError:
            logger.warning("audit_log table not available; skipping audit for approve")

    if "error" in result:
        error_msg = result["error"]
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        if "cannot transition" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    if action_row is not None:
        tool_name = action_row["tool_name"]
        raw_args = action_row["tool_args"]
        tool_args_for_dispatch = (
            json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        )
        if request.edits:
            tool_args_for_dispatch.update(request.edits)

        dispatch_result = await _dispatch_approved_action(
            mcp_mgr, db_mgr, target_pool, action_id, tool_name, tool_args_for_dispatch
        )
        if dispatch_result is not None:
            result = dispatch_result

    result.setdefault("butler", action_butler)
    action_resp = ApprovalAction(
        **{k: result[k] for k in ApprovalAction.model_fields if k in result}
    )
    return ApiResponse(data=action_resp)


@router.post("/{action_id}/deny")
async def deny_approval(
    action_id: str,
    request: ApprovalDenyRequest = Body(default=ApprovalDenyRequest()),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalAction]:
    """Deny (reject) a pending action — POST /api/approvals/{id}/deny {reason?: str}."""
    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    found = await _find_action_pool(db_mgr, parsed_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Approval not found: {action_id}")
    action_butler, target_pool = found

    async with target_pool.acquire() as conn:
        result = await approvals_ops.reject_action(
            conn,
            action_id=action_id,
            reason=request.reason,
        )
        try:
            await audit_router.append(
                conn, _ACTOR_DASHBOARD, "approval.deny", target=action_id, note=request.reason
            )
        except audit_router.AuditTableNotAvailableError:
            logger.warning("audit_log table not available; skipping audit for deny")

    if "error" in result:
        error_msg = result["error"]
        if "not found" in error_msg.lower():
            raise HTTPException(status_code=404, detail=error_msg)
        if "cannot transition" in error_msg.lower():
            raise HTTPException(status_code=409, detail=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)

    result.setdefault("butler", action_butler)
    action = ApprovalAction(**{k: result[k] for k in ApprovalAction.model_fields if k in result})
    return ApiResponse(data=action)


@router.post("/{action_id}/defer")
async def defer_approval(
    action_id: str,
    request: ApprovalDeferRequest = Body(...),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalAction]:
    """Defer an approval by extending its expiry — POST /api/approvals/{id}/defer {hours: int}.

    ``hours`` must be in [1, 168].  The action's ``expires_at`` is extended by the
    given number of hours and the action remains in ``pending`` state.
    """
    try:
        parsed_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    found = await _find_action_pool(db_mgr, parsed_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Approval not found: {action_id}")
    action_butler, target_pool = found

    now = datetime.now(UTC)
    new_expires_at = now + timedelta(hours=request.hours)

    async with target_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Approval not found: {action_id}")

        if row["status"] != "pending":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot defer action with status '{row['status']}'; "
                    "only 'pending' actions can be deferred"
                ),
            )

        updated = await conn.fetchrow(
            "UPDATE pending_actions SET expires_at = $1 WHERE id = $2 RETURNING *",
            new_expires_at,
            parsed_id,
        )
        try:
            await audit_router.append(
                conn, _ACTOR_DASHBOARD, "approval.defer", target=action_id, note=str(request.hours)
            )
        except audit_router.AuditTableNotAvailableError:
            logger.warning("audit_log table not available; skipping audit for defer")

    if updated is None:
        raise HTTPException(status_code=500, detail="Failed to defer approval")

    pa = PendingAction.from_row(updated)
    tc = await _resolve_target_contact(db_mgr, pa)
    return ApiResponse(data=_pending_action_to_api(pa, action_butler, tc))
