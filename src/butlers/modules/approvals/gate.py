"""Approval gate — MCP tool dispatch interception for approval-gated tools.

Wraps gated tools at MCP registration time so that:
1. When a gated tool is called, the call is serialized into a PendingAction.
2. Standing approval rules are checked — if a rule matches, the tool
   is auto-approved and executed immediately as pre-approval delegated
   by the rule's authenticated human owner.
3. If no rule matches, the PendingAction is persisted with status='pending'
   and a structured ``pending_approval`` response is returned to CC.

The wrapping happens at the FastMCP level: tools remain completely unaware
of the approval layer. The original tool function is preserved so it can
be invoked directly after post-approval (by task clc.7).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.config import ApprovalConfig
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus

logger = logging.getLogger(__name__)


def match_standing_rule(
    tool_name: str,
    tool_args: dict[str, Any],
    rules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Check whether any standing approval rule matches this invocation.

    Rules match when:
    - ``tool_name`` matches the rule's tool_name
    - The rule is active and not expired (``expires_at`` is None or in the future)
    - The rule's use_count < max_uses (or max_uses is None for unlimited)
    - Every key/value in ``arg_constraints`` matches the corresponding tool arg.
      The special value ``"*"`` matches any value for that key.
      Empty ``arg_constraints`` ({}) matches all invocations of that tool.

    Parameters
    ----------
    tool_name:
        The name of the tool being invoked.
    tool_args:
        The arguments passed to the tool.
    rules:
        List of rule dicts (from DB fetch), pre-filtered to active rules
        for this tool_name.

    Returns
    -------
    dict | None
        The first matching rule dict, or None if no rule matches.
    """
    now = datetime.now(UTC)

    for rule in rules:
        # Tool name must match (should already be filtered, but be safe)
        if rule["tool_name"] != tool_name:
            continue

        # Check active flag
        if not rule.get("active", True):
            continue

        # Check expiry
        expires_at = rule.get("expires_at")
        if expires_at is not None and expires_at < now:
            continue

        # Check max_uses
        max_uses = rule.get("max_uses")
        use_count = rule.get("use_count", 0)
        if max_uses is not None and use_count >= max_uses:
            continue

        # Check arg constraints
        constraints_raw = rule.get("arg_constraints", "{}")
        if isinstance(constraints_raw, str):
            constraints = json.loads(constraints_raw)
        else:
            constraints = constraints_raw

        if _args_match_constraints(tool_args, constraints):
            return rule

    return None


def _args_match_constraints(
    tool_args: dict[str, Any],
    constraints: dict[str, Any],
) -> bool:
    """Check whether tool_args satisfy all constraint entries.

    Empty constraints ({}) match any args. The wildcard value ``"*"``
    matches any value for that key.
    """
    for key, expected in constraints.items():
        actual = tool_args.get(key)
        # Wildcard: any value is fine as long as the key exists
        if expected == "*":
            if key not in tool_args:
                return False
            continue
        # Exact match
        if actual != expected:
            return False
    return True


def apply_approval_gates(
    mcp: Any,
    approval_config: ApprovalConfig | None,
    pool: Any,
) -> dict[str, Any]:
    """Wrap gated tools on the FastMCP server with approval interception.

    Should be called after all module tools have been registered. Inspects
    the set of registered tools and wraps any whose name appears in the
    ``gated_tools`` config.

    Parameters
    ----------
    mcp:
        The FastMCP server instance (or ``_SpanWrappingMCP`` proxy).
    approval_config:
        The parsed approval configuration, or None if approvals are not
        configured.
    pool:
        The asyncpg connection pool for the butler's database.

    Returns
    -------
    dict[str, Callable]
        Mapping of tool_name -> original tool handler for gated tools.
        These originals can be used for direct invocation after approval.
    """
    if approval_config is None or not approval_config.enabled:
        return {}

    gated_tools = approval_config.gated_tools
    if not gated_tools:
        return {}

    # Get the registered tools dict from FastMCP's tool manager
    registered_tools = mcp._tool_manager.get_tools()

    originals: dict[str, Any] = {}

    for tool_name, tool_config in gated_tools.items():
        if tool_name not in registered_tools:
            logger.warning(
                "Gated tool %r not found in registered tools; skipping gate wrapping",
                tool_name,
            )
            continue

        tool_obj = registered_tools[tool_name]
        original_fn = tool_obj.fn
        originals[tool_name] = original_fn

        # Compute effective expiry for this tool
        effective_expiry_hours = approval_config.get_effective_expiry(tool_name)

        # Create the wrapper
        wrapper = _make_gate_wrapper(
            tool_name=tool_name,
            original_fn=original_fn,
            pool=pool,
            expiry_hours=effective_expiry_hours,
        )

        # Replace the tool's handler on the MCP server
        tool_obj.fn = wrapper

    return originals


def _make_gate_wrapper(
    tool_name: str,
    original_fn: Any,
    pool: Any,
    expiry_hours: int,
) -> Any:
    """Create an async wrapper function that intercepts gated tool calls.

    The wrapper:
    1. Serializes the call into a PendingAction
    2. Checks standing approval rules
    3. If a rule matches: auto-approve, execute the original, log the result
    4. If no rule matches: persist PendingAction with status='pending',
       return a structured pending_approval response
    """

    async def gate_wrapper(**kwargs: Any) -> dict[str, Any]:
        tool_args = dict(kwargs)
        action_id = uuid.uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=expiry_hours)

        # Generate agent summary
        agent_summary = f"Tool '{tool_name}' called with args: {json.dumps(tool_args)}"

        # Check standing rules
        rules = await pool.fetch(
            "SELECT * FROM approval_rules WHERE tool_name = $1 AND active = true",
            tool_name,
        )

        matching_rule = match_standing_rule(tool_name, tool_args, rules)

        if matching_rule is not None:
            # Auto-approve path: persist the action, execute via executor, log
            rule_id = matching_rule["id"]

            # Persist the action with approval_rule_id and decided_by
            await pool.execute(
                "INSERT INTO pending_actions "
                "(id, tool_name, tool_args, agent_summary, session_id, status, "
                "requested_at, expires_at, approval_rule_id, decided_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
                action_id,
                tool_name,
                json.dumps(tool_args),
                agent_summary,
                None,  # session_id
                ActionStatus.APPROVED.value,
                now,
                expires_at,
                rule_id,
                f"rule:{rule_id}",
            )
            await record_approval_event(
                pool,
                ApprovalEventType.ACTION_QUEUED,
                actor="system:approval_gate",
                action_id=action_id,
                reason="gated invocation intercepted",
                metadata={"tool_name": tool_name, "path": "auto_approve"},
                occurred_at=now,
            )
            await record_approval_event(
                pool,
                ApprovalEventType.ACTION_AUTO_APPROVED,
                actor=f"rule:{rule_id}",
                action_id=action_id,
                rule_id=rule_id,
                reason="standing rule matched",
                metadata={"tool_name": tool_name},
                occurred_at=now,
            )

            # Execute via the shared executor (handles DB update + use_count)
            exec_result = await execute_approved_action(
                pool=pool,
                action_id=action_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_fn=original_fn,
                approval_rule_id=rule_id,
            )

            logger.info(
                "Auto-approved gated tool %r (action=%s, rule=%s)",
                tool_name,
                action_id,
                rule_id,
            )

            if exec_result.success:
                return exec_result.result or {}
            return {"error": exec_result.error}

        # No matching rule — park the action
        await pool.execute(
            "INSERT INTO pending_actions "
            "(id, tool_name, tool_args, agent_summary, session_id, status, "
            "requested_at, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            action_id,
            tool_name,
            json.dumps(tool_args),
            agent_summary,
            None,  # session_id
            ActionStatus.PENDING.value,
            now,
            expires_at,
        )
        await record_approval_event(
            pool,
            ApprovalEventType.ACTION_QUEUED,
            actor="system:approval_gate",
            action_id=action_id,
            reason="gated invocation intercepted",
            metadata={"tool_name": tool_name, "path": "pending"},
            occurred_at=now,
        )

        logger.info(
            "Parked gated tool %r for approval (action=%s)",
            tool_name,
            action_id,
        )

        return {
            "status": "pending_approval",
            "action_id": str(action_id),
            "message": f"Action queued for approval: {agent_summary}",
        }

    # Preserve the original function's name for introspection
    gate_wrapper.__name__ = original_fn.__name__
    gate_wrapper.__qualname__ = original_fn.__qualname__

    return gate_wrapper
