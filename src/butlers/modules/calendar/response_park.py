"""Park one invitation response for the owner's approval, from inside the calendar module.

The approvals gate parks ``calendar_respond`` through the module's own canonical preparer
(``CalendarResponseCoordinator.prepare_and_park``): it binds the immutable occurrence command, the
exact approval and the dossier in one transaction. This is that same call, made directly so a
plan step (bu-5ii4gj.11 P2b) needs no MCP round trip to its own butler and no second implementation.
It only parks: the owner decides in the approvals surface, and nothing here sends a response.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import butlers.core.approvals_hooks as approval_hooks
from butlers.modules.calendar_response import CalendarResponseError

if TYPE_CHECKING:
    from . import CalendarModule

logger = logging.getLogger(__name__)

#: The gate's default card lifetime (``ApprovalConfig.default_expiry_hours``).
RESPONSE_CARD_TTL = timedelta(hours=48)


async def park_response(
    module: CalendarModule,
    arguments: dict[str, Any],
    *,
    why: str,
    blast_radius: str = "self",
    reversibility: str = "compensable",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Park ``calendar_respond`` with ``arguments``; ``{"error": ...}`` when it cannot.

    A repeat of the same request returns the command already parked (the preparer is idempotent
    on ``request_id``). A pending result carries ``action_id``.
    """
    dossier = approval_hooks.validate_non_owner_dossier(
        raw_why=why, raw_evidence=[], raw_blast_radius=blast_radius, raw_reversibility=reversibility
    )
    if isinstance(dossier, dict):
        return dossier
    moment = now or datetime.now(UTC)
    try:
        result = await module._response_coordinator.prepare_and_park(
            tool_args=dict(arguments),
            requested_at=moment,
            expires_at=moment + RESPONSE_CARD_TTL,
            dossier=dossier,
        )
    except CalendarResponseError as exc:
        return {"error": str(exc)}
    if result.get("status") == "pending_approval" and result.get("action_id"):
        try:
            from butlers.fleet_events import publish_fleet_event
            from butlers.modules.calendar.invitation_guard import pool_of

            await publish_fleet_event(
                pool_of(module),
                "approval",
                {
                    "kind": "created",
                    "approval_id": str(uuid.UUID(str(result["action_id"]))),
                    "butler": module._butler_name,
                    "tool_name": "calendar_respond",
                    "status": "pending",
                },
            )
        except Exception:  # noqa: BLE001
            logger.debug("response_park: fleet event not published", exc_info=True)
    return result
