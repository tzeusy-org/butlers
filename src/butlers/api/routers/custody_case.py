"""Owner read of one custody episode's case and its evidence summaries (bu-s11n0s.1 S5g).

``GET /api/custody/episodes/{episode_id}/case`` feeds the protected case page. It returns the case
state and bounded summaries of the evidence contributors have appended to the episode's fleet
case (the engine's hold evidence, Finance's held window, the Chronicler's device last fix). Only
allow-listed scalar fields of each summary leave: no merchant, amount, endpoint identity, hold
identifier, coordinate or free text. A contributor or kind this module does not know contributes
its kind and time only. The read says nothing about whether anything is safe; the page shows
unavailable when this read fails (REQ-endpoint-custody-holds-011). The route is an allow-listed
projection over ``public.fleet_case_evidence``, not a copy of it, and it needs cookie authority.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from starlette.responses import JSONResponse

from butlers.api.deps import Db
from butlers.api.models import ApiResponse, ErrorDetail, ErrorResponse
from butlers.api.owner_control import require_dashboard_owner_control

router = APIRouter(
    prefix="/api/custody/episodes",
    tags=["custody"],
    dependencies=[Depends(require_dashboard_owner_control)],
)

_LIMIT = 100

_CASE_SQL = (
    "SELECT id::text AS id, state, outcome, opened_at, closed_at FROM public.fleet_cases "
    "WHERE correlation_key = $1 "
    "ORDER BY (state <> 'closed') DESC, opened_at DESC, id DESC LIMIT 1"
)
_EVIDENCE_SQL = (
    "SELECT contributor, kind, payload, contributed_at FROM public.fleet_case_evidence "
    "WHERE case_id = $1::uuid ORDER BY contributed_at DESC, id DESC LIMIT $2"
)

# (contributor, kind) -> the only payload fields that may leave, all scalars.
_SUMMARY_FIELDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("endpoint_custody", "hold"): ("target_kind", "generation", "reason"),
    ("endpoint_custody", "release"): ("target_kind", "generation"),
    ("endpoint_custody", "replace"): ("target_kind", "generation"),
    ("finance", "held_window"): (
        "window_start",
        "window_end",
        "transactions",
        "debits",
        "credits",
        "first_seen_merchants",
        "anomalies",
    ),
    ("finance", "held_window_final"): (
        "window_start",
        "window_end",
        "transactions",
        "debits",
        "credits",
        "first_seen_merchants",
        "anomalies",
    ),
    ("chronicler", "device_last_fix"): (
        "status",
        "reason",
        "fix_at",
        "stale_fix_at",
        "fresh_until",
        "place",
        "age_seconds_at_observation",
        "observed_at",
    ),
}


def _error(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def _scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _stale_device_fix(summary: dict[str, Any], now: datetime) -> dict[str, Any]:
    """A device reading past its freshness window reads unknown, with the old time kept apart."""
    until = summary.get("fresh_until")
    fix_at = summary.get("fix_at")
    if summary.get("status") != "known" or not isinstance(until, str):
        return summary
    if now <= datetime.fromisoformat(until):
        return summary
    return {
        "status": "unknown",
        "reason": "stale",
        "stale_fix_at": fix_at,
        "observed_at": summary.get("observed_at"),
    }


def summarize(
    contributor: str, kind: str, payload: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """The allow-listed, scalar-only summary of one evidence row, judged for freshness now. Pure."""
    fields = _SUMMARY_FIELDS.get((contributor, kind), ())
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = None
    if not isinstance(payload, dict):
        return {}
    summary: dict[str, Any] = {}
    for name in fields:
        value = payload.get(name)
        if name == "anomalies" and isinstance(value, dict):
            summary[name] = {"total": value.get("total")}  # counts only, not the type breakdown
        elif _scalar(value) and name in payload:
            summary[name] = value
    if kind == "device_last_fix":  # the kind is unique to the device-fix contributor
        return _stale_device_fix(summary, now or datetime.now(UTC))
    return summary


@router.get("/{episode_id}/case", response_model=ApiResponse[dict[str, Any]])
async def read_case(episode_id: uuid.UUID, request: Request, db: Db) -> Any:
    if getattr(getattr(request.state, "owner_authority", None), "method", None) != "cookie":
        return _error(403, "CUSTODY_FORBIDDEN", "Owner cookie authentication required.")
    pool = db.pool("switchboard")
    case = await pool.fetchrow(_CASE_SQL, f"endpoint_custody:{episode_id}")
    if case is None:
        return _error(404, "CUSTODY_EPISODE_NOT_FOUND", "No such custody episode.")
    rows = await pool.fetch(_EVIDENCE_SQL, case["id"], _LIMIT)
    now = datetime.now(UTC)
    evidence = [
        {
            "contributor": row["contributor"],
            "kind": row["kind"],
            "contributed_at": row["contributed_at"].isoformat(),
            "summary": summarize(row["contributor"], row["kind"], row["payload"], now=now),
        }
        for row in rows
    ]
    return ApiResponse[dict[str, Any]](
        data={
            "state": case["state"],
            "outcome": case["outcome"],
            "opened_at": case["opened_at"].isoformat(),
            "closed_at": case["closed_at"].isoformat() if case["closed_at"] else None,
            "evidence": evidence,
            "truncated": len(rows) == _LIMIT,
        }
    )
