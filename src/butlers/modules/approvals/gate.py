"""Approval gate — MCP tool dispatch interception for approval-gated tools.

Wraps gated tools at MCP registration time so that:
1. When a gated tool is called, the call is serialized into a PendingAction.
2. Target contact resolution: extract channel identifier from tool_args and
   resolve via ``resolve_contact_by_channel()``.  If the target has the
   ``'owner'`` role, the action is auto-approved with no standing rule required.
3. For non-owner targets, standing approval rules are checked — if a rule
   matches, the tool is auto-approved and executed immediately.
4. If no rule matches (or the target is unresolvable), the PendingAction is
   persisted with status='pending' and a structured ``pending_approval``
   response is returned to CC.

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

from butlers.config import ApprovalConfig, ApprovalRiskTier
from butlers.identity import ResolvedContact, resolve_contact_by_channel
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.rules import match_rules_from_list

logger = logging.getLogger(__name__)


def match_standing_rule(
    tool_name: str,
    tool_args: dict[str, Any],
    rules: list[Any],
) -> dict[str, Any] | None:
    """Check whether any standing approval rule matches this invocation.

    Uses the shared standing-rule matcher so precedence is deterministic:
    1) higher constraint specificity
    2) bounded scope before unbounded
    3) newer rules before older
    4) lexical rule id tie-breaker

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
        The selected matching rule dict, or None if no rule matches.
    """
    now = datetime.now(UTC)
    normalized_rules: list[dict[str, Any]] = []
    for rule in rules:
        normalized = dict(rule) if not isinstance(rule, dict) else dict(rule)
        normalized.setdefault("description", "")
        normalized.setdefault("created_from", None)
        normalized.setdefault("created_at", now)
        normalized.setdefault("arg_constraints", "{}")
        normalized.setdefault("active", True)
        normalized.setdefault("use_count", 0)
        normalized_rules.append(normalized)
    selected = match_rules_from_list(tool_name, tool_args, normalized_rules)
    if selected is None:
        return None

    selected_id = str(selected.id)
    for rule in normalized_rules:
        if str(rule.get("id")) == selected_id:
            return rule

    logger.warning("Standing rule selected but not found in source rows: %s", selected_id)
    return None


def _extract_channel_identity(
    tool_args: dict[str, Any],
) -> tuple[str, str] | None:
    """Extract (channel_type, channel_value) from tool_args for contact resolution.

    Inspects tool_args for known channel identifier patterns:

    - ``contact_id`` (UUID string): returned as-is with type ``"contact_id"`` for
      direct lookup (not a channel type, handled separately by callers).
    - ``channel`` + ``recipient``: used by the ``notify`` tool.
    - ``chat_id``: used by ``telegram_send_message`` / ``telegram_reply_to_message``.
    - ``to``: used by ``email_send_message`` / ``email_reply_to_thread``.

    Returns
    -------
    tuple[str, str] | None
        ``(channel_type, channel_value)`` when a recognizable pair is found,
        ``None`` when the tool_args carry no resolvable channel identity.
    """
    # contact_id direct lookup (highest priority — explicit contact reference)
    contact_id = tool_args.get("contact_id")
    if contact_id and isinstance(contact_id, str) and contact_id.strip():
        return ("contact_id", contact_id.strip())

    # notify() tool pattern: channel + recipient
    channel = tool_args.get("channel")
    recipient = tool_args.get("recipient")
    recipient_stripped = recipient.strip() if isinstance(recipient, str) else ""
    if channel and isinstance(channel, str) and recipient_stripped:
        return (channel.lower(), recipient_stripped)

    # telegram_send_message / telegram_reply_to_message: chat_id
    chat_id = tool_args.get("chat_id")
    if chat_id and isinstance(chat_id, str) and chat_id.strip():
        return ("telegram", chat_id.strip())

    # email_send_message / email_reply_to_thread: to
    to = tool_args.get("to")
    if to and isinstance(to, str) and to.strip():
        return ("email", to.strip())

    return None


async def _resolve_target_contact(
    pool: Any,
    tool_args: dict[str, Any],
) -> ResolvedContact | None:
    """Resolve the target contact for an outbound tool call.

    Uses :func:`_extract_channel_identity` to determine the channel type and
    value, then queries ``shared.contact_info`` via
    :func:`resolve_contact_by_channel`.

    For ``contact_id`` extractions (explicit contact reference), queries
    ``shared.contacts`` directly by UUID.

    Parameters
    ----------
    pool:
        asyncpg connection pool.
    tool_args:
        Arguments passed to the gated tool.

    Returns
    -------
    ResolvedContact | None
        Resolved contact, or ``None`` if unresolvable.
    """
    identity = _extract_channel_identity(tool_args)
    if identity is None:
        return None

    channel_type, channel_value = identity

    if channel_type == "contact_id":
        # Direct UUID lookup on shared.contacts
        try:
            row = await pool.fetchrow(
                "SELECT id AS contact_id, name, roles, entity_id "
                "FROM shared.contacts WHERE id = $1::uuid",
                channel_value,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "_resolve_target_contact: direct contact_id lookup failed; returning None",
                exc_info=True,
            )
            return None

        if row is None:
            return None

        from uuid import UUID

        raw_roles = row["roles"]
        roles = [str(r) for r in raw_roles] if isinstance(raw_roles, (list, tuple)) else []

        contact_id_val = row["contact_id"]
        if not isinstance(contact_id_val, UUID):
            try:
                contact_id_val = UUID(str(contact_id_val))
            except (ValueError, AttributeError):
                return None

        entity_id = row["entity_id"]
        if entity_id is not None and not isinstance(entity_id, UUID):
            try:
                entity_id = UUID(str(entity_id))
            except (ValueError, AttributeError):
                entity_id = None

        return ResolvedContact(
            contact_id=contact_id_val,
            name=row["name"] or None,
            roles=roles,
            entity_id=entity_id,
        )

    # Channel-based lookup
    return await resolve_contact_by_channel(pool, channel_type, channel_value)


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
        effective_risk_tier = approval_config.get_effective_risk_tier(tool_name)

        # Create the wrapper
        wrapper = _make_gate_wrapper(
            tool_name=tool_name,
            original_fn=original_fn,
            pool=pool,
            expiry_hours=effective_expiry_hours,
            risk_tier=effective_risk_tier,
            rule_precedence=approval_config.rule_precedence,
        )

        # Replace the tool's handler on the MCP server
        tool_obj.fn = wrapper

    return originals


def _make_gate_wrapper(
    tool_name: str,
    original_fn: Any,
    pool: Any,
    expiry_hours: int,
    risk_tier: ApprovalRiskTier,
    rule_precedence: tuple[str, ...],
) -> Any:
    """Create an async wrapper function that intercepts gated tool calls.

    The wrapper implements role-based gating:

    1. Resolves the target contact from tool_args using channel identity
       extraction and ``resolve_contact_by_channel()``.
    2. If the target has the ``'owner'`` role: auto-approve immediately
       (no standing rule required).
    3. If the target is a known non-owner contact: check standing rules;
       auto-approve if a rule matches, otherwise pend.
    4. If the target is unresolvable: require approval (conservative default).
    """

    async def gate_wrapper(**kwargs: Any) -> dict[str, Any]:
        tool_args = dict(kwargs)
        action_id = uuid.uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=expiry_hours)

        # Generate agent summary
        agent_summary = f"Tool '{tool_name}' called with args: {json.dumps(tool_args)}"

        # --- Role-based target resolution ---
        resolved_contact = await _resolve_target_contact(pool, tool_args)

        if resolved_contact is not None and "owner" in resolved_contact.roles:
            # Owner-targeted: auto-approve without any standing rule
            await pool.execute(
                "INSERT INTO pending_actions "
                "(id, tool_name, tool_args, agent_summary, session_id, status, "
                "requested_at, expires_at, decided_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)",
                action_id,
                tool_name,
                json.dumps(tool_args),
                agent_summary,
                None,  # session_id
                ActionStatus.APPROVED.value,
                now,
                expires_at,
                "role:owner",
            )
            await record_approval_event(
                pool,
                ApprovalEventType.ACTION_QUEUED,
                actor="system:approval_gate",
                action_id=action_id,
                reason="gated invocation intercepted",
                metadata={"tool_name": tool_name, "path": "owner_auto_approve"},
                occurred_at=now,
            )
            await record_approval_event(
                pool,
                ApprovalEventType.ACTION_AUTO_APPROVED,
                actor="role:owner",
                action_id=action_id,
                rule_id=None,
                reason="target contact has owner role",
                metadata={
                    "tool_name": tool_name,
                    "contact_id": str(resolved_contact.contact_id),
                },
                occurred_at=now,
            )

            exec_result = await execute_approved_action(
                pool=pool,
                action_id=action_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_fn=original_fn,
                approval_rule_id=None,
            )

            logger.info(
                "Owner-targeted auto-approve: tool %r (action=%s, contact=%s, risk_tier=%s)",
                tool_name,
                action_id,
                resolved_contact.contact_id,
                risk_tier.value,
            )

            if exec_result.success:
                return exec_result.result or {}
            return {"error": exec_result.error}

        # Non-owner or unresolvable: check standing rules
        rules = await pool.fetch(
            "SELECT * FROM approval_rules WHERE tool_name = $1 AND active = true "
            "ORDER BY created_at DESC, id ASC",
            tool_name,
        )

        matching_rule = match_standing_rule(tool_name, tool_args, rules)

        if matching_rule is not None and resolved_contact is not None:
            # Non-owner with matching standing rule: auto-approve
            rule_id = matching_rule["id"]

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

            exec_result = await execute_approved_action(
                pool=pool,
                action_id=action_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_fn=original_fn,
                approval_rule_id=rule_id,
            )

            logger.info(
                "Auto-approved gated tool %r (action=%s, rule=%s, risk_tier=%s)",
                tool_name,
                action_id,
                rule_id,
                risk_tier.value,
            )

            if exec_result.success:
                return exec_result.result or {}
            return {"error": exec_result.error}

        # No rule matched (or unresolvable target) — park the action
        pend_reason: str
        if resolved_contact is None:
            pend_reason = "unresolvable target"
        else:
            pend_reason = "no matching standing rule"

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
            metadata={"tool_name": tool_name, "path": "pending", "reason": pend_reason},
            occurred_at=now,
        )

        logger.info(
            "Parked gated tool %r for approval (action=%s, risk_tier=%s, reason=%s)",
            tool_name,
            action_id,
            risk_tier.value,
            pend_reason,
        )

        return {
            "status": "pending_approval",
            "action_id": str(action_id),
            "message": f"Action queued for approval: {agent_summary}",
            "risk_tier": risk_tier.value,
            "rule_precedence": list(rule_precedence),
        }

    # Preserve the original function's name for introspection
    gate_wrapper.__name__ = original_fn.__name__
    gate_wrapper.__qualname__ = original_fn.__qualname__

    return gate_wrapper
