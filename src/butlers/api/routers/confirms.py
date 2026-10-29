"""Informational answers: exact connector callback and owner-authenticated cards.

The trusted goal-runtime composition installs app.state.confirm_service only at the
canonical switch. An absent composition fails explicitly; no anonymous fallback exists.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from typing_extensions import TypedDict

from butlers.core.confirm_service import ConfirmCursorError, ConfirmService
from butlers.core.owner_auth_context import verified_http_principal

router = APIRouter(prefix="/api/confirms", tags=["confirms"])


class Callback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=64)
    sender_id: str = Field(min_length=1, max_length=128)
    chat_id: str = Field(min_length=1, max_length=128)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_index: StrictInt = Field(ge=0, le=3)


ConfirmStatus = Literal["pending", "answered", "expired"]
ApplicationStatus = Literal["idle", "pending", "applied", "error"]


class ConfirmOptionOutput(TypedDict):
    value: str
    label: str


class ConfirmCardOutput(TypedDict):
    id: str
    text: str
    options: list[ConfirmOptionOutput]
    status: ConfirmStatus
    answer_index: int | None
    application_status: ApplicationStatus
    expires_at: datetime
    created_at: datetime


class ConfirmPageMeta(TypedDict):
    next_cursor: str | None


class ConfirmPageOutput(TypedDict):
    data: list[ConfirmCardOutput]
    meta: ConfirmPageMeta


class ConfirmHandledOutput(TypedDict):
    status: Literal["answered", "expired"]
    application_status: ApplicationStatus


class ConfirmAlreadyHandledOutput(TypedDict):
    status: Literal["already_handled"]
    state: ConfirmStatus
    application_status: ApplicationStatus


class ConfirmRefusedOutput(TypedDict):
    status: Literal["refused"]


class ConfirmAnswerOutput(TypedDict):
    data: ConfirmHandledOutput | ConfirmAlreadyHandledOutput | ConfirmRefusedOutput


def _service(request: Request) -> ConfirmService:
    service = getattr(request.app.state, "confirm_service", None)
    if service is None:
        raise HTTPException(503, detail="confirm_runtime_unavailable")
    return service


def _owner():
    if verified_http_principal.get() != "owner":
        raise HTTPException(403, detail="owner_required")


def _card(row):
    # Never expose signing material, internal routing/sink references or raw quoted links.
    prompt = row["prompt"]
    return {
        "id": str(row["id"]),
        "text": prompt["text"],
        "options": prompt["options"],
        "status": row["status"],
        "answer_index": row["answer_index"],
        "application_status": row["application_status"],
        "expires_at": row["expires_at"],
        "created_at": row["created_at"],
    }


@router.post("/callback", responses={200: {"model": ConfirmAnswerOutput}})
async def callback(body: Callback, request: Request):
    if not getattr(request.state, "confirm_callback_authenticated", False):
        raise HTTPException(403, detail="connector_required")
    return {
        "data": await _service(request).answer_callback(
            body.token, sender_id=body.sender_id, chat_id=body.chat_id
        )
    }


@router.get("", responses={200: {"model": ConfirmPageOutput}})
async def cards(request: Request, before: UUID | None = None):
    _owner()
    try:
        page = await _service(request).list_owner(before=before)
    except ConfirmCursorError:
        raise HTTPException(400, detail="invalid_confirm_cursor") from None
    return {
        "data": [_card(row) for row in page["rows"]],
        "meta": {"next_cursor": page["next_cursor"]},
    }


@router.post("/{confirm_id}/answer", responses={200: {"model": ConfirmAnswerOutput}})
async def answer(confirm_id: UUID, body: Answer, request: Request):
    _owner()
    return {"data": await _service(request).answer_owner(confirm_id, body.option_index)}
