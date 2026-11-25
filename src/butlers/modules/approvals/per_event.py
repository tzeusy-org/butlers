"""Per-event tools: no standing rule approves one and its approvals never feed promotion.

RFC 0038 D13: a tool whose approval policy is ``always_per_event`` (``calendar_respond``) is an
owner decision on every call. A rule for it would show as active and never apply, and approvals of
it would train a promotion that cannot happen, so the rule writes refuse it and
``counts_toward_promotion`` excludes it. One helper answers "is this tool per-event?" for every
path (dashboard REST, MCP, approve-with-rule, suggestions).

The mode is read from the checked-in inventory first, then from the module metadata when the caller
has it. The dashboard process holds no module instance, so the inventory is what it can see.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

RULE_REFUSED_ALWAYS_PER_EVENT = "rule_refused_always_per_event"


def is_always_per_event(
    tool_name: str | None, tool_metadata: Mapping[str, Any] | None = None
) -> bool:
    """Whether ``tool_name`` declares ``approval_policy`` mode ``always_per_event``."""
    if not tool_name:
        return False
    from butlers.core.tool_presentation_inventory import (
        ALWAYS_PER_EVENT_TOOLS,
        TOOL_PRESENTATION_BY_NAME,
    )

    if tool_name in ALWAYS_PER_EVENT_TOOLS:
        return True
    declared = TOOL_PRESENTATION_BY_NAME.get(tool_name)
    if declared is None and tool_metadata is not None:
        declared = tool_metadata.get(tool_name)
    policy = getattr(declared, "approval_policy", None)
    return isinstance(policy, Mapping) and policy.get("mode") == "always_per_event"


def per_event_rule_refusal(
    tool_name: str | None, tool_metadata: Mapping[str, Any] | None = None
) -> dict[str, str] | None:
    """The typed refusal for a rule on a per-event tool; ``None`` when a rule may be made."""
    if is_always_per_event(tool_name, tool_metadata):
        return {"error": RULE_REFUSED_ALWAYS_PER_EVENT}
    return None


async def per_event_action_refusal(
    connection: Any, action_id: str, tool_metadata: Mapping[str, Any] | None = None
) -> dict[str, str] | None:
    """The refusal for a rule made from pending action ``action_id`` whose tool is per-event.

    A malformed or unknown id is not refused here; the owning write reports it.
    """
    try:
        parsed = uuid.UUID(action_id)
    except (ValueError, TypeError, AttributeError):
        return None
    row = await connection.fetchrow("SELECT tool_name FROM pending_actions WHERE id = $1", parsed)
    if row is None:
        return None
    return per_event_rule_refusal(row["tool_name"], tool_metadata)
