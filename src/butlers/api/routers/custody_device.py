"""Owner-selected device binding and last-fix read for a custody episode (bu-s11n0s.1 S5f).

``PUT /api/custody/episodes/{episode_id}/device`` records, as case evidence, which registered
OwnTracks endpoint the owner says is the device in question. Nothing is inferred and nothing is
held, released or answered by it. ``GET`` reads the binding back with the newest Chronicler
``device_last_fix`` evidence, judged for freshness now: a fix past its ``fresh_until`` reads as
unknown with the old time labelled stale. Only the fix age and a named place are returned; raw
coordinates stay in the connector and Chronicler (RFC 0038, security.md). Context evidence like
this never answers the security question (REQ-endpoint-custody-holds-009).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from butlers.api.deps import Db
from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.chronicler.custody_device_fix import (
    BINDING_CONTRIBUTOR,
    BINDING_KIND,
    CONTRIBUTOR,
    EVIDENCE_KIND,
)
from butlers.core.fleet_cases import contribute_evidence

router = APIRouter(
    prefix="/api/custody/episodes",
    tags=["custody"],
    dependencies=[Depends(require_dashboard_owner_control)],
)

_CASE_SQL = (
    "SELECT id::text AS id, state FROM public.fleet_cases WHERE correlation_key = $1 LIMIT 1"
)
_ENDPOINT_SQL = (
    "SELECT 1 FROM connector_registry WHERE connector_type = 'owntracks' "
    "AND endpoint_identity = $1 AND deleted_at IS NULL AND archived_at IS NULL"
)
_BINDING_SQL = (
    "SELECT payload ->> 'endpoint_identity' FROM public.fleet_case_evidence "
    "WHERE case_id = $1::uuid AND contributor = $2 "
    "AND kind = $3 ORDER BY contributed_at DESC, id DESC LIMIT 1"
)
_FIX_SQL = (
    "SELECT payload FROM public.fleet_case_evidence WHERE case_id = $1::uuid "
    "AND contributor = $2 AND kind = $3 AND ref LIKE $4 ESCAPE '\\' "
    "ORDER BY contributed_at DESC, id DESC LIMIT 1"
)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceBinding(_Body):
    endpoint_identity: str = Field(min_length=1, max_length=200)


def _error(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def judge_fix(payload: dict[str, Any] | None, *, now: datetime) -> dict[str, Any]:
    """Apply freshness now: what the owner may read, never coordinates. Pure."""
    if not payload:
        return {"status": "unknown", "reason": "not_observed_yet"}
    if payload.get("status") != "known":
        return {
            k: payload[k]
            for k in ("status", "reason", "stale_fix_at", "observed_at")
            if k in payload
        }
    fix_at = datetime.fromisoformat(payload["fix_at"])
    if now > datetime.fromisoformat(payload["fresh_until"]):
        return {
            "status": "unknown",
            "reason": "stale",
            "stale_fix_at": payload["fix_at"],
            "observed_at": payload.get("observed_at"),
        }
    return {
        "status": "known",
        "fix_at": payload["fix_at"],
        "age_seconds": max(0, int((now - fix_at).total_seconds())),
        "place": payload.get("place"),
        "observed_at": payload.get("observed_at"),
    }


async def _open_case(pool: Any, episode_id: uuid.UUID) -> dict[str, Any] | None:
    row = await pool.fetchrow(_CASE_SQL, f"endpoint_custody:{episode_id}")
    return dict(row) if row else None


@router.put("/{episode_id}/device", response_model=ApiResponse[dict[str, Any]])
async def bind_device(episode_id: uuid.UUID, body: DeviceBinding, request: Request, db: Db) -> Any:
    authority = getattr(request.state, "owner_authority", None)
    if getattr(authority, "method", None) != "cookie":
        return _error(403, "CUSTODY_FORBIDDEN", "Owner cookie authentication required.")
    pool = db.pool("switchboard")
    case = await _open_case(pool, episode_id)
    if case is None or case["state"] == "closed":
        return _error(404, "CUSTODY_EPISODE_NOT_FOUND", "No open custody episode.")
    if await pool.fetchval(_ENDPOINT_SQL, body.endpoint_identity) is None:
        return _error(409, "CUSTODY_DEVICE_UNKNOWN", "That OwnTracks device is not registered.")
    current = await pool.fetchval(_BINDING_SQL, case["id"], BINDING_CONTRIBUTOR, BINDING_KIND)
    if current == body.endpoint_identity:
        return ApiResponse[dict[str, Any]](data={"bound": True, "newly_recorded": False})
    # Every selection is its own row (a fresh ref), so selecting A, then B, then A again leaves A
    # effective: the newest selection wins and an earlier one is never "already recorded".
    _row, created = await contribute_evidence(
        pool,
        case_id=case["id"],
        contributor=BINDING_CONTRIBUTOR,
        kind=BINDING_KIND,
        ref=f"selection:{uuid.uuid4()}",
        payload={
            "endpoint_identity": body.endpoint_identity,
            "selected_by": "owner",
            "authority": authority.method,
        },
    )
    return ApiResponse[dict[str, Any]](data={"bound": True, "newly_recorded": created})


@router.get("/{episode_id}/device", response_model=ApiResponse[dict[str, Any]])
async def read_device(episode_id: uuid.UUID, db: Db) -> Any:
    pool = db.pool("switchboard")
    case = await _open_case(pool, episode_id)
    if case is None:
        return _error(404, "CUSTODY_EPISODE_NOT_FOUND", "No such custody episode.")
    endpoint = await pool.fetchval(_BINDING_SQL, case["id"], BINDING_CONTRIBUTOR, BINDING_KIND)
    if endpoint is None:
        return ApiResponse[dict[str, Any]](data={"bound": False, "last_fix": None})
    like = endpoint.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ":%"
    payload = await pool.fetchval(_FIX_SQL, case["id"], CONTRIBUTOR, EVIDENCE_KIND, like)
    if isinstance(payload, str):
        import json

        payload = json.loads(payload)
    return ApiResponse[dict[str, Any]](
        data={
            "bound": True,
            "last_fix": judge_fix(payload, now=datetime.now(UTC)),
            "evidence_only": True,
        }
    )
