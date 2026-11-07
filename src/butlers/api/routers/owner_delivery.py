"""Canonical owner-delivery action and retraction routes (bu-dhloi1.13, RFC 0038 S6).

``POST /api/owner-delivery/actions`` is the one path a single-use card token decides
through, for every surface. The surface is derived from how the request authenticated,
never from the body: the connector callback credential means Telegram, an owner browser
session means the dashboard. The token then decides through the same approve/deny
transaction, audit and dispatch as the established approval routes.

``GET /api/owner-delivery/retractions`` and ``POST .../retractions/{id}`` let the
Telegram connector drain stale cards (decided elsewhere, expired, cancelled) and receipt
each attempt; they accept only the connector credential.

``public.owner_delivery_rollout`` governs **minting** only. Before it is first enabled no
subject or token exists, so every token is unknown. Turning it off stops new cards, but
tokens already minted stay redeemable (their decision still runs through
``transition_pending_action``) and the retraction drain keeps running, so live cards are
neither stranded nor left un-retracted.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from opentelemetry import metrics
from pydantic import BaseModel, Field

from butlers.api.deps import Db, MCPClientManager, get_mcp_manager
from butlers.api.models import ApiResponse
from butlers.api.models.approval import ApprovalAction, DisplayedApprovalCall
from butlers.api.routers.approvals import (
    _decision_actor_id,
    _find_action_pool,
    _find_named_approvals_pools,
    approve_owning_action,
    deny_owning_action,
)
from butlers.core import owner_delivery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/owner-delivery", tags=["owner-delivery"])


def _record_retraction_given_up() -> None:
    """Add one to ``butlers.owner_delivery.retraction.given_up`` on the ``butlers`` OTel meter.

    Resolved per call, like ``butlers.audit.log.appended``, so it binds to whichever meter
    provider is installed when the card is given up.
    """
    metrics.get_meter("butlers").create_counter(
        "butlers.owner_delivery.retraction.given_up",
        description="Stale owner cards that ran out of retraction attempts and show buttons.",
    ).add(1)


class OwnerDeliveryActionRequest(BaseModel):
    token: str = Field(min_length=1, max_length=64)
    # Dashboard only: the call the owner was shown (required to approve) and a denial note.
    displayed_call: DisplayedApprovalCall | None = None
    reason: str | None = None


class OwnerDeliveryRetraction(BaseModel):
    presentation_id: str
    route_ref: str
    provider_ref: str | None = None


class OwnerDeliveryRetractionReport(BaseModel):
    retracted: bool
    reason: str | None = Field(default=None, max_length=64, pattern=r"^[a-z_]+$")


def _callback_authenticated(request: Request) -> bool:
    return bool(getattr(request.state, "approval_callback_authenticated", False))


async def _locate(db_mgr: Any, verifier: str) -> tuple[str, Any, Any] | None:
    """The ``(butler, pool, token row)`` that owns a verifier, searched schema by schema."""
    for butler_name, pool in await _find_named_approvals_pools(db_mgr, "pending_actions"):
        try:
            row = await pool.fetchrow(
                "SELECT t.action, s.kind, s.source_ref FROM public.owner_delivery_tokens t "
                "JOIN public.owner_delivery_subjects s ON s.id = t.subject_id "
                "WHERE t.verifier = $1 AND t.owning_schema = current_schema()",
                verifier,
            )
        except Exception:  # an unreachable or pre-core_317 schema holds no token
            continue
        if row is not None:
            return butler_name, pool, row
    return None


@router.post("/actions")
async def redeem_owner_action(
    http_request: Request,
    request: Annotated[OwnerDeliveryActionRequest, Body()],
    *,
    db_mgr: Db,
    mcp_mgr: MCPClientManager = Depends(get_mcp_manager),
    callback_actor: str | None = Header(default=None, alias="X-Butlers-Decision-Actor"),
) -> ApiResponse[ApprovalAction]:
    """Decide one owner card through its single-use token."""
    callback = _callback_authenticated(http_request)
    actor_id = _decision_actor_id(callback_actor, callback_authenticated=callback)
    surface: owner_delivery.Surface = "telegram" if callback else "dashboard"
    if callback and request.reason:
        raise HTTPException(
            status_code=403,
            detail="Connector decisions cannot add a denial reason.",
        )
    verifier = owner_delivery.token_verifier(request.token)
    if verifier is None:
        raise HTTPException(
            status_code=409, detail={"error": "owner_delivery_refused", "reason": "token_malformed"}
        )
    located = await _locate(db_mgr, verifier)
    if located is None:
        raise HTTPException(
            status_code=409, detail={"error": "owner_delivery_refused", "reason": "token_unknown"}
        )
    _, _, token_row = located
    if token_row["kind"] != "approval" or token_row["action"] not in ("approve", "reject"):
        raise HTTPException(
            status_code=409,
            detail={"error": "owner_delivery_refused", "reason": "action_unsupported"},
        )
    parsed_id = UUID(token_row["source_ref"])
    # Re-resolve the owning pool from the authority row itself, as the approval routes do.
    found = await _find_action_pool(db_mgr, parsed_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Approval not found: {parsed_id}")
    action_butler, target_pool = found
    if token_row["action"] == "approve":
        return await approve_owning_action(
            parsed_id=parsed_id,
            actor_id=actor_id,
            edits=None,
            action_butler=action_butler,
            target_pool=target_pool,
            db_mgr=db_mgr,
            mcp_mgr=mcp_mgr,
            # A Telegram card shows its exact call itself; the dashboard echoes what it showed.
            displayed_call=request.displayed_call,
            require_displayed_call=not callback,
            owner_delivery_token=request.token,
            owner_delivery_surface=surface,
        )
    return await deny_owning_action(
        parsed_id=parsed_id,
        actor_id=actor_id,
        reason=request.reason,
        action_butler=action_butler,
        target_pool=target_pool,
        db_mgr=db_mgr,
        owner_delivery_token=request.token,
        owner_delivery_surface=surface,
    )


@router.get("/retractions")
async def list_owner_delivery_retractions(
    http_request: Request, *, db_mgr: Db
) -> ApiResponse[list[OwnerDeliveryRetraction]]:
    """Stale Telegram cards the connector should retract, across approval schemas."""
    if not _callback_authenticated(http_request):
        raise HTTPException(status_code=403, detail="Connector credential required.")
    items: list[OwnerDeliveryRetraction] = []
    for _, pool in await _find_named_approvals_pools(db_mgr, "pending_actions"):
        try:
            async with pool.acquire() as conn:
                targets = await _schema_retractions(conn)
        except Exception:
            continue
        items.extend(
            OwnerDeliveryRetraction(
                presentation_id=str(t.presentation_id),
                route_ref=t.route_ref,
                provider_ref=t.provider_ref,
            )
            for t in targets
        )
    return ApiResponse(data=items)


async def _schema_retractions(conn: Any) -> tuple[owner_delivery.RetractTarget, ...]:
    # Not gated on the rollout flag: it governs minting only, and stale cards minted while
    # it was on must still be retracted after it is turned off.
    return await owner_delivery.pending_retractions(conn, surface="telegram")


@router.post("/retractions/{presentation_id}")
async def report_owner_delivery_retraction(
    presentation_id: str,
    http_request: Request,
    request: Annotated[OwnerDeliveryRetractionReport, Body()],
    *,
    db_mgr: Db,
) -> ApiResponse[dict[str, bool]]:
    """Receipt one retraction attempt; a failure stays listed and never claims success."""
    if not _callback_authenticated(http_request):
        raise HTTPException(status_code=403, detail="Connector credential required.")
    try:
        parsed = UUID(presentation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid presentation_id") from exc
    for _, pool in await _find_named_approvals_pools(db_mgr, "pending_actions"):
        try:
            async with pool.acquire() as conn, conn.transaction():
                owned = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM public.owner_delivery_presentations "
                    "WHERE id = $1 AND owning_schema = current_schema() AND surface = 'telegram')",
                    parsed,
                )
                if not owned:
                    continue
                given_up = await owner_delivery.record_retraction(
                    conn, parsed, retracted=request.retracted, reason=request.reason
                )
            if given_up:
                _record_retraction_given_up()
                logger.warning(
                    "Owner-delivery retraction given up for %s after %d failed attempts; "
                    "its buttons still show but are harmless",
                    parsed,
                    owner_delivery.RETRACTION_MAX_FAILED_ATTEMPTS,
                )
            return ApiResponse(data={"recorded": True})
        except owner_delivery.OwnerDeliveryRefused as refused:
            raise HTTPException(status_code=409, detail=refused.reason) from None
        except Exception:
            logger.warning("owner-delivery retraction receipt failed for one schema")
            continue
    raise HTTPException(status_code=404, detail="Presentation not found")
