"""Post-approval tool executor -- executes approved actions and logs results.

Provides a standalone executor that:
1. Calls the original tool function with the deserialized args
2. Captures the result or exception
3. Updates the PendingAction with execution_result and status='executed'
4. Increments rule use_count for auto-approved actions
5. Returns an ExecutionResult for the caller

Both manual approval (from module.py) and auto-approval (from gate.py)
should use this executor for consistent audit logging.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
import weakref
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.models import ActionStatus

logger = logging.getLogger(__name__)
_EXECUTION_LOCKS: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)
_EXECUTION_LOCKS_GUARD = asyncio.Lock()


@dataclass
class ExecutionResult:
    """Outcome of executing an approved action."""

    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    executed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary."""
        d: dict[str, Any] = {
            "success": self.success,
            "executed_at": self.executed_at.isoformat(),
        }
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d


async def _get_execution_lock(action_id: uuid.UUID) -> asyncio.Lock:
    """Return a process-local lock for the given action ID."""
    async with _EXECUTION_LOCKS_GUARD:
        lock = _EXECUTION_LOCKS.get(action_id)
        if lock is None:
            lock = asyncio.Lock()
            _EXECUTION_LOCKS[action_id] = lock
        return lock


def _parse_execution_result(raw_payload: Any) -> ExecutionResult | None:
    """Deserialize a stored execution_result payload into ExecutionResult."""
    if raw_payload is None:
        return None

    payload: Any = raw_payload
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None

    if not isinstance(payload, dict) or "success" not in payload:
        return None

    executed_at = datetime.now(UTC)
    raw_executed_at = payload.get("executed_at")
    if isinstance(raw_executed_at, str):
        try:
            executed_at = datetime.fromisoformat(raw_executed_at)
            if executed_at.tzinfo is None:
                executed_at = executed_at.replace(tzinfo=UTC)
        except ValueError:
            executed_at = datetime.now(UTC)

    raw_result = payload.get("result")
    result: dict[str, Any] | None
    if isinstance(raw_result, dict):
        result = raw_result
    elif raw_result is None:
        result = None
    else:
        result = {"value": raw_result}

    raw_error = payload.get("error")
    error = raw_error if isinstance(raw_error, str) else None

    return ExecutionResult(
        success=bool(payload["success"]),
        result=result,
        error=error,
        executed_at=executed_at,
    )


async def execute_approved_action(
    pool: Any,
    action_id: uuid.UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_fn: Any,
    approval_rule_id: uuid.UUID | None = None,
) -> ExecutionResult:
    """Execute an approved action and persist the result.

    Calls ``tool_fn(**tool_args)``, captures the result or exception,
    updates the ``pending_actions`` table, and increments rule ``use_count``
    if auto-approved.

    Parameters
    ----------
    pool:
        asyncpg connection pool for the butler's database.
    action_id:
        UUID of the PendingAction to execute.
    tool_name:
        Name of the tool being executed (for logging).
    tool_args:
        Keyword arguments to pass to the tool function.
    tool_fn:
        The original tool function to invoke.
    approval_rule_id:
        If set, the action was auto-approved by this rule; its use_count
        will be incremented.

    Returns
    -------
    ExecutionResult
        Outcome containing success flag and result or error.
    """
    lock = await _get_execution_lock(action_id)

    async with lock:
        existing_row = await pool.fetchrow(
            "SELECT status, execution_result FROM pending_actions WHERE id = $1",
            action_id,
        )
        if existing_row is None:
            return ExecutionResult(success=False, error=f"Action not found: {action_id}")

        existing_status = existing_row["status"]
        if existing_status == ActionStatus.EXECUTED.value:
            replay = _parse_execution_result(existing_row.get("execution_result"))
            if replay is not None:
                logger.debug("Replay executed result for action %s (%s)", action_id, tool_name)
                return replay
            return ExecutionResult(
                success=False,
                error=f"Action {action_id} already executed without a replayable result",
            )

        if existing_status != ActionStatus.APPROVED.value:
            return ExecutionResult(
                success=False,
                error=f"Action {action_id} is not executable from status '{existing_status}'",
            )

        now = datetime.now(UTC)

        # 1. Call the tool function
        try:
            raw_result = tool_fn(**tool_args)
            # Support both sync and async tool functions
            if inspect.isawaitable(raw_result):
                raw_result = await raw_result

            # Normalise the result to a dict
            if isinstance(raw_result, dict):
                result_dict = raw_result
            else:
                result_dict = {"value": raw_result}

            execution_result = ExecutionResult(
                success=True,
                result=result_dict,
                executed_at=now,
            )
        except Exception as exc:
            logger.error(
                "Tool execution failed for action %s (%s): %s",
                action_id,
                tool_name,
                exc,
            )
            execution_result = ExecutionResult(
                success=False,
                error=str(exc),
                executed_at=now,
            )

        # 2. Build the execution_result JSONB payload
        er_json = json.dumps(execution_result.to_dict())

        # 3. Update the pending_action row to 'executed' (CAS on approved)
        await pool.execute(
            "UPDATE pending_actions "
            "SET status = $1, execution_result = $2, decided_at = $3 "
            "WHERE id = $4 AND status = $5",
            ActionStatus.EXECUTED.value,
            er_json,
            now,
            action_id,
            ActionStatus.APPROVED.value,
        )

        # 4. If auto-approved, increment rule use_count
        if approval_rule_id is not None:
            await pool.execute(
                "UPDATE approval_rules SET use_count = use_count + 1 WHERE id = $1",
                approval_rule_id,
            )

    logger.info(
        "Executed action %s (%s) success=%s rule=%s",
        action_id,
        tool_name,
        execution_result.success,
        approval_rule_id,
    )
    await record_approval_event(
        pool,
        (
            ApprovalEventType.ACTION_EXECUTION_SUCCEEDED
            if execution_result.success
            else ApprovalEventType.ACTION_EXECUTION_FAILED
        ),
        actor="system:executor",
        action_id=action_id,
        rule_id=approval_rule_id,
        reason=execution_result.error,
        metadata={"tool_name": tool_name},
        occurred_at=now,
    )

    return execution_result


async def list_executed_actions(
    pool: Any,
    tool_name: str | None = None,
    rule_id: uuid.UUID | None = None,
    since: datetime | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query executed actions for audit review.

    Supports filtering by ``tool_name``, ``approval_rule_id``, and date range.
    Returns a list of PendingAction dicts with execution details.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    tool_name:
        Filter to actions for this tool only.
    rule_id:
        Filter to actions auto-approved by this rule.
    since:
        Only return actions executed after this timestamp.
    limit:
        Maximum number of rows to return (default 50).

    Returns
    -------
    list[dict]
        List of PendingAction dicts ordered by decided_at descending.
    """
    conditions: list[str] = ["status = $1"]
    params: list[Any] = [ActionStatus.EXECUTED.value]
    idx = 2  # next positional parameter

    if tool_name is not None:
        conditions.append(f"tool_name = ${idx}")
        params.append(tool_name)
        idx += 1

    if rule_id is not None:
        conditions.append(f"approval_rule_id = ${idx}")
        params.append(rule_id)
        idx += 1

    if since is not None:
        conditions.append(f"decided_at >= ${idx}")
        params.append(since)
        idx += 1

    where_clause = " AND ".join(conditions)
    query = (
        f"SELECT * FROM pending_actions WHERE {where_clause} ORDER BY decided_at DESC LIMIT ${idx}"
    )
    params.append(limit)

    rows = await pool.fetch(query, *params)

    from butlers.modules.approvals.models import PendingAction

    return [PendingAction.from_row(row).to_dict() for row in rows]
