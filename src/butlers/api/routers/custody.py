"""Owner-only endpoint custody routes: hold, release, replace, list, read back (bu-s11n0s.1).

Every write goes through ``public.custody_dashboard`` (core_347) on the dashboard-auth login. The
definer rechecks the owner's cookie session and CSRF token under the ``dashboard_auth`` singleton
lock, in the same transaction that commits the hold, its fleet case and its receipt, so a session
revoked before that commit cannot hold or release anything. API-key header authority is refused:
custody needs a current browser session.

No request model carries an actor, owner, authority or raw identifier (``extra="forbid"``): a hold
names owner channel fact ids and the caller's own command id. Repeating a command id returns the
stored result; when a response is lost, ``GET /api/custody/commands/{command_id}`` reads it back.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.responses import JSONResponse

from butlers.account_security import PROVIDER_RECOVERY_DOORS
from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.owner_control import require_dashboard_owner_control

router = APIRouter(
    prefix="/api/custody",
    tags=["custody"],
    dependencies=[Depends(require_dashboard_owner_control)],
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HoldRequest(_Body):
    command_id: uuid.UUID
    fact_ids: list[uuid.UUID] = Field(min_length=1, max_length=16)
    reason: Literal["lost", "stolen", "replaced", "disowned_access"]


class DispositionRequest(_Body):
    command_id: uuid.UUID
    expected_generation: int = Field(ge=1)


def _error(status: int, code: str, message: str, reason: str | None = None) -> JSONResponse:
    details = {"reason": reason} if reason else None
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


async def _custody(
    request: Request,
    action: str,
    values: dict[str, Any],
    *,
    call: str = "custody_call",
    writes: frozenset[str] = frozenset({"hold", "release", "replace"}),
) -> Any:
    from butlers.api.owner_auth.service import AuthError

    authority = getattr(request.state, "owner_authority", None)
    service = getattr(request.app.state, "owner_auth_service", None)
    if authority is None or service is None:
        return _error(403, "CUSTODY_FORBIDDEN", "Owner cookie authentication required.")
    try:
        result = await getattr(service, call)(
            action, authority, request.headers.get("x-csrf-token", ""), **values
        )
    except AuthError as exc:
        if exc.code == "AUTH_UNAVAILABLE" and action in writes:
            # The command may or may not have committed: read it back by its id.
            return _error(
                503, "CUSTODY_OUTCOME_UNKNOWN", "Custody outcome unknown; read the command back."
            )
        return _error(exc.status_code, "CUSTODY_" + exc.code, exc.message)
    if result.get("outcome") == "refused":
        reason = result.get("reason")
        if reason in {"not_found", "unknown_question"}:
            return _error(404, "CUSTODY_HOLD_NOT_FOUND", "No such custody hold.", reason)
        return _error(409, "CUSTODY_REFUSED", "The custody command was refused.", reason)
    return ApiResponse[dict[str, Any]](data=result)


@router.get("/holds", response_model=ApiResponse[dict[str, Any]])
async def list_holds(request: Request) -> Any:
    """Active holds, without identifiers."""
    return await _custody(request, "list", {})


@router.post("/holds", response_model=ApiResponse[dict[str, Any]])
async def hold(body: HoldRequest, request: Request) -> Any:
    return await _custody(request, "hold", body.model_dump(mode="json"))


@router.post("/holds/{hold_id}/release", response_model=ApiResponse[dict[str, Any]])
async def release(hold_id: uuid.UUID, body: DispositionRequest, request: Request) -> Any:
    return await _custody(
        request, "release", {"hold_id": str(hold_id)} | body.model_dump(mode="json")
    )


@router.post("/holds/{hold_id}/replace", response_model=ApiResponse[dict[str, Any]])
async def replace(hold_id: uuid.UUID, body: DispositionRequest, request: Request) -> Any:
    return await _custody(
        request, "replace", {"hold_id": str(hold_id)} | body.model_dump(mode="json")
    )


@router.get("/commands/{command_id}", response_model=ApiResponse[dict[str, Any]])
async def read_command(command_id: uuid.UUID, request: Request) -> Any:
    """The committed result of one command, or ``unknown_command`` if it never committed."""
    return await _custody(request, "status", {"command_id": str(command_id)})


class SecurityAnswerRequest(_Body):
    """``yes`` carries nothing else; ``no`` names the owner-selected exact targets."""

    answer: Literal["yes", "no"]
    command_id: uuid.UUID | None = None
    fact_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=16)
    reason: Literal["lost", "stolen", "replaced", "disowned_access"] | None = None

    @model_validator(mode="after")
    def _shape(self) -> SecurityAnswerRequest:
        bound = (self.command_id, self.fact_ids, self.reason)
        if self.answer == "yes" and any(v is not None for v in bound):
            raise ValueError("a yes answer carries no target")
        if self.answer == "no" and any(v is None for v in bound):
            raise ValueError("a no answer names its command, targets and reason")
        return self


_QUESTION_WRITES = frozenset({"answer"})


async def _question(request: Request, action: str, values: dict[str, Any]) -> Any:
    return await _custody(
        request, action, values, call="security_question_call", writes=_QUESTION_WRITES
    )


@router.get("/security-questions", response_model=ApiResponse[dict[str, Any]])
async def list_security_questions(request: Request) -> Any:
    """Recent security questions with their presentation state and answer."""
    return await _question(request, "list", {})


@router.get("/security-questions/{event_id}", response_model=ApiResponse[dict[str, Any]])
async def read_security_question(event_id: uuid.UUID, request: Request) -> Any:
    """One question, its answer, the owner's selectable targets and the provider recovery door."""
    result = await _question(request, "get", {"event_id": str(event_id)})
    if isinstance(result, ApiResponse):
        data = dict(result.data)
        data["recovery_door"] = PROVIDER_RECOVERY_DOORS.get(str(data.get("provider")))
        return ApiResponse[dict[str, Any]](data=data)
    return result


@router.post("/security-questions/{event_id}/answer", response_model=ApiResponse[dict[str, Any]])
async def answer_security_question(
    event_id: uuid.UUID, body: SecurityAnswerRequest, request: Request
) -> Any:
    """Yes changes nothing; no binds the chosen targets as a custody hold, then the case."""
    return await _question(
        request,
        "answer",
        {"event_id": str(event_id)} | body.model_dump(mode="json", exclude_none=True),
    )
