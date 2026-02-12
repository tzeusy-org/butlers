"""Approvals module — MCP tools for managing the pending action approval queue.

Provides thirteen tools:
- list_pending_actions: list actions with optional status filter
- show_pending_action: show full details for a single action
- approve_action: approve and execute a pending action
- reject_action: reject with optional reason
- pending_action_count: count of pending actions
- expire_stale_actions: mark expired actions past their expires_at
- create_approval_rule: create a new standing approval rule
- create_rule_from_action: create a rule from a pending action with smart defaults
- list_approval_rules: list standing approval rules
- show_approval_rule: show full rule details with use_count
- revoke_approval_rule: deactivate a standing approval rule
- suggest_rule_constraints: preview suggested constraints for a pending action
- list_executed_actions: query executed actions for audit review
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.executor import (
    list_executed_actions as _list_executed_actions_query,
)
from butlers.modules.approvals.models import ActionStatus, ApprovalRule, PendingAction
from butlers.modules.approvals.sensitivity import suggest_constraints
from butlers.modules.base import Module

logger = logging.getLogger(__name__)

# Valid status transitions: source -> set of valid targets
_VALID_TRANSITIONS: dict[ActionStatus, set[ActionStatus]] = {
    ActionStatus.PENDING: {ActionStatus.APPROVED, ActionStatus.REJECTED, ActionStatus.EXPIRED},
    ActionStatus.APPROVED: {ActionStatus.EXECUTED},
    ActionStatus.REJECTED: set(),
    ActionStatus.EXPIRED: set(),
    ActionStatus.EXECUTED: set(),
}


class InvalidTransitionError(Exception):
    """Raised when an invalid status transition is attempted."""


def validate_transition(current: ActionStatus, target: ActionStatus) -> None:
    """Validate that a status transition is allowed.

    Raises InvalidTransitionError if the transition is not in the valid set.
    """
    valid = _VALID_TRANSITIONS.get(current, set())
    if target not in valid:
        raise InvalidTransitionError(
            f"Cannot transition from '{current.value}' to '{target.value}'"
        )


class ApprovalsConfig(BaseModel):
    """Configuration for the Approvals module."""

    default_limit: int = 50


# Type alias for tool executor callbacks
ToolExecutor = Callable[[str, dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


class ApprovalsModule(Module):
    """Approvals module providing human-in-the-loop tool approval MCP tools.

    All tools operate on the butler's own DB via the ``db`` connection pool
    passed during ``register_tools()``.
    """

    def __init__(self) -> None:
        self._config: ApprovalsConfig = ApprovalsConfig()
        self._db: Any = None
        self._tool_executor: ToolExecutor | None = None

    @property
    def name(self) -> str:
        return "approvals"

    @property
    def config_schema(self) -> type[BaseModel]:
        return ApprovalsConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return "approvals"

    def set_tool_executor(self, executor: ToolExecutor) -> None:
        """Set the callback used by approve_action to execute the original tool.

        The executor receives (tool_name, tool_args) and returns a result dict.
        This is designed to be wired up by the MCP dispatch interception layer
        (task clc.4). Until then, approve_action will store the approval but
        skip execution if no executor is set.
        """
        self._tool_executor = executor

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all 13 approval MCP tools (7 queue + 6 rules CRUD)."""
        self._config = (
            config if isinstance(config, ApprovalsConfig) else ApprovalsConfig(**(config or {}))
        )
        self._db = db
        module = self  # capture for closures

        # --- Approval queue tools (6) ---

        @mcp.tool()
        async def list_pending_actions(
            status: str | None = None, limit: int | None = None
        ) -> list[dict]:
            """List pending actions with optional status filter and limit."""
            return await module._list_pending_actions(status=status, limit=limit)

        @mcp.tool()
        async def show_pending_action(action_id: str) -> dict:
            """Show full details for a single pending action."""
            return await module._show_pending_action(action_id)

        @mcp.tool()
        async def approve_action(action_id: str, create_rule: bool = False) -> dict:
            """Approve a pending action and execute it."""
            return await module._approve_action(action_id, create_rule=create_rule)

        @mcp.tool()
        async def reject_action(action_id: str, reason: str | None = None) -> dict:
            """Reject a pending action with optional reason."""
            return await module._reject_action(action_id, reason=reason)

        @mcp.tool()
        async def pending_action_count() -> dict:
            """Return count of pending actions."""
            return await module._pending_action_count()

        @mcp.tool()
        async def expire_stale_actions() -> dict:
            """Mark expired actions that are past their expires_at."""
            return await module._expire_stale_actions()

        @mcp.tool()
        async def list_executed_actions(
            tool_name: str | None = None,
            rule_id: str | None = None,
            since: str | None = None,
            limit: int | None = None,
        ) -> list[dict]:
            """List executed actions for audit review with optional filters."""
            return await module._list_executed_actions_tool(
                tool_name=tool_name, rule_id=rule_id, since=since, limit=limit
            )

        # --- Standing approval rules CRUD tools (6) ---

        @mcp.tool()
        async def create_approval_rule(
            tool_name: str,
            arg_constraints: dict,
            description: str,
            expires_at: str | None = None,
            max_uses: int | None = None,
        ) -> dict:
            """Create a new standing approval rule for auto-approving tool invocations."""
            return await module._create_approval_rule(
                tool_name=tool_name,
                arg_constraints=arg_constraints,
                description=description,
                expires_at=expires_at,
                max_uses=max_uses,
            )

        @mcp.tool()
        async def create_rule_from_action(
            action_id: str,
            constraint_overrides: dict | None = None,
        ) -> dict:
            """Create a standing rule from a pending action with smart constraint defaults."""
            return await module._create_rule_from_action(
                action_id=action_id,
                constraint_overrides=constraint_overrides,
            )

        @mcp.tool()
        async def list_approval_rules(
            tool_name: str | None = None,
            active_only: bool = True,
        ) -> list[dict]:
            """List standing approval rules with optional filters."""
            return await module._list_approval_rules(
                tool_name=tool_name,
                active_only=active_only,
            )

        @mcp.tool()
        async def show_approval_rule(rule_id: str) -> dict:
            """Show full details for a single standing approval rule."""
            return await module._show_approval_rule(rule_id)

        @mcp.tool()
        async def revoke_approval_rule(rule_id: str) -> dict:
            """Revoke (deactivate) a standing approval rule."""
            return await module._revoke_approval_rule(rule_id)

        @mcp.tool()
        async def suggest_rule_constraints(action_id: str) -> dict:
            """Preview suggested constraints for creating a rule from a pending action."""
            return await module._suggest_rule_constraints(action_id)

    async def on_startup(self, config: Any, db: Any) -> None:
        """Initialize config and store db reference."""
        self._config = (
            config if isinstance(config, ApprovalsConfig) else ApprovalsConfig(**(config or {}))
        )
        self._db = db

    async def on_shutdown(self) -> None:
        """No persistent resources to clean up."""
        pass

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _list_pending_actions(
        self, status: str | None = None, limit: int | None = None
    ) -> list[dict]:
        """List pending actions with optional filters."""
        effective_limit = limit if limit is not None else self._config.default_limit

        if status is not None:
            # Validate the status value
            try:
                ActionStatus(status)
            except ValueError:
                return [{"error": f"Invalid status: {status}"}]

            query = (
                "SELECT * FROM pending_actions WHERE status = $1 "
                "ORDER BY requested_at DESC LIMIT $2"
            )
            rows = await self._db.fetch(query, status, effective_limit)
        else:
            query = "SELECT * FROM pending_actions ORDER BY requested_at DESC LIMIT $1"
            rows = await self._db.fetch(query, effective_limit)

        return [PendingAction.from_row(row).to_dict() for row in rows]

    async def _show_pending_action(self, action_id: str) -> dict:
        """Show full details for a single pending action."""
        try:
            parsed_id = uuid.UUID(action_id)
        except ValueError:
            return {"error": f"Invalid action_id: {action_id}"}

        row = await self._db.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if row is None:
            return {"error": f"Action not found: {action_id}"}

        return PendingAction.from_row(row).to_dict()

    async def _approve_action(self, action_id: str, create_rule: bool = False) -> dict:
        """Approve a pending action, execute it, and optionally create a rule."""
        try:
            parsed_id = uuid.UUID(action_id)
        except ValueError:
            return {"error": f"Invalid action_id: {action_id}"}

        row = await self._db.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if row is None:
            return {"error": f"Action not found: {action_id}"}

        action = PendingAction.from_row(row)

        # Validate transition: pending -> approved
        try:
            validate_transition(action.status, ActionStatus.APPROVED)
        except InvalidTransitionError as exc:
            return {"error": str(exc)}

        now = datetime.now(UTC)

        # Transition to approved
        await self._db.execute(
            "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
            "WHERE id = $4",
            ActionStatus.APPROVED.value,
            "user:manual",
            now,
            parsed_id,
        )

        # Execute the original tool via the executor
        if self._tool_executor is not None:
            # Wrap the ToolExecutor callback as a tool_fn for the executor
            _exec = self._tool_executor
            _tname = action.tool_name

            async def _tool_fn(**kwargs: Any) -> dict[str, Any]:
                return await _exec(_tname, kwargs)

            await execute_approved_action(
                pool=self._db,
                action_id=parsed_id,
                tool_name=action.tool_name,
                tool_args=action.tool_args,
                tool_fn=_tool_fn,
            )
        else:
            # No executor — still mark as executed (with no execution result)
            await self._db.execute(
                "UPDATE pending_actions SET status = $1, execution_result = $2, "
                "decided_at = $3 WHERE id = $4",
                ActionStatus.EXECUTED.value,
                None,
                now,
                parsed_id,
            )

        # Optionally create an approval rule from this action
        rule_dict: dict[str, Any] | None = None
        if create_rule:
            rule_id = uuid.uuid4()
            rule = ApprovalRule(
                id=rule_id,
                tool_name=action.tool_name,
                arg_constraints=action.tool_args,
                description=f"Auto-created from approved action {action_id}",
                created_from=parsed_id,
                created_at=now,
            )
            await self._db.execute(
                "INSERT INTO approval_rules "
                "(id, tool_name, arg_constraints, description, created_from, created_at, "
                "active) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7)",
                rule.id,
                rule.tool_name,
                json.dumps(rule.arg_constraints),
                rule.description,
                rule.created_from,
                rule.created_at,
                rule.active,
            )
            rule_dict = rule.to_dict()

        # Re-read the final state
        final_row = await self._db.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1", parsed_id
        )
        result = PendingAction.from_row(final_row).to_dict()
        if rule_dict is not None:
            result["created_rule"] = rule_dict

        return result

    async def _reject_action(self, action_id: str, reason: str | None = None) -> dict:
        """Reject a pending action with optional reason."""
        try:
            parsed_id = uuid.UUID(action_id)
        except ValueError:
            return {"error": f"Invalid action_id: {action_id}"}

        row = await self._db.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if row is None:
            return {"error": f"Action not found: {action_id}"}

        action = PendingAction.from_row(row)

        # Validate transition: pending -> rejected
        try:
            validate_transition(action.status, ActionStatus.REJECTED)
        except InvalidTransitionError as exc:
            return {"error": str(exc)}

        now = datetime.now(UTC)

        # Build decided_by with optional reason
        decided_by = "user:manual"
        if reason:
            decided_by = f"user:manual (reason: {reason})"

        await self._db.execute(
            "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
            "WHERE id = $4",
            ActionStatus.REJECTED.value,
            decided_by,
            now,
            parsed_id,
        )

        final_row = await self._db.fetchrow(
            "SELECT * FROM pending_actions WHERE id = $1", parsed_id
        )
        return PendingAction.from_row(final_row).to_dict()

    async def _pending_action_count(self) -> dict:
        """Return counts of pending actions by status."""
        rows = await self._db.fetch(
            "SELECT status, COUNT(*) as count FROM pending_actions GROUP BY status"
        )
        counts = {row["status"]: row["count"] for row in rows}
        total = sum(counts.values())
        return {
            "total": total,
            "by_status": counts,
        }

    async def _expire_stale_actions(self) -> dict:
        """Mark actions past their expires_at as expired."""
        now = datetime.now(UTC)

        # Find all pending actions that have expired
        rows = await self._db.fetch(
            "SELECT * FROM pending_actions WHERE status = $1 AND expires_at IS NOT NULL "
            "AND expires_at < $2",
            ActionStatus.PENDING.value,
            now,
        )

        expired_ids: list[str] = []
        for row in rows:
            action = PendingAction.from_row(row)
            try:
                validate_transition(action.status, ActionStatus.EXPIRED)
            except InvalidTransitionError:
                continue

            await self._db.execute(
                "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
                "WHERE id = $4",
                ActionStatus.EXPIRED.value,
                "system:expiry",
                now,
                action.id,
            )
            expired_ids.append(str(action.id))

        return {
            "expired_count": len(expired_ids),
            "expired_ids": expired_ids,
        }

    # ------------------------------------------------------------------
    # Standing approval rules CRUD implementations
    # ------------------------------------------------------------------

    async def _create_approval_rule(
        self,
        tool_name: str,
        arg_constraints: dict,
        description: str,
        expires_at: str | None = None,
        max_uses: int | None = None,
    ) -> dict:
        """Create a new standing approval rule."""
        rule_id = uuid.uuid4()
        now = datetime.now(UTC)

        # Parse expires_at if provided
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

        await self._db.execute(
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

        return rule.to_dict()

    async def _create_rule_from_action(
        self,
        action_id: str,
        constraint_overrides: dict | None = None,
    ) -> dict:
        """Create a standing rule from a pending action with smart constraint defaults.

        Uses suggest_constraints to generate default arg constraints based on
        sensitivity classification, then applies any user-provided overrides.
        """
        try:
            parsed_id = uuid.UUID(action_id)
        except ValueError:
            return {"error": f"Invalid action_id: {action_id}"}

        row = await self._db.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if row is None:
            return {"error": f"Action not found: {action_id}"}

        action = PendingAction.from_row(row)

        # Generate suggested constraints
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

        await self._db.execute(
            "INSERT INTO approval_rules "
            "(id, tool_name, arg_constraints, description, created_from, created_at, active) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            rule.id,
            rule.tool_name,
            json.dumps(rule.arg_constraints),
            rule.description,
            rule.created_from,
            rule.created_at,
            rule.active,
        )

        return rule.to_dict()

    async def _list_approval_rules(
        self,
        tool_name: str | None = None,
        active_only: bool = True,
    ) -> list[dict]:
        """List standing approval rules with optional filters."""
        if tool_name is not None and active_only:
            query = (
                "SELECT * FROM approval_rules WHERE tool_name = $1 AND active = true "
                "ORDER BY created_at DESC"
            )
            rows = await self._db.fetch(query, tool_name)
        elif tool_name is not None:
            query = "SELECT * FROM approval_rules WHERE tool_name = $1 ORDER BY created_at DESC"
            rows = await self._db.fetch(query, tool_name)
        elif active_only:
            query = "SELECT * FROM approval_rules WHERE active = true ORDER BY created_at DESC"
            rows = await self._db.fetch(query)
        else:
            query = "SELECT * FROM approval_rules ORDER BY created_at DESC"
            rows = await self._db.fetch(query)

        return [ApprovalRule.from_row(row).to_dict() for row in rows]

    async def _show_approval_rule(self, rule_id: str) -> dict:
        """Show full details for a single standing approval rule."""
        try:
            parsed_id = uuid.UUID(rule_id)
        except ValueError:
            return {"error": f"Invalid rule_id: {rule_id}"}

        row = await self._db.fetchrow("SELECT * FROM approval_rules WHERE id = $1", parsed_id)
        if row is None:
            return {"error": f"Rule not found: {rule_id}"}

        return ApprovalRule.from_row(row).to_dict()

    async def _revoke_approval_rule(self, rule_id: str) -> dict:
        """Revoke (deactivate) a standing approval rule."""
        try:
            parsed_id = uuid.UUID(rule_id)
        except ValueError:
            return {"error": f"Invalid rule_id: {rule_id}"}

        row = await self._db.fetchrow("SELECT * FROM approval_rules WHERE id = $1", parsed_id)
        if row is None:
            return {"error": f"Rule not found: {rule_id}"}

        rule = ApprovalRule.from_row(row)
        if not rule.active:
            return {"error": f"Rule {rule_id} is already revoked"}

        await self._db.execute(
            "UPDATE approval_rules SET active = $1 WHERE id = $2",
            False,
            parsed_id,
        )

        # Re-read to return updated state
        updated_row = await self._db.fetchrow(
            "SELECT * FROM approval_rules WHERE id = $1", parsed_id
        )
        return ApprovalRule.from_row(updated_row).to_dict()

    async def _list_executed_actions_tool(
        self,
        tool_name: str | None = None,
        rule_id: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """List executed actions for audit review with optional filters."""
        parsed_rule_id: uuid.UUID | None = None
        if rule_id is not None:
            try:
                parsed_rule_id = uuid.UUID(rule_id)
            except ValueError:
                return [{"error": f"Invalid rule_id: {rule_id}"}]

        parsed_since: datetime | None = None
        if since is not None:
            try:
                parsed_since = datetime.fromisoformat(since)
            except ValueError:
                return [{"error": f"Invalid since format: {since}"}]

        effective_limit = limit if limit is not None else self._config.default_limit

        return await _list_executed_actions_query(
            pool=self._db,
            tool_name=tool_name,
            rule_id=parsed_rule_id,
            since=parsed_since,
            limit=effective_limit,
        )

    async def _suggest_rule_constraints(self, action_id: str) -> dict:
        """Preview suggested constraints for creating a rule from a pending action.

        Returns the suggested constraints without actually creating the rule.
        """
        try:
            parsed_id = uuid.UUID(action_id)
        except ValueError:
            return {"error": f"Invalid action_id: {action_id}"}

        row = await self._db.fetchrow("SELECT * FROM pending_actions WHERE id = $1", parsed_id)
        if row is None:
            return {"error": f"Action not found: {action_id}"}

        action = PendingAction.from_row(row)

        suggested = suggest_constraints(action.tool_name, action.tool_args)

        return {
            "action_id": str(action.id),
            "tool_name": action.tool_name,
            "tool_args": action.tool_args,
            "suggested_constraints": suggested,
        }
