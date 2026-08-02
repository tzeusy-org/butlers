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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.models import ActionStatus

if TYPE_CHECKING:
    from butlers.modules.approvals.decision_memory import DecisionMemoryWriter

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


@asynccontextmanager
async def _approval_write_transaction(pool: Any) -> AsyncIterator[Any]:
    """Yield a write target with a transaction when the runtime exposes one.

    Modules receive either an asyncpg pool/connection or the project's
    lightweight ``Database`` proxy. Test doubles intentionally need no
    transaction API, so they keep the same ordered writes while production
    pools atomically commit the terminal result and immutable audit event.
    """
    backing_pool = getattr(pool, "pool", None)
    target = backing_pool if backing_pool is not None else pool
    acquire = getattr(target, "acquire", None)
    if callable(acquire):
        async with acquire() as connection:
            transaction = getattr(connection, "transaction", None)
            if callable(transaction):
                async with connection.transaction():
                    yield connection
            else:
                yield connection
        return

    transaction = getattr(target, "transaction", None)
    if callable(transaction):
        async with transaction():
            yield target
        return

    yield target


async def execute_approved_action(
    pool: Any,
    action_id: uuid.UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_fn: Any,
    approval_rule_id: uuid.UUID | None = None,
    decision_memory_writer: DecisionMemoryWriter | None = None,
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
    decision_memory_writer:
        Optional owning-memory writer invoked after the execution outcome is
        committed and audited. Its failures are intentionally non-blocking.

    Returns
    -------
    ExecutionResult
        Outcome containing success flag and result or error.
    """
    lock = await _get_execution_lock(action_id)

    async with lock:
        # Hold a database row lock from the eligibility check through the tool
        # invocation and terminal write.  The process-local lock protects one
        # daemon; this lock also makes Retry and dashboard-only Abandon mutually
        # exclusive across daemon processes.  In particular, Abandon's CAS
        # update waits here instead of marking the row terminal while a handler
        # is already allowed to perform its side effect.
        failed_execution: ExecutionResult | None = None
        now = datetime.now(UTC)
        try:
            async with _approval_write_transaction(pool) as write_target:
                existing_row = await write_target.fetchrow(
                    "SELECT status, execution_result FROM pending_actions WHERE id = $1 FOR UPDATE",
                    action_id,
                )
                if existing_row is None:
                    return ExecutionResult(success=False, error=f"Action not found: {action_id}")

                existing_status = existing_row["status"]
                if existing_status == ActionStatus.EXECUTED.value:
                    replay = _parse_execution_result(existing_row.get("execution_result"))
                    if replay is not None:
                        logger.debug(
                            "Replay executed result for action %s (%s)", action_id, tool_name
                        )
                        return replay
                    return ExecutionResult(
                        success=False,
                        error=f"Action {action_id} already executed without a replayable result",
                    )

                if existing_status != ActionStatus.APPROVED.value:
                    return ExecutionResult(
                        success=False,
                        error=(
                            f"Action {action_id} is not executable from status '{existing_status}'"
                        ),
                    )

                try:
                    raw_result = tool_fn(**tool_args)
                    if inspect.isawaitable(raw_result):
                        raw_result = await raw_result
                    # MCP tool contracts use an error dict for an unsuccessful
                    # operation. Treat it exactly like an exception so a handler that
                    # reports its own failure cannot be recorded as `executed`.
                    if isinstance(raw_result, dict):
                        if raw_result.get("error"):
                            raise RuntimeError(str(raw_result["error"]))
                        if raw_result.get("success") is False:
                            raise RuntimeError("tool reported unsuccessful execution")
                except Exception as exc:
                    failed_execution = ExecutionResult(
                        success=False,
                        error=str(exc),
                        executed_at=now,
                    )
                    logger.error(
                        "Tool execution failed for action %s (%s): %s",
                        action_id,
                        tool_name,
                        exc,
                    )
                else:
                    # Normalise the successful result and persist the result,
                    # terminal status, immutable audit event, and auto-rule use
                    # count atomically before releasing the row lock.
                    result_dict = (
                        raw_result if isinstance(raw_result, dict) else {"value": raw_result}
                    )
                    execution_result = ExecutionResult(
                        success=True, result=result_dict, executed_at=now
                    )
                    safe_execution_result = json.loads(
                        json.dumps(execution_result.to_dict(), default=str)
                    )
                    transition_result = await write_target.execute(
                        "UPDATE pending_actions "
                        "SET status = $1, execution_result = $2, decided_at = $3 "
                        "WHERE id = $4 AND status = $5",
                        ActionStatus.EXECUTED.value,
                        safe_execution_result,
                        now,
                        action_id,
                        ActionStatus.APPROVED.value,
                    )
                    transitioned_to_executed = transition_result is None or str(
                        transition_result
                    ).endswith(" 1")
                    if not transitioned_to_executed:
                        return ExecutionResult(
                            success=False,
                            error=(
                                f"Action {action_id} was no longer approved while "
                                "persisting execution"
                            ),
                            executed_at=now,
                        )

                    await record_approval_event(
                        write_target,
                        ApprovalEventType.ACTION_EXECUTION_SUCCEEDED,
                        actor="system:executor",
                        action_id=action_id,
                        rule_id=approval_rule_id,
                        metadata={"tool_name": tool_name},
                        occurred_at=now,
                    )

                    if approval_rule_id is not None:
                        await write_target.execute(
                            "UPDATE approval_rules SET use_count = use_count + 1 WHERE id = $1",
                            approval_rule_id,
                        )
        except Exception as exc:
            logger.error(
                "Could not persist successful execution for action %s (%s): %s",
                action_id,
                tool_name,
                exc,
            )
            return ExecutionResult(
                success=False,
                error=f"Could not persist execution outcome: {exc}",
                executed_at=now,
            )

        if failed_execution is not None:
            # A failed handler remains retryable. Record its audit event after
            # releasing the database row lock; a failed audit must not turn a
            # known handler failure into a different terminal outcome.
            try:
                async with _approval_write_transaction(pool) as write_target:
                    await record_approval_event(
                        write_target,
                        ApprovalEventType.ACTION_EXECUTION_FAILED,
                        actor="system:executor",
                        action_id=action_id,
                        rule_id=approval_rule_id,
                        reason=failed_execution.error,
                        metadata={"tool_name": tool_name},
                        occurred_at=failed_execution.executed_at,
                    )
            except Exception:  # noqa: BLE001 -- failure audit cannot change queue state
                logger.warning(
                    "Could not audit failed execution for action %s; action remains approved",
                    action_id,
                    exc_info=True,
                )

            if approval_rule_id is not None:
                try:
                    from butlers.modules.approvals.autonomy_suggestions import (
                        create_demotion_suggestion,
                    )
                    from butlers.modules.approvals.models import PendingAction

                    action_row = await pool.fetchrow(
                        "SELECT * FROM pending_actions WHERE id = $1", action_id
                    )
                    if action_row is not None:
                        await create_demotion_suggestion(
                            pool=pool,
                            action=PendingAction.from_row(action_row),
                            rule_id=approval_rule_id,
                            error_details=failed_execution.error or "unknown error",
                        )
                except Exception:  # noqa: BLE001 -- demotion is best-effort
                    logger.exception(
                        "Demotion suggestion hook failed for action %s; action remains approved",
                        action_id,
                    )
            return failed_execution

    logger.info(
        "Executed action %s (%s) success=True rule=%s",
        action_id,
        tool_name,
        approval_rule_id,
    )

    # Decision memory is a best-effort knowledge dividend, not part of the
    # execution transaction. It runs only after the terminal state and audit
    # outcome are durable, and the writer itself fails open.
    if decision_memory_writer is not None:
        try:
            from butlers.modules.approvals.models import PendingAction

            action_row = await pool.fetchrow(
                "SELECT * FROM pending_actions WHERE id = $1", action_id
            )
            if action_row is not None:
                await decision_memory_writer.record_terminal_decision(
                    PendingAction.from_row(action_row), "approved"
                )
        except Exception:  # noqa: BLE001 -- writeback must never affect execution
            logger.warning(
                "Decision-memory executor hook failed for action %s; execution remains committed",
                action_id,
                exc_info=True,
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
