"""Approval gate — MCP tool dispatch interception for approval-gated tools.

Wraps gated tools at MCP registration time so that:
1. When a gated tool is called, the call is serialized into a PendingAction.
2. Target contact resolution: extract channel identifier from tool_args and
   resolve via ``resolve_contact_by_channel()``.  If the target has the
   ``'owner'`` role AND the targeted channel address is the primary entry for
   that channel type in ``relationship.entity_facts``, the action is auto-approved
   with no standing rule required.  Non-primary owner addresses (e.g. a work
   Telegram chat ID) fall through to the rules/parking flow.
   ``entity_id`` dispatch is exempt from the primacy check — the system
   already resolves to the primary address downstream.
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

import inspect
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.config import ApprovalConfig, ApprovalRiskTier
from butlers.identity import (
    ResolvedContact,
    resolve_contact_by_channel,
    resolve_owner_channel_via_definer,
)
from butlers.modules.approvals._shared import is_primary_contact
from butlers.modules.approvals.events import ApprovalEventType, record_approval_event
from butlers.modules.approvals.executor import execute_approved_action
from butlers.modules.approvals.models import ActionStatus
from butlers.modules.approvals.rules import match_rules_from_list

logger = logging.getLogger(__name__)


async def _resolve_registered_tool(mcp: Any, tool_name: str) -> Any | None:
    """Resolve a registered tool by name via FastMCP public API."""
    get_tool = getattr(mcp, "get_tool", None)
    if not callable(get_tool):
        raise RuntimeError("FastMCP instance does not expose required get_tool(name) API")

    try:
        tool_obj = get_tool(tool_name)
        if inspect.isawaitable(tool_obj):
            tool_obj = await tool_obj
    except KeyError:
        return None
    return tool_obj


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

    - ``entity_id`` (UUID string): returned as-is with type ``"entity_id"`` for
      direct lookup (not a channel type, handled separately by callers).
    - ``channel`` + ``recipient``: used by the ``notify`` tool.
    - ``chat_id``: used by ``telegram_send_message`` / ``telegram_reply_to_message``.
    - ``to``: used by ``email_send_message`` / ``email_reply_to_thread``.
    - ``recipient`` (without ``channel``): used by ``whatsapp_send_message``.
    - ``chat_jid``: used by ``whatsapp_reply_to_message``.

    Returns
    -------
    tuple[str, str] | None
        ``(channel_type, channel_value)`` when a recognizable pair is found,
        ``None`` when the tool_args carry no resolvable channel identity.
    """
    # entity_id direct lookup (highest priority — explicit entity reference)
    entity_id = tool_args.get("entity_id")
    if entity_id and isinstance(entity_id, str) and entity_id.strip():
        return ("entity_id", entity_id.strip())

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

    # whatsapp_send_message: recipient (phone or JID).
    # Only matched when channel is absent to avoid colliding with notify()'s channel+recipient.
    # recipient was already read above for the notify() path; reuse it here.
    if (
        recipient
        and isinstance(recipient, str)
        and recipient.strip()
        and not tool_args.get("channel")
    ):
        return ("whatsapp_jid", recipient.strip())

    # whatsapp_reply_to_message: chat_jid (distinct from telegram's chat_id)
    chat_jid = tool_args.get("chat_jid")
    if chat_jid and isinstance(chat_jid, str) and chat_jid.strip():
        return ("whatsapp_jid", chat_jid.strip())

    return None


async def _resolve_target_contact(
    pool: Any,
    tool_args: dict[str, Any],
) -> ResolvedContact | None:
    """Resolve the target contact for an outbound tool call.

    Uses :func:`_extract_channel_identity` to determine the channel type and
    value, then queries ``relationship.entity_facts`` via
    :func:`resolve_contact_by_channel` (migration bead 7).

    For ``entity_id`` extractions (explicit entity reference), queries
    ``public.entities`` directly by UUID.

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

    if channel_type == "entity_id":
        # entity_id dispatch: resolve the entity directly (the caller passes
        # public.entities.id; no public.contacts indirection).
        try:
            row = await pool.fetchrow(
                """
                SELECT e.id               AS entity_id,
                       e.canonical_name   AS name,
                       COALESCE(e.roles, '{}') AS roles
                FROM public.entities e
                WHERE e.id = $1::uuid
                """,
                channel_value,
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "_resolve_target_contact: direct entity_id lookup failed; returning None",
                exc_info=True,
            )
            return None

        if row is None:
            return None

        from uuid import UUID

        raw_roles = row["roles"]
        roles = [str(r) for r in raw_roles] if isinstance(raw_roles, (list, tuple)) else []

        entity_id = row["entity_id"]
        if not isinstance(entity_id, UUID):
            try:
                entity_id = UUID(str(entity_id))
            except (ValueError, AttributeError):
                return None

        return ResolvedContact(
            contact_id=None,  # entity_id is authoritative post bead 7
            name=row["name"] or None,
            roles=roles,
            entity_id=entity_id,
        )

    # Channel-based lookup
    return await resolve_contact_by_channel(pool, channel_type, channel_value)


async def apply_approval_gates(
    mcp: Any,
    approval_config: ApprovalConfig | None,
    pool: Any,
    butler_name: str | None = None,
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
    butler_name:
        The name of the butler that owns this gate (used for WS event
        attribution).  Pass ``None`` when the name is unavailable.

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

    originals: dict[str, Any] = {}

    for tool_name, tool_config in gated_tools.items():
        tool_obj = await _resolve_registered_tool(mcp, tool_name)
        if tool_obj is None:
            logger.warning(
                "Gated tool %r not found in registered tools; skipping gate wrapping",
                tool_name,
            )
            continue

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
            butler_name=butler_name,
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
    butler_name: str | None = None,
) -> Any:
    """Create an async wrapper function that intercepts gated tool calls.

    The wrapper implements role-based gating:

    1. Resolves the target contact from tool_args using channel identity
       extraction and ``resolve_contact_by_channel()``.
    2. If the target has the ``'owner'`` role AND the targeted channel address
       is the primary entry for that channel type in ``relationship.entity_facts``:
       auto-approve immediately (no standing rule required).
       Non-primary owner addresses (e.g. a work Telegram chat ID alongside the
       personal one) fall through to the rules/parking flow so they appear in
       the approval queue.
       ``entity_id`` dispatch is exempt from the primacy check because no
       specific address is targeted — the system already resolves to primary.
    3. If the target is a known non-owner contact: check standing rules;
       auto-approve if a rule matches, otherwise pend.
    4. If the target is unresolvable: require approval (conservative default).
    """

    _WHY_MAX_CHARS = 2000
    _EVIDENCE_MAX_ITEMS = 50
    _EVIDENCE_ITEM_MAX_CHARS = 500

    def _emit_created(action_id: uuid.UUID, status: str) -> None:
        """Publish a 'created' approval WS event; silently ignored if broker is unavailable."""
        try:
            from butlers.api.routers.approvals import emit_approvals_event

            emit_approvals_event(
                "created",
                str(action_id),
                butler=butler_name,
                tool_name=tool_name,
                status=status,
            )
        except Exception:  # noqa: BLE001
            logger.debug("gate: emit_approvals_event('created') failed; ignoring", exc_info=True)

    async def gate_wrapper(**kwargs: Any) -> dict[str, Any]:
        tool_args = dict(kwargs)
        action_id = uuid.uuid4()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=expiry_hours)

        # Extract and validate why/evidence from tool kwargs (§8.2 agent contract).
        # These are gate-level metadata, not forwarded to the underlying tool.
        raw_why: str | None = tool_args.pop("_why", None) or tool_args.pop("why", None)
        raw_evidence: list | None = tool_args.pop("_evidence", None) or tool_args.pop(
            "evidence", None
        )

        if raw_why is None:
            logger.warning(
                "Gate wrapper: tool %r called without 'why' rationale (action=%s)",
                tool_name,
                action_id,
            )
            why: str | None = None
        elif not isinstance(raw_why, str):
            logger.warning(
                "Gate wrapper: tool %r 'why' is not a string (%r); ignoring",
                tool_name,
                type(raw_why).__name__,
            )
            why = None
        elif len(raw_why) > _WHY_MAX_CHARS:
            logger.warning(
                "Gate wrapper: tool %r 'why' exceeds %d chars (%d); truncating",
                tool_name,
                _WHY_MAX_CHARS,
                len(raw_why),
            )
            why = raw_why[:_WHY_MAX_CHARS]
        else:
            why = raw_why

        evidence: list[str] = []
        if raw_evidence is not None:
            if not isinstance(raw_evidence, list):
                logger.warning(
                    "Gate wrapper: tool %r 'evidence' is not a list (%r); ignoring",
                    tool_name,
                    type(raw_evidence).__name__,
                )
            else:
                if len(raw_evidence) > _EVIDENCE_MAX_ITEMS:
                    logger.warning(
                        "Gate wrapper: tool %r 'evidence' has %d items (max %d); truncating",
                        tool_name,
                        len(raw_evidence),
                        _EVIDENCE_MAX_ITEMS,
                    )
                    raw_evidence = raw_evidence[:_EVIDENCE_MAX_ITEMS]
                for item in raw_evidence:
                    item_str = str(item)
                    if len(item_str) > _EVIDENCE_ITEM_MAX_CHARS:
                        logger.warning(
                            "Gate wrapper: tool %r evidence item truncated from %d to %d chars",
                            tool_name,
                            len(item_str),
                            _EVIDENCE_ITEM_MAX_CHARS,
                        )
                        item_str = item_str[:_EVIDENCE_ITEM_MAX_CHARS]
                    evidence.append(item_str)

        # Generate agent summary
        agent_summary = f"Tool '{tool_name}' called with args: {json.dumps(tool_args)}"

        # --- Role-based target resolution ---
        resolved_contact = await _resolve_target_contact(pool, tool_args)
        identity = _extract_channel_identity(tool_args)

        # Cross-schema owner fallback. resolve_contact_by_channel and
        # is_primary_contact read relationship.entity_facts directly, which a
        # non-relationship butler's role cannot (schema isolation via SET ROLE).
        # So owner-directed sends from those butlers resolve to None and park as
        # "unresolvable target". Recognize the owner — and its channel primacy — via
        # the SECURITY DEFINER public.resolve_owner_triple() lookup (migration
        # core_145), which runs as a role that can read the relationship schema.
        # Only when normal resolution failed entirely: a resolved non-owner contact
        # means the butler COULD read the relationship schema, so no owner fallback
        # is needed (and the channel demonstrably belongs to a non-owner).
        owner_primary_override: bool | None = None
        if resolved_contact is None and identity is not None and identity[0] != "entity_id":
            fallback = await resolve_owner_channel_via_definer(pool, identity[0], identity[1])
            if fallback is not None:
                resolved_contact, owner_primary_override = fallback

        # Determine whether this is a channel-based or entity_id-based dispatch.
        # For channel-based dispatches (telegram, whatsapp, etc.) the owner bypass
        # requires that the targeted address is the *primary* entry for that channel
        # type.  Non-primary addresses (e.g. a work Telegram chat ID) must go
        # through the normal rules/parking flow.
        # entity_id dispatch has no specific address to check — skip primacy gate.
        owner_is_primary: bool
        if owner_primary_override is not None:
            # Owner identity and channel primacy already resolved by the
            # cross-schema SECURITY DEFINER fallback above.
            owner_is_primary = owner_primary_override
        elif (
            resolved_contact is not None
            and "owner" in resolved_contact.roles
            and identity is not None
            and identity[0] != "entity_id"
        ):
            channel_type, channel_value = identity
            if resolved_contact.entity_id is None:
                # No entity_id — cannot check primacy; treat as non-primary so
                # the action falls through to the rules/parking flow.
                owner_is_primary = False
            else:
                owner_is_primary = await is_primary_contact(
                    pool,
                    resolved_contact.entity_id,
                    channel_type,
                    channel_value,
                )
        else:
            # entity_id dispatch or unresolvable: treat as primary (no specific
            # address to gate against; entity_id resolution already prefers primary).
            owner_is_primary = True

        if resolved_contact is not None and "owner" in resolved_contact.roles and owner_is_primary:
            # Owner-targeted primary channel: auto-approve without any standing rule
            await pool.execute(
                "INSERT INTO pending_actions "
                "(id, tool_name, tool_args, agent_summary, session_id, status, "
                "requested_at, expires_at, decided_by, why, evidence) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
                action_id,
                tool_name,
                json.dumps(tool_args),
                agent_summary,
                None,  # session_id
                ActionStatus.APPROVED.value,
                now,
                expires_at,
                "role:owner",
                why,
                json.dumps(evidence),
            )
            _emit_created(action_id, ActionStatus.APPROVED.value)
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
                    "entity_id": str(resolved_contact.entity_id),
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
                "Owner-targeted auto-approve: tool %r (action=%s, entity=%s, risk_tier=%s)",
                tool_name,
                action_id,
                resolved_contact.entity_id,
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
                "requested_at, expires_at, approval_rule_id, decided_by, why, evidence) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
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
                why,
                json.dumps(evidence),
            )
            _emit_created(action_id, ActionStatus.APPROVED.value)
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
            "requested_at, expires_at, why, evidence) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)",
            action_id,
            tool_name,
            json.dumps(tool_args),
            agent_summary,
            None,  # session_id
            ActionStatus.PENDING.value,
            now,
            expires_at,
            why,
            json.dumps(evidence),
        )
        _emit_created(action_id, ActionStatus.PENDING.value)
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
