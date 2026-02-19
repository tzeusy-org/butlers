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

import html
import json
import logging
import uuid
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any

from fastmcp.server.dependencies import AccessToken, get_access_token
from pydantic import BaseModel

from butlers.config import ApprovalConfig, ApprovalRiskTier
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.executor import (
    list_executed_actions as _list_executed_actions_query,
)
from butlers.modules.approvals.models import ActionStatus, ApprovalRule, PendingAction
from butlers.modules.approvals.sensitivity import suggest_constraints
from butlers.modules.base import Module

logger = logging.getLogger(__name__)
_HIGH_RISK_TIERS: frozenset[ApprovalRiskTier] = frozenset(
    {ApprovalRiskTier.HIGH, ApprovalRiskTier.CRITICAL}
)

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
ActorContext = Mapping[str, Any]

_HUMAN_ACTOR_ERROR_CODE = "human_actor_required"
_HUMAN_ACTOR_TYPES = frozenset({"human", "user"})


def _normalize_actor_field(value: Any) -> str | None:
    """Normalize actor field values into stripped strings when present."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _human_actor_error(
    operation: str,
    actor: ActorContext | None,
    reason: str,
) -> dict[str, Any]:
    """Return a stable structured error for denied decision operations."""
    actor_type_raw = actor.get("type") if actor is not None else None
    actor_id_raw = actor.get("id") if actor is not None else None
    authenticated = bool(actor.get("authenticated")) if actor is not None else False

    return {
        "error": reason,
        "error_code": _HUMAN_ACTOR_ERROR_CODE,
        "operation": operation,
        "actor_type": _normalize_actor_field(actor_type_raw),
        "actor_id": _normalize_actor_field(actor_id_raw),
        "authenticated": authenticated,
    }


def _require_authenticated_human_actor(
    operation: str,
    actor: ActorContext | None,
) -> str | dict[str, Any]:
    """Validate a decision actor context and return actor_id when allowed."""
    if actor is None:
        return _human_actor_error(
            operation=operation,
            actor=actor,
            reason="Authenticated human actor context is required.",
        )

    actor_type = _normalize_actor_field(actor.get("type"))
    actor_id = _normalize_actor_field(actor.get("id"))
    authenticated = actor.get("authenticated") is True

    if actor_type not in _HUMAN_ACTOR_TYPES:
        return _human_actor_error(
            operation=operation,
            actor=actor,
            reason="Decision action denied: actor must be human.",
        )

    if not authenticated:
        return _human_actor_error(
            operation=operation,
            actor=actor,
            reason="Decision action denied: actor must be authenticated.",
        )

    if actor_id is None:
        return _human_actor_error(
            operation=operation,
            actor=actor,
            reason="Decision action denied: actor id is required.",
        )

    return actor_id


def _format_manual_decider(actor_id: str, reason: str | None = None) -> str:
    """Build decided_by audit text for manual human decisions."""
    decided_by = f"human:{actor_id}"
    if reason:
        decided_by = f"{decided_by} (reason: {reason})"
    return decided_by


def _actor_from_access_token(access_token: AccessToken | None) -> ActorContext | None:
    """Build actor context from FastMCP access token, if present."""
    if access_token is None:
        return None

    claims = access_token.claims if isinstance(access_token.claims, Mapping) else {}
    resource_owner = _normalize_actor_field(access_token.resource_owner)
    actor_id = (
        resource_owner
        or _normalize_actor_field(claims.get("sub"))
        or _normalize_actor_field(access_token.client_id)
    )
    actor_type = _normalize_actor_field(
        claims.get("actor_type") or claims.get("subject_type") or claims.get("type")
    )

    if actor_type is None and resource_owner is not None:
        actor_type = "human"

    return {
        "type": actor_type,
        "id": actor_id,
        "authenticated": True,
    }


class ApprovalsModule(Module):
    """Approvals module providing human-in-the-loop tool approval MCP tools.

    All tools operate on the butler's own DB via the ``db`` connection pool
    passed during ``register_tools()``.
    """

    def __init__(self) -> None:
        self._config: ApprovalsConfig = ApprovalsConfig()
        self._db: Any = None
        self._tool_executor: ToolExecutor | None = None
        self._approval_policy: ApprovalConfig | None = None

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

    def set_approval_policy(self, policy: ApprovalConfig | None) -> None:
        """Set parsed approval policy metadata used for rule safety enforcement."""
        self._approval_policy = policy

    def _risk_tier_for_tool(self, tool_name: str) -> ApprovalRiskTier:
        """Resolve effective risk tier for a tool."""
        if self._approval_policy is None:
            return ApprovalRiskTier.MEDIUM
        return self._approval_policy.get_effective_risk_tier(tool_name)

    @staticmethod
    def _has_narrow_constraints(arg_constraints: dict[str, Any]) -> bool:
        """Return whether any constraint is exact/pattern (or legacy exact)."""
        if not arg_constraints:
            return False

        for constraint in arg_constraints.values():
            if isinstance(constraint, dict):
                ctype = str(constraint.get("type", "")).lower()
                if ctype in {"exact", "pattern"}:
                    return True
                continue
            if constraint != "*":
                return True
        return False

    def _validate_rule_constraints(
        self,
        *,
        tool_name: str,
        arg_constraints: dict[str, Any],
        expires_at: datetime | None,
        max_uses: int | None,
    ) -> str | None:
        """Validate rule safety constraints against configured risk tier."""
        if max_uses is not None and max_uses <= 0:
            return "max_uses must be greater than 0"

        risk_tier = self._risk_tier_for_tool(tool_name)
        if risk_tier in _HIGH_RISK_TIERS:
            if not self._has_narrow_constraints(arg_constraints):
                return (
                    f"High-risk tool '{tool_name}' requires at least one exact or pattern "
                    "arg constraint"
                )
            if expires_at is None and max_uses is None:
                return (
                    f"High-risk tool '{tool_name}' requires bounded scope via "
                    "'expires_at' or 'max_uses'"
                )
        return None

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
        async def approve_action(
            action_id: str,
            create_rule: bool = False,
        ) -> dict:
            """Approve a pending action and execute it."""
            return await module._approve_action(
                action_id,
                create_rule=create_rule,
                actor=_actor_from_access_token(get_access_token()),
            )

        @mcp.tool()
        async def reject_action(
            action_id: str,
            reason: str | None = None,
        ) -> dict:
            """Reject a pending action with optional reason."""
            return await module._reject_action(
                action_id,
                reason=reason,
                actor=_actor_from_access_token(get_access_token()),
            )

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
                actor=_actor_from_access_token(get_access_token()),
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
                actor=_actor_from_access_token(get_access_token()),
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
            return await module._revoke_approval_rule(
                rule_id,
                actor=_actor_from_access_token(get_access_token()),
            )

        @mcp.tool()
        async def suggest_rule_constraints(action_id: str) -> dict:
            """Preview suggested constraints for creating a rule from a pending action."""
            return await module._suggest_rule_constraints(action_id)

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
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

    async def _approve_action(
        self,
        action_id: str,
        create_rule: bool = False,
        actor: ActorContext | None = None,
    ) -> dict:
        """Approve a pending action, execute it, and optionally create a rule."""
        actor_result = _require_authenticated_human_actor("approve_action", actor)
        if isinstance(actor_result, dict):
            return actor_result
        actor_id = actor_result

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

        # Transition to approved with compare-and-set on pending state.
        approved_row = await self._db.fetchrow(
            "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
            "WHERE id = $4 AND status = $5 "
            "RETURNING *",
            ActionStatus.APPROVED.value,
            _format_manual_decider(actor_id),
            now,
            parsed_id,
            ActionStatus.PENDING.value,
        )
        if approved_row is None:
            latest_row = await self._db.fetchrow(
                "SELECT * FROM pending_actions WHERE id = $1", parsed_id
            )
            if latest_row is None:
                return {"error": f"Action not found: {action_id}"}
            latest_action = PendingAction.from_row(latest_row)
            return {
                "error": (
                    f"Cannot transition from '{latest_action.status.value}' "
                    f"to '{ActionStatus.APPROVED.value}'"
                )
            }
        action = PendingAction.from_row(approved_row)
        await record_approval_event(
            self._db,
            ApprovalEventType.ACTION_APPROVED,
            actor="user:manual",
            action_id=parsed_id,
            reason="approved by operator",
            metadata={"tool_name": action.tool_name},
            occurred_at=now,
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
            executed_row = await self._db.fetchrow(
                "UPDATE pending_actions SET status = $1, execution_result = $2, "
                "decided_at = $3 WHERE id = $4 AND status = $5 RETURNING *",
                ActionStatus.EXECUTED.value,
                None,
                now,
                parsed_id,
                ActionStatus.APPROVED.value,
            )
            if executed_row is None:
                latest_row = await self._db.fetchrow(
                    "SELECT * FROM pending_actions WHERE id = $1", parsed_id
                )
                if latest_row is None:
                    return {"error": f"Action not found: {action_id}"}
                latest_action = PendingAction.from_row(latest_row)
                return {
                    "error": (
                        f"Cannot transition from '{latest_action.status.value}' "
                        f"to '{ActionStatus.EXECUTED.value}'"
                    )
                }

        # Optionally create an approval rule from this action
        rule_dict: dict[str, Any] | None = None
        if create_rule:
            max_uses = 1 if self._risk_tier_for_tool(action.tool_name) in _HIGH_RISK_TIERS else None
            constraint_error = self._validate_rule_constraints(
                tool_name=action.tool_name,
                arg_constraints=action.tool_args,
                expires_at=None,
                max_uses=max_uses,
            )
            if constraint_error is not None:
                final_row = await self._db.fetchrow(
                    "SELECT * FROM pending_actions WHERE id = $1", parsed_id
                )
                result = PendingAction.from_row(final_row).to_dict()
                result["created_rule_error"] = constraint_error
                return result

            rule_id = uuid.uuid4()
            rule = ApprovalRule(
                id=rule_id,
                tool_name=action.tool_name,
                arg_constraints=action.tool_args,
                description=f"Auto-created from approved action {action_id}",
                created_from=parsed_id,
                created_at=now,
                max_uses=max_uses,
            )
            await self._db.execute(
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
                self._db,
                ApprovalEventType.RULE_CREATED,
                actor="user:manual",
                action_id=parsed_id,
                rule_id=rule.id,
                reason="create_rule=true during approve_action",
                metadata={"tool_name": rule.tool_name},
                occurred_at=now,
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

    async def _reject_action(
        self,
        action_id: str,
        reason: str | None = None,
        actor: ActorContext | None = None,
    ) -> dict:
        """Reject a pending action with optional reason."""
        actor_result = _require_authenticated_human_actor("reject_action", actor)
        if isinstance(actor_result, dict):
            return actor_result
        actor_id = actor_result

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
        escaped_reason = html.escape(reason, quote=True) if reason else None
        decided_by = _format_manual_decider(actor_id, reason=escaped_reason)

        rejected_row = await self._db.fetchrow(
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
            latest_row = await self._db.fetchrow(
                "SELECT * FROM pending_actions WHERE id = $1", parsed_id
            )
            if latest_row is None:
                return {"error": f"Action not found: {action_id}"}
            latest_action = PendingAction.from_row(latest_row)
            return {
                "error": (
                    f"Cannot transition from '{latest_action.status.value}' "
                    f"to '{ActionStatus.REJECTED.value}'"
                )
            }
        await record_approval_event(
            self._db,
            ApprovalEventType.ACTION_REJECTED,
            actor="user:manual",
            action_id=parsed_id,
            reason=reason or "rejected by operator",
            metadata={"tool_name": action.tool_name},
            occurred_at=now,
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

            expired_row = await self._db.fetchrow(
                "UPDATE pending_actions SET status = $1, decided_by = $2, decided_at = $3 "
                "WHERE id = $4 AND status = $5 "
                "RETURNING id",
                ActionStatus.EXPIRED.value,
                "system:expiry",
                now,
                action.id,
                ActionStatus.PENDING.value,
            )
            if expired_row is not None:
                await record_approval_event(
                    self._db,
                    ApprovalEventType.ACTION_EXPIRED,
                    actor="system:expiry",
                    action_id=action.id,
                    reason="approval window elapsed",
                    metadata={"tool_name": action.tool_name},
                    occurred_at=now,
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
        actor: ActorContext | None = None,
    ) -> dict:
        """Create a new standing approval rule."""
        actor_result = _require_authenticated_human_actor("create_approval_rule", actor)
        if isinstance(actor_result, dict):
            return actor_result

        rule_id = uuid.uuid4()
        now = datetime.now(UTC)

        # Parse expires_at if provided
        parsed_expires: datetime | None = None
        if expires_at is not None:
            try:
                parsed_expires = datetime.fromisoformat(expires_at)
            except ValueError:
                return {"error": f"Invalid expires_at format: {expires_at}"}

        constraint_error = self._validate_rule_constraints(
            tool_name=tool_name,
            arg_constraints=arg_constraints,
            expires_at=parsed_expires,
            max_uses=max_uses,
        )
        if constraint_error is not None:
            return {"error": constraint_error}

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
        await record_approval_event(
            self._db,
            ApprovalEventType.RULE_CREATED,
            actor="user:manual",
            rule_id=rule.id,
            reason="create_approval_rule",
            metadata={"tool_name": rule.tool_name},
            occurred_at=now,
        )

        return rule.to_dict()

    async def _create_rule_from_action(
        self,
        action_id: str,
        constraint_overrides: dict | None = None,
        actor: ActorContext | None = None,
    ) -> dict:
        """Create a standing rule from a pending action with smart constraint defaults.

        Uses suggest_constraints to generate default arg constraints based on
        sensitivity classification, then applies any user-provided overrides.
        """
        actor_result = _require_authenticated_human_actor("create_rule_from_action", actor)
        if isinstance(actor_result, dict):
            return actor_result

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

        risk_tier = self._risk_tier_for_tool(action.tool_name)
        max_uses = 1 if risk_tier in _HIGH_RISK_TIERS else None
        constraint_error = self._validate_rule_constraints(
            tool_name=action.tool_name,
            arg_constraints=suggested,
            expires_at=None,
            max_uses=max_uses,
        )
        if constraint_error is not None:
            return {"error": constraint_error}

        rule_id = uuid.uuid4()
        now = datetime.now(UTC)

        rule = ApprovalRule(
            id=rule_id,
            tool_name=action.tool_name,
            arg_constraints=suggested,
            description=f"Rule created from action {action_id}",
            created_from=parsed_id,
            created_at=now,
            max_uses=max_uses,
        )

        await self._db.execute(
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
            self._db,
            ApprovalEventType.RULE_CREATED,
            actor="user:manual",
            action_id=parsed_id,
            rule_id=rule.id,
            reason="create_rule_from_action",
            metadata={"tool_name": rule.tool_name},
            occurred_at=now,
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

    async def _revoke_approval_rule(
        self,
        rule_id: str,
        actor: ActorContext | None = None,
    ) -> dict:
        """Revoke (deactivate) a standing approval rule."""
        actor_result = _require_authenticated_human_actor("revoke_approval_rule", actor)
        if isinstance(actor_result, dict):
            return actor_result

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
        await record_approval_event(
            self._db,
            ApprovalEventType.RULE_REVOKED,
            actor="user:manual",
            rule_id=parsed_id,
            reason="rule revoked by operator",
            metadata={"tool_name": rule.tool_name},
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
