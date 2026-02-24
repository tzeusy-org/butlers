"""Approvals dashboard API endpoints.

Provides REST API access to the approvals subsystem for dashboard integration:
- Pending action queue with filtering and pagination
- Decision endpoints (approve/reject)
- Standing approval rules CRUD
- Metrics for monitoring approval workflows
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import (
    ApiResponse,
    PaginatedResponse,
    PaginationMeta,
)
from butlers.api.models.approval import (
    ApprovalAction,
    ApprovalActionApproveRequest,
    ApprovalActionRejectRequest,
    ApprovalMetrics,
    ApprovalRule,
    ApprovalRuleCreateRequest,
    ApprovalRuleFromActionRequest,
    ExpireStaleActionsResponse,
    RuleConstraintSuggestion,
    TargetContact,
)
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

    Returns the first pool that has the table, or None if no butler has it.
    Uses a cache to avoid repeated system catalog queries on hot paths.
    """
    for butler_name in db_mgr.butler_names:
        cache_key = (butler_name, table_name)

        # Check cache first
        if cache_key in _TABLE_CACHE:
            if _TABLE_CACHE[cache_key]:
                try:
                    return db_mgr.pool(butler_name)
                except KeyError:
                    # Pool no longer exists, invalidate cache
                    del _TABLE_CACHE[cache_key]
                    continue
            else:
                continue

        # Not in cache, query the database
        try:
            pool = db_mgr.pool(butler_name)
            async with pool.acquire() as conn:
                table_check = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = $1)",
                    table_name,
                )
                _TABLE_CACHE[cache_key] = table_check
                if table_check:
                    return pool
        except KeyError:
            continue
    return None


def _pending_action_to_api(
    action: PendingAction,
    target_contact: TargetContact | None = None,
) -> ApprovalAction:
    """Convert a PendingAction to API representation with redacted sensitive data."""
    return ApprovalAction(
        id=str(action.id),
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
    )


async def _resolve_target_contact(
    db_mgr: DatabaseManager,
    action: PendingAction,
) -> TargetContact | None:
    """Resolve target_contact from contact_id in action tool_args.

    Looks up shared.contacts when tool_args contains a non-empty 'contact_id' key.
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

    # Find a pool that has shared.contacts (try all butlers)
    for butler_name in db_mgr.butler_names:
        try:
            pool = db_mgr.pool(butler_name)
            row = await pool.fetchrow(
                """
                SELECT id, name, COALESCE(roles, '{}') AS roles
                FROM shared.contacts
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
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ApprovalAction]:
    """List pending actions with filtering and pagination."""
    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")

    if target_pool is None:
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

    # Get total count
    async with target_pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM pending_actions{where_clause}",
            *args,
        )

        # Get paginated results
        query = (
            f"SELECT * FROM pending_actions{where_clause} "
            f"ORDER BY requested_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        rows = await conn.fetch(query, *args, limit, offset)

    pending_actions_list = [PendingAction.from_row(row) for row in rows]
    actions = []
    for pa in pending_actions_list:
        tc = await _resolve_target_contact(db_mgr, pa)
        actions.append(_pending_action_to_api(pa, tc))

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
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[ApprovalAction]:
    """List executed actions for audit review."""
    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")

    if target_pool is None:
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

    async with target_pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM pending_actions{where_clause}",
            *args,
        )

        query = (
            f"SELECT * FROM pending_actions{where_clause} "
            f"ORDER BY decided_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        rows = await conn.fetch(query, *args, limit, offset)

    pending_actions_list = [PendingAction.from_row(row) for row in rows]
    actions = []
    for pa in pending_actions_list:
        tc = await _resolve_target_contact(db_mgr, pa)
        actions.append(_pending_action_to_api(pa, tc))

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

    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    async with target_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)

    if row is None:
        raise HTTPException(status_code=404, detail=f"Action not found: {action_id}")

    pa = PendingAction.from_row(row)
    tc = await _resolve_target_contact(db_mgr, pa)
    action = _pending_action_to_api(pa, tc)
    return ApiResponse(data=action)


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    request: ApprovalActionApproveRequest = Body(default=ApprovalActionApproveRequest()),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalAction]:
    """Approve a pending action and execute it."""
    try:
        UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

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

    # Build the ApprovalAction from the result dict
    action = ApprovalAction(**{k: result[k] for k in ApprovalAction.model_fields if k in result})
    return ApiResponse(data=action)


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: str,
    request: ApprovalActionRejectRequest = Body(default=ApprovalActionRejectRequest()),
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ApprovalAction]:
    """Reject a pending action with optional reason."""
    try:
        UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {action_id}")

    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

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

    action = ApprovalAction(**{k: result[k] for k in ApprovalAction.model_fields if k in result})
    return ApiResponse(data=action)


@router.post("/actions/expire-stale")
async def expire_stale_actions(
    db_mgr: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ExpireStaleActionsResponse]:
    """Mark expired actions that are past their expires_at timestamp."""
    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")

    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    now = datetime.now(UTC)

    async with target_pool.acquire() as conn:
        rows = await conn.fetch(
            "UPDATE pending_actions SET status = 'expired', decided_by = 'system:expiry', "
            "decided_at = $1 WHERE status = 'pending' AND expires_at IS NOT NULL "
            "AND expires_at < $1 RETURNING id",
            now,
        )
        expired_ids = [str(row["id"]) for row in rows]

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
        UUID(request.action_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid action_id: {request.action_id}")

    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")
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
    target_pool = await _find_approvals_pool(db_mgr, "approval_rules")

    if target_pool is None:
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

    async with target_pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM approval_rules{where_clause}",
            *args,
        )

        query = (
            f"SELECT * FROM approval_rules{where_clause} "
            f"ORDER BY created_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        rows = await conn.fetch(query, *args, limit, offset)

    rules = [_approval_rule_to_api(ApprovalRuleModel.from_row(row)) for row in rows]

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

    target_pool = await _find_approvals_pool(db_mgr, "approval_rules")
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

    async with target_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM approval_rules WHERE id = $1", parsed_id)

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
        UUID(rule_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid rule_id: {rule_id}")

    target_pool = await _find_approvals_pool(db_mgr, "approval_rules")
    if target_pool is None:
        raise HTTPException(status_code=503, detail="Approvals subsystem unavailable")

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

    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")
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
    target_pool = await _find_approvals_pool(db_mgr, "pending_actions")

    if target_pool is None:
        return ApiResponse(data=ApprovalMetrics())

    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    async with target_pool.acquire() as conn:
        total_pending = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'"
        )

        total_approved_today = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_actions "
            "WHERE status IN ('approved', 'executed') AND decided_at >= $1",
            today_start,
        )

        total_rejected_today = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_actions WHERE status = 'rejected' AND decided_at >= $1",
            today_start,
        )

        total_auto_approved_today = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_actions "
            "WHERE status IN ('approved', 'executed') AND approval_rule_id IS NOT NULL "
            "AND decided_at >= $1",
            today_start,
        )

        total_expired_today = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_actions WHERE status = 'expired' AND decided_at >= $1",
            today_start,
        )

        avg_latency_row = await conn.fetchrow(
            "SELECT AVG(EXTRACT(EPOCH FROM (decided_at - requested_at))) as avg_latency "
            "FROM pending_actions "
            "WHERE decided_at >= $1 AND decided_at IS NOT NULL",
            today_start,
        )
        avg_decision_latency_seconds = (
            float(avg_latency_row["avg_latency"]) if avg_latency_row["avg_latency"] else None
        )

        total_decisions_today = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_actions WHERE decided_at >= $1",
            today_start,
        )

        auto_approval_rate = (
            (total_auto_approved_today / total_decisions_today)
            if total_decisions_today > 0
            else 0.0
        )

        rejection_rate = (
            (total_rejected_today / total_decisions_today) if total_decisions_today > 0 else 0.0
        )

        failure_count_today = await conn.fetchval(
            "SELECT COUNT(*) FROM pending_actions "
            "WHERE status = 'executed' AND decided_at >= $1 "
            "AND execution_result->>'error' IS NOT NULL",
            today_start,
        )

        active_rules_count = await conn.fetchval(
            "SELECT COUNT(*) FROM approval_rules WHERE active = true"
        )

    metrics = ApprovalMetrics(
        total_pending=total_pending or 0,
        total_approved_today=total_approved_today or 0,
        total_rejected_today=total_rejected_today or 0,
        total_auto_approved_today=total_auto_approved_today or 0,
        total_expired_today=total_expired_today or 0,
        avg_decision_latency_seconds=avg_decision_latency_seconds,
        auto_approval_rate=auto_approval_rate,
        rejection_rate=rejection_rate,
        failure_count_today=failure_count_today,
        active_rules_count=active_rules_count or 0,
    )

    return ApiResponse(data=metrics)
