"""Owner-only vendor-door routes: create, confirm, reject, revoke, list (bu-jv1moa.40).

Dark until ``butlers.core.vendor_doors.VENDOR_DOORS_LIVE`` is flipped: every route answers 404
while it is ``False``. Writes go only through the ``public.vendor_door_*`` definer functions under
the ``dashboard_vendor_doors`` capability role (see ``butlers.api.vendor_door_store``).

Template-to-event binding (REQ-vendor-doors-006): no request model has an ingestion-event, origin,
tier, verdict or lineage field (``extra="forbid"`` makes any of them a 422). A route can therefore
only mint a dashboard-origin row that the owner typed; it cannot attach a template to another
event or claim an email/extracted provenance. Proposals that carry an event id come from the
Switchboard/Finance proposers, whose origin the database derives from the caller's role.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.pagination import Page, decode_cursor, encode_cursor
from butlers.api.vendor_door_store import VendorDoorStore, VendorDoorStoreUnavailable
from butlers.core import vendor_doors as flag

CURSOR_SCOPE = "vendor-doors:list:v1"
Kind = Literal["cancel", "manage", "return", "check_in"]
Status = Literal["proposed", "confirmed", "revoked", "rejected", "expired"]


def _require_live() -> None:
    if not flag.VENDOR_DOORS_LIVE:
        raise HTTPException(status_code=404, detail="Not Found")


router = APIRouter(
    prefix="/api/vendor-doors",
    tags=["vendor-doors"],
    dependencies=[Depends(require_dashboard_owner_control), Depends(_require_live)],
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateDoor(_Body):
    kind: Kind
    template: str = Field(min_length=1, max_length=300)
    slot_types: dict[str, Any] = Field(default_factory=dict)
    vendor_entity_id: uuid.UUID | None = Field(default=None, strict=False)
    supersedes: uuid.UUID | None = Field(default=None, strict=False)


class Versioned(_Body):
    expected_version: int = Field(ge=1)


class DoorView(BaseModel):
    id: uuid.UUID
    version: int
    kind: str
    host: str
    template: str
    slot_types: dict[str, Any]
    status: str
    origin: str
    tier: str
    sender_verdict: str | None
    sender_domain: str | None
    vendor_entity_id: uuid.UUID | None
    supersedes_id: uuid.UUID | None
    expires_at: datetime | None
    confirmed_at: datetime | None
    revoked_at: datetime | None
    rejected_at: datetime | None
    created_at: datetime


class Outcome(BaseModel):
    outcome: str
    id: uuid.UUID | None = None
    status: str | None = None
    version: int | None = None
    tier: str | None = None


def _error(status: int, code: str, message: str, reason: str | None = None) -> JSONResponse:
    details = {"reason": reason} if reason else None
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def _store(request: Request) -> VendorDoorStore:
    store = getattr(request.app.state, "vendor_door_store", None)
    if store is None:
        raise VendorDoorStoreUnavailable
    return store


def _respond(result: dict[str, Any]) -> Any:
    if result.get("outcome") == "refused":
        reason = result.get("reason")
        if reason == "not_found":
            return _error(404, "VENDOR_DOOR_NOT_FOUND", "No such vendor door.", reason)
        return _error(409, "VENDOR_DOOR_REFUSED", "The vendor door change was refused.", reason)
    return ApiResponse[Outcome](data=Outcome(**result))


def _session_hash(request: Request) -> str:
    session = getattr(request.state.owner_authority, "session_digest", None)
    label = f"session:{session}" if session else "header"
    return hashlib.sha256(label.encode()).hexdigest()


@router.get("", response_model=Page[DoorView])
async def list_doors(
    request: Request,
    limit: int = Query(20, ge=1, le=50),
    cursor: str | None = Query(None),
    status: Status | None = Query(None),
) -> Any:
    position = (
        cast(
            tuple[datetime, uuid.UUID],
            decode_cursor(cursor, (datetime, uuid.UUID), scope=CURSOR_SCOPE),
        )
        if cursor
        else None
    )
    try:
        rows = await _store(request).list(limit=limit, status=status, after=position)
    except VendorDoorStoreUnavailable:
        return _error(503, "VENDOR_DOORS_UNAVAILABLE", "Vendor-door storage is unavailable.")
    page = rows[:limit]
    next_cursor = (
        encode_cursor((page[-1]["created_at"], page[-1]["id"]), scope=CURSOR_SCOPE)
        if len(rows) > limit
        else None
    )
    return Page[DoorView](items=[DoorView(**r) for r in page], next_cursor=next_cursor, limit=limit)


async def _run(request: Request, call) -> Any:
    try:
        return _respond(await call(_store(request)))
    except VendorDoorStoreUnavailable:
        return _error(503, "VENDOR_DOORS_UNAVAILABLE", "Vendor-door storage is unavailable.")


@router.post("")
async def create_door(body: CreateDoor, request: Request) -> Any:
    return await _run(
        request,
        lambda s: s.create(
            kind=body.kind,
            template=body.template,
            slot_types=body.slot_types,
            vendor_entity_id=body.vendor_entity_id,
            supersedes=body.supersedes,
        ),
    )


@router.post("/{door_id}/confirm")
async def confirm_door(door_id: uuid.UUID, body: Versioned, request: Request) -> Any:
    session = _session_hash(request)
    return await _run(request, lambda s: s.confirm(door_id, body.expected_version, session))


@router.post("/{door_id}/revoke")
async def revoke_door(door_id: uuid.UUID, body: Versioned, request: Request) -> Any:
    return await _run(request, lambda s: s.revoke(door_id, body.expected_version))


@router.post("/{door_id}/reject")
async def reject_door(door_id: uuid.UUID, body: Versioned, request: Request) -> Any:
    return await _run(request, lambda s: s.reject(door_id, body.expected_version))
