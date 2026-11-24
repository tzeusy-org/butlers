"""Domain-side owner communication boundary, independent of descriptive catalog metadata."""

from __future__ import annotations

import json
from typing import Any

from butlers.modules.approvals.execution_context import (
    approval_tool_args_digest,
    get_approval_execution_context,
)


async def parties_are_owner(pool: Any, parties: list[tuple[str, str]] | None) -> bool:
    """Every named party must pass the existing ambiguity-safe notify resolver."""
    from butlers.identity import resolve_channel_contact_with_owner_corroboration

    if parties is None:
        return False
    if not parties:
        return True
    if pool is None:
        return False
    for channel, identifier in parties:
        if not isinstance(identifier, str) or not identifier.strip():
            return False
        try:
            contact = await resolve_channel_contact_with_owner_corroboration(
                pool, channel, identifier.strip()
            )
        except Exception:
            return False
        if contact is None or "owner" not in contact.roles:
            return False
    return True


async def gate_party_call(
    module: Any,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    parties: list[tuple[str, str]] | None,
) -> dict[str, Any] | None:
    """Park a non-owner effect; only the executor's exact stored call opens it."""
    pool = getattr(getattr(module, "_db", None), "pool", None)
    if await parties_are_owner(pool, parties):
        return None
    enqueuer = getattr(module, "_approval_enqueuer", None)
    if enqueuer is None:
        return {
            "status": "approval_unavailable",
            "message": "Communication with a non-owner requires the approvals module.",
        }
    if get_approval_execution_context(tool_name=tool_name, tool_args=arguments) is not None:
        return None
    # Arguments, including recipients/destination, are detached before parking.
    stored = json.loads(json.dumps(arguments, allow_nan=False))
    action_id = await enqueuer(
        tool_name, stored, "Communication with a non-owner requires owner approval."
    )
    return {
        "status": "approval_required",
        "action_id": action_id,
        "args_digest": approval_tool_args_digest(stored),
        "message": "Communication with a non-owner has been queued for approval.",
    }
