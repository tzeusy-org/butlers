"""Standalone business logic for approvals operations.

Extracted from ApprovalsModule so that both MCP tools and REST API endpoints
can share the same implementation without duplication.

All functions accept an asyncpg connection pool (or compatible object) directly
and perform the database operations needed for each operation. Unlike the MCP
module methods, these functions do not enforce actor authentication — callers
are responsible for ensuring the request is authorized before calling these.
"""

from __future__ import annotations

import html
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.models import ActionStatus, ApprovalRule, PendingAction
from butlers.modules.approvals.sensitivity import suggest_constraints

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Approve action
# ---------------------------------------------------------------------------


async def approve_action(
    pool: Any,
    action_id: str,
    actor_id: str = "dashboard:rest-api",
    create_rule: bool = False,
) -> dict[str, Any]:
    """Approve a pending action and execute it (status transition only — no tool execution).

    Transitions the action from 'pending' → 'approved' → 'executed' (no tool_fn
    is available in the REST context). Returns the updated action dict.

    Parameters
    ----------
    pool:
        asyncpg pool or compatible object with fetchrow/execute/fetch.
    action_id:
        UUID string of the pending action.
    actor_id:
        Human-readable identifier for the decision maker (recorded in decided_by).
    create_rule:
        If True, creates a standing approval rule for this action's parameters.

    Returns
    -------
    dict
        Updated action dict, optionally with a ``created_rule`` key if create_rule=True.
        On error, returns ``{"error": "<message>"}``.
    """
    try:
        parsed_id = uuid.UUID(action_id)
    except ValueError:
        return {"error": f"Invalid action_id: {action_id}"}

    row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    if row is None:
        return {"error": f"Action not found: {action_id}"}

    action = PendingAction.from_row(row)

    # Validate transition: only pending → approved is valid
    if action.status != ActionStatus.PENDING:
        return {"error": f"Cannot transition from '{action.status.value}' to 'approved'"}

    now = datetime.now(UTC)
    decided_by = f"human:{actor_id}"

    # CAS update: pending → approved
    approved_row = await pool.fetchrow(
        "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
        "WHERE id = $4 AND status = $5 "
        "RETURNING *",
        ActionStatus.APPROVED.value,
        decided_by,
        now,
        parsed_id,
        ActionStatus.PENDING.value,
    )
    if approved_row is None:
        latest_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if latest_row is None:
            return {"error": f"Action not found: {action_id}"}
        latest_action = PendingAction.from_row(latest_row)
        return {"error": (f"Cannot transition from '{latest_action.status.value}' to 'approved'")}

    action = PendingAction.from_row(approved_row)

    await record_approval_event(
        pool,
        ApprovalEventType.ACTION_APPROVED,
        actor=f"user:{actor_id}",
        action_id=parsed_id,
        reason="approved via REST API",
        metadata={"tool_name": action.tool_name},
        occurred_at=now,
    )

    # Mark as executed immediately (no tool executor in REST context)
    executed_row = await pool.fetchrow(
        "UPDATE pending_actions SET status = $1, decided_at = $2 "
        "WHERE id = $3 AND status = $4 RETURNING *",
        ActionStatus.EXECUTED.value,
        now,
        parsed_id,
        ActionStatus.APPROVED.value,
    )
    if executed_row is None:
        # Already transitioned by another process — re-read final state
        executed_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)

    await record_approval_event(
        pool,
        ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
        actor=f"system:{actor_id}",
        action_id=parsed_id,
        reason="approved via REST API",
        metadata={"tool_name": action.tool_name},
        occurred_at=now,
    )

    # Optionally create a standing rule
    rule_dict: dict[str, Any] | None = None
    if create_rule:
        rule_result = await create_rule_from_action(
            pool,
            action_id=action_id,
            actor_id=actor_id,
        )
        if "error" not in rule_result:
            rule_dict = rule_result

    # Return final state
    final_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    result = PendingAction.from_row(final_row).to_dict()
    if rule_dict is not None:
        result["created_rule"] = rule_dict

    return result


# ---------------------------------------------------------------------------
# Reject action
# ---------------------------------------------------------------------------


async def reject_action(
    pool: Any,
    action_id: str,
    reason: str | None = None,
    actor_id: str = "dashboard:rest-api",
) -> dict[str, Any]:
    """Reject a pending action with optional reason.

    Parameters
    ----------
    pool:
        asyncpg pool or compatible object.
    action_id:
        UUID string of the pending action.
    reason:
        Human-readable reason for rejection (recorded in decided_by).
    actor_id:
        Identifier for the decision maker.

    Returns
    -------
    dict
        Updated action dict or ``{"error": "<message>"}``.
    """
    try:
        parsed_id = uuid.UUID(action_id)
    except ValueError:
        return {"error": f"Invalid action_id: {action_id}"}

    row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    if row is None:
        return {"error": f"Action not found: {action_id}"}

    action = PendingAction.from_row(row)

    if action.status != ActionStatus.PENDING:
        return {"error": f"Cannot transition from '{action.status.value}' to 'rejected'"}

    now = datetime.now(UTC)
    escaped_reason = html.escape(reason, quote=True) if reason else None
    decided_by = f"human:{actor_id}"
    if escaped_reason:
        decided_by = f"{decided_by} (reason: {escaped_reason})"

    rejected_row = await pool.fetchrow(
        "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
        "WHERE id = $4 AND status = $5 "
        "RETURNING *",
        ActionStatus.REJECTED.value,
        decided_by,
        now,
        parsed_id,
        ActionStatus.PENDING.value,
    )
    if rejected_row is None:
        latest_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if latest_row is None:
            return {"error": f"Action not found: {action_id}"}
        latest_action = PendingAction.from_row(latest_row)
        return {"error": (f"Cannot transition from '{latest_action.status.value}' to 'rejected'")}

    await record_approval_event(
        pool,
        ApprovalEventType.ACTION_REJECTED,
        actor=f"user:{actor_id}",
        action_id=parsed_id,
        reason=reason or "rejected via REST API",
        metadata={"tool_name": action.tool_name},
        occurred_at=now,
    )

    final_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    return PendingAction.from_row(final_row).to_dict()


# ---------------------------------------------------------------------------
# Create approval rule
# ---------------------------------------------------------------------------


async def create_approval_rule(
    pool: Any,
    tool_name: str,
    arg_constraints: dict[str, Any],
    description: str,
    expires_at: str | None = None,
    max_uses: int | None = None,
    actor_id: str = "dashboard:rest-api",
) -> dict[str, Any]:
    """Create a new standing approval rule.

    Parameters
    ----------
    pool:
        asyncpg pool or compatible object.
    tool_name:
        The tool name this rule applies to.
    arg_constraints:
        Argument constraints dict (see rules.py for constraint format).
    description:
        Human-readable description.
    expires_at:
        ISO-format datetime string for rule expiry (optional).
    max_uses:
        Maximum number of times the rule can be auto-applied (optional).
    actor_id:
        Identifier for the creator.

    Returns
    -------
    dict
        New rule dict or ``{"error": "<message>"}``.
    """
    if max_uses is not None and max_uses <= 0:
        return {"error": "max_uses must be greater than 0"}

    rule_id = uuid.uuid4()
    now = datetime.now(UTC)

    parsed_expires: datetime | None = None
    if expires_at is not None:
        try:
            parsed_expires = datetime.fromisoformat(expires_at)
        except ValueError:
            return {"error": f"Invalid expires_at format: {expires_at}"}

    rule = ApprovalRule(
        id=rule_id,
        tool_name=tool_name,
        arg_constraints=arg_constraints,
        description=description,
        created_at=now,
        expires_at=parsed_expires,
        max_uses=max_uses,
    )

    await pool.execute(
        "INSERT INTO approval_rules "
        "(id, tool_name, arg_constraints, description, created_at, "
        "expires_at, max_uses, active) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        rule.id,
        rule.tool_name,
        json.dumps(rule.arg_constraints),
        rule.description,
        rule.created_at,
        rule.expires_at,
        rule.max_uses,
        rule.active,
    )
    await record_approval_event(
        pool,
        ApprovalEventType.RULE_CREATED,
        actor=f"user:{actor_id}",
        rule_id=rule.id,
        reason="create_approval_rule via REST API",
        metadata={"tool_name": rule.tool_name},
        occurred_at=now,
    )

    return rule.to_dict()


# ---------------------------------------------------------------------------
# Create rule from action
# ---------------------------------------------------------------------------


async def create_rule_from_action(
    pool: Any,
    action_id: str,
    constraint_overrides: dict[str, Any] | None = None,
    actor_id: str = "dashboard:rest-api",
) -> dict[str, Any]:
    """Create a standing rule from a pending action using smart constraint defaults.

    Parameters
    ----------
    pool:
        asyncpg pool or compatible object.
    action_id:
        UUID string of the pending action to use as a template.
    constraint_overrides:
        Optional dict of constraints that override the auto-suggested ones.
    actor_id:
        Identifier for the creator.

    Returns
    -------
    dict
        New rule dict or ``{"error": "<message>"}``.
    """
    try:
        parsed_id = uuid.UUID(action_id)
    except ValueError:
        return {"error": f"Invalid action_id: {action_id}"}

    row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    if row is None:
        return {"error": f"Action not found: {action_id}"}

    action = PendingAction.from_row(row)

    # Generate suggested constraints via sensitivity analysis
    suggested = suggest_constraints(action.tool_name, action.tool_args)

    # Apply overrides if provided
    if constraint_overrides:
        for key, override in constraint_overrides.items():
            suggested[key] = override

    rule_id = uuid.uuid4()
    now = datetime.now(UTC)

    rule = ApprovalRule(
        id=rule_id,
        tool_name=action.tool_name,
        arg_constraints=suggested,
        description=f"Rule created from action {action_id}",
        created_from=parsed_id,
        created_at=now,
    )

    await pool.execute(
        "INSERT INTO approval_rules "
        "(id, tool_name, arg_constraints, description, created_from, created_at, "
        "max_uses, active) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        rule.id,
        rule.tool_name,
        json.dumps(rule.arg_constraints),
        rule.description,
        rule.created_from,
        rule.created_at,
        rule.max_uses,
        rule.active,
    )
    await record_approval_event(
        pool,
        ApprovalEventType.RULE_CREATED,
        actor=f"user:{actor_id}",
        action_id=parsed_id,
        rule_id=rule.id,
        reason="create_rule_from_action via REST API",
        metadata={"tool_name": rule.tool_name},
        occurred_at=now,
    )

    return rule.to_dict()


# ---------------------------------------------------------------------------
# Revoke rule
# ---------------------------------------------------------------------------


async def revoke_approval_rule(
    pool: Any,
    rule_id: str,
    actor_id: str = "dashboard:rest-api",
) -> dict[str, Any]:
    """Deactivate a standing approval rule.

    Parameters
    ----------
    pool:
        asyncpg pool or compatible object.
    rule_id:
        UUID string of the rule to revoke.
    actor_id:
        Identifier for the revoker (for audit log).

    Returns
    -------
    dict
        Updated rule dict or ``{"error": "<message>"}``.
    """
    try:
        parsed_id = uuid.UUID(rule_id)
    except ValueError:
        return {"error": f"Invalid rule_id: {rule_id}"}

    row = await pool.fetchrow("SELECT * FROM approval_rules WHERE id = $1", parsed_id)
    if row is None:
        return {"error": f"Rule not found: {rule_id}"}

    rule = ApprovalRule.from_row(row)
    if not rule.active:
        return {"error": f"Rule {rule_id} is already revoked"}

    await pool.execute(
        "UPDATE approval_rules SET active = $1 WHERE id = $2",
        False,
        parsed_id,
    )
    await record_approval_event(
        pool,
        ApprovalEventType.RULE_REVOKED,
        actor=f"user:{actor_id}",
        rule_id=parsed_id,
        reason="rule revoked via REST API",
        metadata={"tool_name": rule.tool_name},
    )

    updated_row = await pool.fetchrow("SELECT * FROM approval_rules WHERE id = $1", parsed_id)
    return ApprovalRule.from_row(updated_row).to_dict()
