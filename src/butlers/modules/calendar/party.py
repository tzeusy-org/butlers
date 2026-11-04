"""Party-reaching calendar mutations share the module's durable replay boundary."""

from __future__ import annotations

import uuid
from typing import Any

from butlers.modules.calendar_approval import (
    bind_calendar_approval_calendar,
    calendar_approval_args,
)
from butlers.modules.party_approval import gate_party_call, parties_are_owner

from .constants import MUTATION_STATUS_NOOP, MUTATION_STATUS_PENDING


async def calendar_party_binding(module: Any, calendar_id: str) -> dict[str, str]:
    """Bind a parked call to the currently verified owning account and destination."""
    account = await module._verified_response_account()
    entity_id = str(uuid.UUID(account["entity_id"]))
    provider = module._provider.name
    butler = module._butler_name
    if not all(isinstance(value, str) and value for value in (provider, butler, calendar_id)):
        raise ValueError("Calendar party binding unavailable")
    return {
        "butler": butler,
        "provider": provider,
        "account_entity_id": entity_id,
        "calendar_id": calendar_id,
    }


async def gate_calendar_party_mutation(
    module: Any,
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    attendees: list[str],
    calendar_id: str,
    action_type: str,
    request_id: str | None,
    idempotency_key: str,
    action_payload: dict[str, Any],
    source_id: Any,
    origin_ref: str | None,
) -> dict[str, Any] | None:
    arguments = (
        tool_args
        if tool_name == "calendar_create_event"
        else bind_calendar_approval_calendar(
            calendar_approval_args(tool_name, **tool_args), calendar_id
        )
    )
    pool = getattr(getattr(module, "_db", None), "pool", None)
    parties = [("email", attendee) for attendee in attendees]
    if await parties_are_owner(pool, parties):
        return None
    binding = None
    if getattr(module, "_approval_enqueuer", None) is not None:
        try:
            binding = await calendar_party_binding(module, calendar_id)
        except Exception:
            return {
                "status": "approval_unavailable",
                "message": "The owning calendar account could not be verified.",
            }
    result = await gate_party_call(
        module,
        tool_name=tool_name,
        arguments=arguments,
        parties=parties,
    )
    if result is not None and result["status"] == "approval_required":
        result.update(tool_name=tool_name, action_type=action_type, party_binding=binding)
    if result is not None:
        await module._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type=action_type,
            request_id=request_id,
            action_status=MUTATION_STATUS_PENDING
            if result["status"] == "approval_required"
            else MUTATION_STATUS_NOOP,
            action_payload=action_payload,
            action_result=result,
            source_id=source_id,
            origin_ref=origin_ref,
        )
    return result
