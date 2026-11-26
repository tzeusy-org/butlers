"""Owner-only RSVP policy routes (bu-5ii4gj.9 S1, REQ-calendar-invitation-docket-001).

Every read and write goes through ``public.calendar_rsvp_policy_dashboard`` (core_388) on the
dashboard-auth login. The definer rechecks the owner's cookie session and, for a write, its CSRF
token in the committing transaction; API-key header authority is refused. There is no MCP tool and
no butler role holds a grant on the table, so this is the only way a policy is written.

No request model carries an actor, owner or authority (``extra="forbid"``), and a predicate outside
the closed vocabulary (an event title or description among them) is refused here and again by the
definer. The route stores a policy; it never answers an invitation.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.responses import JSONResponse

from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.core.temporal.rsvp_policy import PolicyError, parse_predicates

router = APIRouter(
    prefix="/api/calendar/rsvp-policies",
    tags=["calendar"],
    dependencies=[Depends(require_dashboard_owner_control)],
)

_WRITES = frozenset({"create", "update", "revoke"})


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _checked(value: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        return [p.as_json() for p in parse_predicates(value)]
    except PolicyError as exc:
        raise ValueError(str(exc)) from exc


class PolicyCreate(_Body):
    position: int = Field(ge=0, le=1000)
    predicates: list[dict[str, Any]]
    verdict: Literal["accepted", "declined", "tentative"]
    send_updates: Literal["all", "externalOnly", "none"]

    _predicates = field_validator("predicates")(_checked)


class PolicyUpdate(_Body):
    position: int | None = Field(default=None, ge=0, le=1000)
    predicates: list[dict[str, Any]] | None = None
    verdict: Literal["accepted", "declined", "tentative"] | None = None
    send_updates: Literal["all", "externalOnly", "none"] | None = None

    @field_validator("predicates")
    @classmethod
    def _predicates(cls, value: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        return None if value is None else _checked(value)

    @model_validator(mode="after")
    def _something_changes(self) -> PolicyUpdate:
        if all(v is None for v in self.model_dump().values()):
            raise ValueError("an update changes at least one field")
        return self


def _error(status: int, code: str, message: str, reason: str | None = None) -> JSONResponse:
    details = {"reason": reason} if reason else None
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


async def _call(request: Request, action: str, values: dict[str, Any]) -> Any:
    from butlers.api.owner_auth.service import AuthError

    authority = getattr(request.state, "owner_authority", None)
    service = getattr(request.app.state, "owner_auth_service", None)
    if authority is None or service is None:
        return _error(403, "RSVP_POLICY_FORBIDDEN", "Owner cookie authentication required.")
    try:
        result = await service.rsvp_policy_call(
            action, authority, request.headers.get("x-csrf-token", ""), **values
        )
    except AuthError as exc:
        if exc.code == "AUTH_UNAVAILABLE" and action in _WRITES:
            return _error(
                503, "RSVP_POLICY_OUTCOME_UNKNOWN", "Outcome unknown; list the policies to check."
            )
        return _error(exc.status_code, "RSVP_POLICY_" + exc.code, exc.message)
    if result.get("outcome") == "refused":
        reason = result.get("reason")
        if reason == "unknown_policy":
            return _error(404, "RSVP_POLICY_NOT_FOUND", "No such policy.", reason)
        return _error(409, "RSVP_POLICY_REFUSED", "The policy change was refused.", reason)
    return ApiResponse[dict[str, Any]](data=result)


@router.get("", response_model=ApiResponse[dict[str, Any]])
async def list_policies(request: Request) -> Any:
    """Every policy, revoked ones included, in evaluation order."""
    return await _call(request, "list", {})


@router.post("", response_model=ApiResponse[dict[str, Any]])
async def create_policy(body: PolicyCreate, request: Request) -> Any:
    return await _call(request, "create", body.model_dump(mode="json"))


@router.patch("/{policy_id}", response_model=ApiResponse[dict[str, Any]])
async def update_policy(policy_id: uuid.UUID, body: PolicyUpdate, request: Request) -> Any:
    """An edit raises the policy's version, so a card prepared under the old one is superseded."""
    values = body.model_dump(mode="json", exclude_none=True)
    return await _call(request, "update", {"id": str(policy_id)} | values)


@router.post("/{policy_id}/revoke", response_model=ApiResponse[dict[str, Any]])
async def revoke_policy(policy_id: uuid.UUID, request: Request) -> Any:
    return await _call(request, "revoke", {"id": str(policy_id)})
