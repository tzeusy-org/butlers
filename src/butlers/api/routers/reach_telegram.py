"""Telegram ``/reach``: list and revoke break-through clauses from the owner channel.

Narrowing only (RFC 0038 D4). The route is reachable solely with the connector credential
(admitted in ``owner_auth/http.py``, which sets ``reach_callback_authenticated``) and never
creates an owner principal. It re-verifies that the sender is the primary owner channel, then
calls the closed ``owner_channel_attention_call`` surface, which can read and revoke but can
never create a clause: granting stays with the dashboard cookie session.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from butlers.api.deps import Db

router = APIRouter(prefix="/api/attention/reach", tags=["attention"])


class ReachTelegramRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "revoke"]
    sender_id: str = Field(min_length=1, max_length=128)
    chat_id: str = Field(min_length=1, max_length=128)
    clause_id: UUID | None = None


async def _is_primary_owner(db: Db, sender_id: str) -> bool:
    from butlers.identity import resolve_owner_channel_via_definer

    try:
        owner = await resolve_owner_channel_via_definer(
            db.pool("switchboard"), "telegram_bot", sender_id, raise_on_error=True
        )
    except Exception:  # noqa: BLE001 -- unavailable lookup fails closed
        raise HTTPException(503, detail="owner_lookup_unavailable") from None
    return owner is not None and owner[1]


@router.post("/telegram")
async def reach_telegram(body: ReachTelegramRequest, request: Request, db: Db):
    from butlers.api.owner_auth.service import AuthError

    if not getattr(request.state, "reach_callback_authenticated", False):
        raise HTTPException(403, detail="connector_required")
    if body.action == "revoke" and body.clause_id is None:
        raise HTTPException(422, detail="clause_id_required")
    if not await _is_primary_owner(db, body.sender_id):
        return {"data": {"status": "refused"}}
    service = getattr(request.app.state, "owner_auth_service", None)
    if service is None:
        raise HTTPException(503, detail="owner_auth_unavailable")
    try:
        if body.action == "list":
            result = await service.owner_channel_attention_call("clause_list_owner")
            return {"data": {"status": "ok", "clauses": result.get("clauses", [])}}
        await service.owner_channel_attention_call("clause_revoke_owner", id=str(body.clause_id))
    except AuthError as exc:
        if exc.code == "INVALID_REQUEST" and body.action == "revoke":
            return {"data": {"status": "unknown_clause"}}
        raise HTTPException(exc.status_code, exc.message) from None
    return {"data": {"status": "revoked"}}
