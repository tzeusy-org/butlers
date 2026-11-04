"""Party-reaching calendar mutations share the module's durable replay boundary."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from butlers.modules.approvals.execution_context import (
    approval_tool_args_digest,
    get_approval_execution_context,
)
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
    snapshot_etag: str | None = None,
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
    owner_only = await parties_are_owner(pool, parties)
    # A digest binds the complete fetched audience without replacing a caller's
    # attendees=None (which preserves provider attendee/status semantics).
    audience_digest = hashlib.sha256(
        json.dumps(
            sorted(set(attendees)),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    context = get_approval_execution_context(tool_name=tool_name, tool_args=arguments)
    if context is not None:
        changed = {
            "status": "request_changed",
            "message": "The approved calendar action or its audience has changed.",
            "error": "The approved calendar action or its audience has changed.",
        }

        async def refuse_changed() -> dict[str, Any]:
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type=action_type,
                request_id=request_id,
                action_status=MUTATION_STATUS_NOOP,
                action_payload=action_payload,
                action_result=changed,
                source_id=source_id,
                origin_ref=origin_ref,
            )
            return changed

        try:
            owning = await module._load_party_approval_receipt(context.action_id)
            row = await pool.fetchrow(
                "SELECT tool_name, tool_args, status, agent_summary "
                "FROM pending_actions WHERE id=$1",
                context.action_id,
            )
            if row is None:
                return await refuse_changed()
            if row["status"] in {"executed", "rejected", "expired"}:
                return {"status": row["status"], "idempotent_replay": True}
            stored_args = row["tool_args"]
            if isinstance(stored_args, str):
                stored_args = json.loads(stored_args)
            if (
                row["tool_name"] != tool_name
                or row["status"] != "approved"
                or approval_tool_args_digest(stored_args) != approval_tool_args_digest(arguments)
            ):
                return await refuse_changed()
            if owning is None:
                # Summary is only a refusal marker, never authority. A legacy
                # owner-only overlap call has no party receipt; a party call
                # whose receipt vanished cannot become one after audience drift.
                party_summary = "Communication with a non-owner requires owner approval."
                if owner_only and row.get("agent_summary") != party_summary:
                    return None
                return await refuse_changed()
            key, status, receipt, _ = owning
            if (
                key != idempotency_key
                or status != MUTATION_STATUS_PENDING
                or receipt.get("action_id") != str(context.action_id)
                or receipt.get("tool_name") != tool_name
                or receipt.get("action_type") != action_type
                or row["tool_name"] != tool_name
                or row["status"] != "approved"
                or approval_tool_args_digest(stored_args) != receipt.get("args_digest")
                or approval_tool_args_digest(arguments) != receipt.get("args_digest")
                or receipt.get("party_binding") != await calendar_party_binding(module, calendar_id)
                or receipt.get("party_audience_digest") != audience_digest
                or (
                    tool_name != "calendar_create_event"
                    and not owner_only
                    and (not isinstance(snapshot_etag, str) or not snapshot_etag.strip())
                )
            ):
                return await refuse_changed()
            return None
        except Exception:
            return await refuse_changed()
    if owner_only:
        return None
    binding = None
    result: dict[str, Any] | None = None
    if getattr(module, "_approval_enqueuer", None) is not None:
        try:
            binding = await calendar_party_binding(module, calendar_id)
        except Exception:
            result = {
                "status": "approval_unavailable",
                "message": "The owning calendar account could not be verified.",
            }
    if result is None:
        result = await gate_party_call(
            module,
            tool_name=tool_name,
            arguments=arguments,
            parties=parties,
        )
    if result is not None and result["status"] == "approval_required":
        result.update(
            tool_name=tool_name,
            action_type=action_type,
            party_binding=binding,
            party_audience_digest=audience_digest,
        )
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
