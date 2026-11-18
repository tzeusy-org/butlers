"""Telegram ``/reach``: list, revoke and propose break-through clauses from the owner channel.

Narrowing only (RFC 0038 D4); a proposal is a pending row the owner must confirm on the
dashboard. The route is reachable solely with the connector credential
(admitted in ``owner_auth/http.py``, which sets ``reach_callback_authenticated``) and never
creates an owner principal. It re-verifies that the sender and the chat are the primary owner
channel, then calls the two narrow ``dashboard_attention`` functions (list, revoke one live
clause, record one pending proposal), none of which can create a clause: granting stays with the
dashboard cookie session.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from butlers.api.deps import Db

router = APIRouter(prefix="/api/attention/reach", tags=["attention"])


class ReachTelegramRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "revoke", "propose"]
    sender_id: str = Field(min_length=1, max_length=128)
    chat_id: str = Field(min_length=1, max_length=128)
    clause_id: UUID | None = None
    # Propose: fixed matchers only, never message text. The definer validates them again.
    sender_domain: str | None = Field(default=None, max_length=253)
    channel: Literal["telegram", "email", "whatsapp", "dashboard"] | None = None
    security_kind: (
        Literal[
            "new_sign_in",
            "password_changed",
            "mfa_changed",
            "recovery_changed",
            "deletion_scheduled",
        ]
        | None
    ) = None
    scope: Literal["always", "quiet_hours_only"] | None = None

    @model_validator(mode="after")
    def _matchers_only_with_propose(self):
        has_matcher = any((self.sender_domain, self.channel, self.security_kind, self.scope))
        if has_matcher and self.action != "propose":
            raise ValueError("matchers are only accepted by propose")
        if self.action == "propose" and not any(
            (self.sender_domain, self.channel, self.security_kind)
        ):
            raise ValueError("propose needs at least one matcher")
        return self


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
    # Both the sender and the chat must be the primary owner channel: a list is never sent to a
    # group or any chat other than the owner's own, whatever the connector believed.
    if not (
        await _is_primary_owner(db, body.sender_id) and await _is_primary_owner(db, body.chat_id)
    ):
        return {"data": {"status": "refused"}}
    service = getattr(request.app.state, "owner_auth_service", None)
    if service is None:
        raise HTTPException(503, detail="owner_auth_unavailable")
    try:
        if body.action == "propose":
            matchers = {
                "sender_domain": body.sender_domain,
                "channel": body.channel,
                "security_kind": body.security_kind,
                "scope": body.scope,
            }
            await service.owner_channel_propose(matchers)
            return {"data": {"status": "proposed"}}
        if body.action == "list":
            return {"data": {"status": "ok", "clauses": await service.owner_channel_clauses()}}
        await service.owner_channel_revoke(str(body.clause_id))
    except AuthError as exc:
        if exc.code == "INVALID_REQUEST" and body.action == "revoke":
            return {"data": {"status": "unknown_clause"}}
        if body.action == "propose" and exc.code == "INVALID_REQUEST":
            return {"data": {"status": "invalid"}}
        if body.action == "propose" and exc.code == "RATE_LIMITED":
            return {"data": {"status": "too_many_pending"}}
        raise HTTPException(exc.status_code, exc.message) from None
    return {"data": {"status": "revoked"}}
