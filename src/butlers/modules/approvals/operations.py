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

from butlers.modules.approvals.autonomy_suggestions import (
    supersede_matching_suggestions as _supersede_matching_suggestions,
)
from butlers.modules.approvals.autonomy_tracker import (
    check_promotion_threshold as _check_promotion_threshold,
)
from butlers.modules.approvals.autonomy_tracker import (
    compute_fingerprint as _compute_fingerprint,
)
from butlers.modules.approvals.autonomy_tracker import (
    record_approval as _record_approval,
)
from butlers.modules.approvals.decision_memory import DecisionMemoryWriter
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.executor import _approval_write_transaction
from butlers.modules.approvals.models import ActionStatus, ApprovalRule, PendingAction
from butlers.modules.approvals.sensitivity import suggest_constraints

logger = logging.getLogger(__name__)

_TELEGRAM_CALLBACK_ACTOR = "owner@telegram"


def _decision_event_actor(actor_id: str) -> str:
    """Keep the verified Telegram decision provenance intact in the event spine."""
    if actor_id == _TELEGRAM_CALLBACK_ACTOR:
        return f"human:{actor_id}"
    return f"user:{actor_id}"


# ---------------------------------------------------------------------------
# Approve action
# ---------------------------------------------------------------------------


async def expire_pending_action_if_stale(
    pool: Any,
    action: PendingAction,
    *,
    now: datetime | None = None,
    target_action: str = "approved",
) -> dict[str, Any] | None:
    """Expire a still-pending action whose approval window has elapsed.

    Approval expiry is a denial boundary, not just a background cleanup concern.
    Decision paths call this before allowing any pending -> approved transition
    so a missed sweep cannot make an expired action executable.
    """
    if action.status != ActionStatus.PENDING or action.expires_at is None:
        return None

    effective_now = now or datetime.now(UTC)
    expires_at = action.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at >= effective_now:
        return None

    expired_row = await pool.fetchrow(
        "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
        "WHERE id = $4 AND status = $5 "
        "RETURNING *",
        ActionStatus.EXPIRED.value,
        "system:expiry",
        effective_now,
        action.id,
        ActionStatus.PENDING.value,
    )
    if expired_row is not None:
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_EXPIRED,
            actor="system:expiry",
            action_id=action.id,
            reason="approval window elapsed",
            metadata={"tool_name": action.tool_name},
            occurred_at=effective_now,
        )
        return {
            "error": (
                f"Action {action.id} expired at {expires_at.isoformat()} "
                f"and cannot be {target_action}"
            )
        }

    latest_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", action.id)
    if latest_row is None:
        return {"error": f"Action not found: {action.id}"}
    latest_action = PendingAction.from_row(latest_row)
    return {"error": f"Cannot transition from '{latest_action.status.value}' to '{target_action}'"}


async def approve_action(
    pool: Any,
    action_id: str,
    actor_id: str = "dashboard:rest-api",
    create_rule: bool = False,
    decision_memory_writer: DecisionMemoryWriter | None = None,
) -> dict[str, Any]:
    """Approve a pending action without performing its side effect.

    Transitions the action from ``pending`` to ``approved``. The API dispatches
    the side effect through the owning daemon afterwards; only that successful,
    audited dispatch may transition the row to ``executed``. Returns the updated
    action dict.

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
    expired_result = await expire_pending_action_if_stale(pool, action, now=now)
    if expired_result is not None:
        return expired_result

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
        actor=_decision_event_actor(actor_id),
        action_id=parsed_id,
        reason="approved via REST API",
        metadata={"tool_name": action.tool_name},
        occurred_at=now,
    )

    # Post-approval autonomy tracker hook (task 7.1)
    # Wrap in try/except so tracker failure doesn't block approval
    try:
        # Re-read to get decided_at
        updated_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if updated_row is not None:
            updated_action = PendingAction.from_row(updated_row)
            await _record_approval(pool, updated_action)
            # Use a minimal config namespace with defaults (no module import needed)
            _default_config = type(
                "_Config", (), {"promotion_threshold": 5, "suggestion_cooldown_days": 30}
            )()
            await _check_promotion_threshold(
                pool=pool,
                pattern_fingerprint=_compute_fingerprint(action.tool_name, action.tool_args),
                tool_name=action.tool_name,
                tool_args=action.tool_args,
                config=_default_config,
                action_id=updated_action.id,
            )
    except Exception:
        logger.exception(
            "Autonomy tracker hook failed for action %s — approval not blocked", action_id
        )

    # Optionally create a standing rule
    rule_dict: dict[str, Any] | None = None
    if create_rule:
        rule_result = await create_rule_from_action(
            pool,
            action_id=action_id,
            actor_id=actor_id,
            decision_memory_writer=decision_memory_writer,
        )
        if "error" not in rule_result:
            rule_dict = rule_result

    # Return the approved (not yet executed) state.
    # Callers with access to a tool executor should dispatch execution
    # and call mark_executed() afterwards; the REST API does this via
    # MCP call_tool on the appropriate butler daemon.
    final_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    result = PendingAction.from_row(final_row).to_dict()
    if rule_dict is not None:
        result["created_rule"] = rule_dict

    return result


async def mark_executed(
    pool: Any,
    action_id: str,
    execution_result: dict[str, Any] | None = None,
    success: bool = True,
    actor_id: str = "dashboard:rest-api",
    decision_memory_writer: DecisionMemoryWriter | None = None,
) -> dict[str, Any]:
    """Transition an approved action to executed with an execution result.

    Called after the caller has actually dispatched the tool (e.g. via MCP
    call_tool). Only a successful dispatch with a persisted result may make
    the terminal transition; failed or unavailable work stays `approved` and
    retryable rather than falsely claiming execution.

    Returns the final action dict or ``{"error": "<message>"}``.
    """
    try:
        parsed_id = uuid.UUID(action_id)
    except ValueError:
        return {"error": f"Invalid action_id: {action_id}"}

    if not success:
        return {
            "error": (
                "Cannot mark a failed dispatch as executed; action remains approved for retry"
            )
        }
    if execution_result is None:
        return {"error": "Cannot mark executed without a persisted execution result"}
    if isinstance(execution_result, dict) and execution_result.get("success") is False:
        return {
            "error": (
                "Cannot mark a failed execution result as executed; "
                "action remains approved for retry"
            )
        }

    now = datetime.now(UTC)
    # Bind the sanitized dict directly (no json.dumps, no ::jsonb cast) —
    # asyncpg's registered jsonb codec already serializes once; pre-serializing
    # double-encodes into a jsonb-typed STRING (bu-cymc4/bu-c8b8e; mirrors
    # gate.py's fix, PR #2924).
    safe_execution_result = (
        json.loads(json.dumps(execution_result, default=str)) if execution_result else None
    )

    try:
        # The REST notify fast-path performs the irreversible delivery before
        # arriving here. Its terminal status is therefore trustworthy only if
        # the result and immutable audit event commit together.
        async with _approval_write_transaction(pool) as write_target:
            executed_row = await write_target.fetchrow(
                "UPDATE pending_actions SET status = $1, execution_result = $2, decided_at = $3 "
                "WHERE id = $4 AND status = $5 RETURNING *",
                ActionStatus.EXECUTED.value,
                safe_execution_result,
                now,
                parsed_id,
                ActionStatus.APPROVED.value,
            )
            if executed_row is None:
                row = await write_target.fetchrow(
                    "SELECT * FROM pending_actions WHERE id = $1", parsed_id
                )
                if row is None:
                    return {"error": f"Action not found: {action_id}"}
                return PendingAction.from_row(row).to_dict()

            await record_approval_event(
                write_target,
                ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
                actor=f"system:{actor_id}",
                action_id=parsed_id,
                reason="executed via REST API dispatch",
                metadata={"tool_name": PendingAction.from_row(executed_row).tool_name},
                occurred_at=now,
            )
    except Exception as exc:  # noqa: BLE001 -- leave the action retryable on audit failure
        logger.error(
            "Could not persist REST execution outcome for action %s: %s",
            action_id,
            exc,
        )
        return {"error": f"Could not persist execution outcome: {exc}"}

    executed_action = PendingAction.from_row(executed_row)
    if decision_memory_writer is not None:
        await decision_memory_writer.record_terminal_decision(executed_action, "approved")

    return executed_action.to_dict()


# ---------------------------------------------------------------------------
# Reject action
# ---------------------------------------------------------------------------


async def reject_action(
    pool: Any,
    action_id: str,
    reason: str | None = None,
    actor_id: str = "dashboard:rest-api",
    decision_memory_writer: DecisionMemoryWriter | None = None,
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
    expired_result = await expire_pending_action_if_stale(
        pool,
        action,
        now=now,
        target_action=ActionStatus.REJECTED.value,
    )
    if expired_result is not None:
        return expired_result

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
        actor=_decision_event_actor(actor_id),
        action_id=parsed_id,
        reason=reason or "rejected via REST API",
        metadata={"tool_name": action.tool_name},
        occurred_at=now,
    )

    if decision_memory_writer is not None:
        await decision_memory_writer.record_terminal_decision(
            PendingAction.from_row(rejected_row), "rejected"
        )

    final_row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    return PendingAction.from_row(final_row).to_dict()


# ---------------------------------------------------------------------------
# Abandon approved recovery
# ---------------------------------------------------------------------------


async def abandon_approved_action(
    pool: Any,
    action_id: str,
    reason: str,
    actor_id: str = "dashboard:rest-api",
) -> dict[str, Any]:
    """Durably abandon one approved action that has never executed.

    This operation is intentionally not an MCP tool: the dashboard is the
    only owner-decision boundary permitted to invoke it.  Its compare-and-set
    predicate is also the stalled/retry predicate, so a stale client cannot
    abandon an action that has already acquired an execution result.
    """
    if not reason or not reason.strip():
        return {"error": "Abandon reason must not be blank"}

    try:
        parsed_id = uuid.UUID(action_id)
    except ValueError:
        return {"error": f"Invalid action_id: {action_id}"}

    row = await pool.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
    if row is None:
        return {"error": f"Action not found: {action_id}"}

    action = PendingAction.from_row(row)
    # A dashboard request can be delayed behind a handler's execution lock or
    # its pool connection. Once the handler commits a result, explain that
    # irreversible outcome instead of presenting it as an abstract transition
    # failure. This mirrors the result-first check after a failed CAS below.
    if action.status == ActionStatus.EXECUTED and action.execution_result is not None:
        return {"error": "Action already has an execution result and cannot be abandoned"}
    if action.status != ActionStatus.APPROVED:
        return {"error": f"Cannot transition from '{action.status.value}' to 'abandoned'"}
    if action.execution_result is not None:
        return {"error": "Action already has an execution result and cannot be abandoned"}

    now = datetime.now(UTC)
    decided_by = f"human:{actor_id}"
    try:
        async with _approval_write_transaction(pool) as write_target:
            abandoned_row = await write_target.fetchrow(
                "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
                "WHERE id = $4 AND status = $5 AND execution_result IS NULL "
                "RETURNING *",
                ActionStatus.ABANDONED.value,
                decided_by,
                now,
                parsed_id,
                ActionStatus.APPROVED.value,
            )
            if abandoned_row is None:
                latest_row = await write_target.fetchrow(
                    "SELECT * FROM pending_actions WHERE id = $1", parsed_id
                )
                if latest_row is None:
                    return {"error": f"Action not found: {action_id}"}
                latest_action = PendingAction.from_row(latest_row)
                if latest_action.execution_result is not None:
                    return {
                        "error": "Action already has an execution result and cannot be abandoned"
                    }
                return {
                    "error": (
                        f"Cannot transition from '{latest_action.status.value}' to 'abandoned'"
                    )
                }

            abandoned_action = PendingAction.from_row(abandoned_row)
            await record_approval_event(
                write_target,
                ApprovalEventType.ACTION_ABANDONED,
                actor=_decision_event_actor(actor_id),
                action_id=parsed_id,
                reason=reason.strip(),
                metadata={"tool_name": abandoned_action.tool_name},
                occurred_at=now,
            )
    except Exception as exc:  # noqa: BLE001 -- terminal transition must remain atomic
        logger.error("Could not abandon approval action %s: %s", action_id, exc)
        return {"error": f"Could not abandon approval action: {exc}"}

    return abandoned_action.to_dict()


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
    decision_memory_writer: DecisionMemoryWriter | None = None,
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

    # Bind the sanitized dict directly (no json.dumps, no ::jsonb cast) —
    # asyncpg's registered jsonb codec already serializes once; pre-serializing
    # double-encodes into a jsonb-typed STRING (bu-cymc4/bu-c8b8e; mirrors
    # gate.py's fix, PR #2924).
    safe_arg_constraints = json.loads(json.dumps(rule.arg_constraints, default=str))
    await pool.execute(
        "INSERT INTO approval_rules "
        "(id, tool_name, arg_constraints, description, created_at, "
        "expires_at, max_uses, active) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        rule.id,
        rule.tool_name,
        safe_arg_constraints,
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

    if decision_memory_writer is not None:
        await decision_memory_writer.record_standing_rule(rule, active=True)

    # Rule-creation supersede hook (task 7.3)
    try:
        await _supersede_matching_suggestions(
            pool=pool,
            tool_name=tool_name,
            arg_constraints=arg_constraints,
        )
    except Exception:
        logger.exception("Supersede hook failed for rule %s — rule creation not blocked", rule.id)

    return rule.to_dict()


# ---------------------------------------------------------------------------
# Create rule from action
# ---------------------------------------------------------------------------


async def create_rule_from_action(
    pool: Any,
    action_id: str,
    constraint_overrides: dict[str, Any] | None = None,
    actor_id: str = "dashboard:rest-api",
    decision_memory_writer: DecisionMemoryWriter | None = None,
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

    # Bind the sanitized dict directly (no json.dumps, no ::jsonb cast) —
    # asyncpg's registered jsonb codec already serializes once; pre-serializing
    # double-encodes into a jsonb-typed STRING (bu-cymc4/bu-c8b8e; mirrors
    # gate.py's fix, PR #2924).
    safe_arg_constraints = json.loads(json.dumps(rule.arg_constraints, default=str))
    await pool.execute(
        "INSERT INTO approval_rules "
        "(id, tool_name, arg_constraints, description, created_from, created_at, "
        "max_uses, active) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
        rule.id,
        rule.tool_name,
        safe_arg_constraints,
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

    if decision_memory_writer is not None:
        await decision_memory_writer.record_standing_rule(rule, active=True)

    # Rule-creation supersede hook (task 7.3)
    try:
        await _supersede_matching_suggestions(
            pool=pool,
            tool_name=action.tool_name,
            arg_constraints=suggested,
        )
    except Exception:
        logger.exception("Supersede hook failed for rule %s — rule creation not blocked", rule.id)

    return rule.to_dict()


# ---------------------------------------------------------------------------
# Revoke rule
# ---------------------------------------------------------------------------


async def revoke_approval_rule(
    pool: Any,
    rule_id: str,
    actor_id: str = "dashboard:rest-api",
    decision_memory_writer: DecisionMemoryWriter | None = None,
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
    updated_rule = ApprovalRule.from_row(updated_row)
    if decision_memory_writer is not None:
        await decision_memory_writer.record_standing_rule(updated_rule, active=False)
    return updated_rule.to_dict()
