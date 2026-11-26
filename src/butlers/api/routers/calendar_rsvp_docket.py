"""The invitation docket: every prepared RSVP answer in one place, approved in one submit.

bu-5ii4gj.9 S6, REQ-calendar-invitation-docket-005. Owner cookie only. The list shows each parked
docket card with its exact call (event, verdict, ``send_updates``), the policy id and version and
the matched facts, beside how many invitations were left unprepared and each policy's agreement
counts. It is read-only.

A submit names the cards the owner ticked. It takes one fresh snapshot, re-runs the same pure
evaluator the docket job uses, and approves each ticked card individually through the existing
approve path (``approve_owning_action``, bound to the exact stored call and to the digest the
dashboard displayed), so each keeps its own audit trail and no bypass exists. A card whose event
etag or policy version changed, that no longer matches its policy, whose facts cannot be read now,
or that is no longer pending is refused and named; the rest are still applied. A card the owner did
not tick is untouched. This is not a goal-runtime decision set.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from butlers.api.deps import Db, MCPClientManager, get_mcp_manager
from butlers.api.models import ApiResponse
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.jobs import calendar_rsvp_docket as docket
from butlers.modules.approvals.execution_context import approval_tool_args_digest

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/calendar/rsvp-docket",
    tags=["calendar"],
    dependencies=[Depends(require_dashboard_owner_control)],
)

MAX_SUBMIT = 50


class DocketFact(BaseModel):
    kind: str
    note: str


class DocketEvent(BaseModel):
    title: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class DocketCard(BaseModel):
    action_id: str
    entry_id: str
    verdict: str
    send_updates: str
    policy_id: str
    policy_version: int
    facts: list[DocketFact]
    event: DocketEvent | None = None
    #: Digest of the exact call shown; a submit echoes it and is refused if the call moved.
    args_digest: str
    #: ``current`` can be approved; ``changed`` names why it can't; ``unverifiable`` could not be
    #: re-checked right now (a read failed). Only ``current`` cards are approved by a submit.
    state: str
    reason: str | None = None


class DocketResponse(BaseModel):
    cards: list[DocketCard]
    #: Pending invitations the docket could not evaluate on this read; an empty docket with a
    #: non-zero count is not a clean docket.
    unprepared: int
    unprepared_reasons: dict[str, int]
    complete: bool
    degraded: list[str]
    policy_agreement: list[dict[str, Any]] | None = None


class SubmitItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: UUID
    args_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SubmitItem] = Field(min_length=1, max_length=MAX_SUBMIT)


class SubmitRefusal(BaseModel):
    action_id: str
    entry_id: str | None = None
    event: DocketEvent | None = None
    reason: str


class SubmitResult(BaseModel):
    approved: list[str]
    refused: list[SubmitRefusal]


def _event(snap: docket.Snapshot, entry_id: str) -> DocketEvent | None:
    entry = snap.entries.get(entry_id)
    if entry is None:
        return None
    row = entry.row
    return DocketEvent(
        title=row.get("title") if isinstance(row.get("title"), str) else None,
        starts_at=row.get("instance_starts_at"),
        ends_at=row.get("instance_ends_at"),
    )


def _facts(evidence: Any) -> list[DocketFact]:
    items = docket.parse_evidence(evidence)
    return [
        DocketFact(kind=str(i["ref"]).removeprefix("rsvp-fact:"), note=str(i.get("note", "")))
        for i in items
        if str(i.get("ref", "")).startswith("rsvp-fact:")
    ]


async def _open_docket_cards(pool: Any) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT id, tool_args, evidence FROM pending_actions "
        "WHERE tool_name = 'calendar_respond' AND status = 'pending' ORDER BY requested_at, id"
    )
    cards = []
    for row in rows:
        marker = docket.parse_marker(row["evidence"])
        if marker is not None:
            cards.append(
                {
                    "id": row["id"],
                    "marker": marker,
                    "tool_args": docket.json_object(row["tool_args"]),
                    "evidence": row["evidence"],
                }
            )
    return cards


async def _context(db: Any) -> tuple[Any, list[Any], list[dict[str, Any]], docket.Snapshot]:
    if not docket.docket_butler_configured():
        raise HTTPException(status_code=503, detail="docket_butler_not_configured")
    try:
        pool = db.pool(docket.docket_butler())
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="docket_butler_unavailable") from exc
    policies = await docket.read_policies(pool)
    if policies is None:
        raise HTTPException(status_code=503, detail="policies_unavailable")
    try:
        cards = await _open_docket_cards(pool)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="open_cards_unavailable") from exc
    snap = await docket.take_snapshot(db, pool, policies, now=datetime.now(UTC))
    return pool, policies, cards, snap


@router.get("", response_model=ApiResponse[DocketResponse])
async def get_docket(request: Request, db: Db) -> ApiResponse[DocketResponse]:
    """Every parked docket card with its exact call, judged against a fresh read."""
    pool, policies, cards, snap = await _context(db)
    by_policy = {p.id: p for p in policies}
    out: list[DocketCard] = []
    for card in cards:
        marker = card["marker"]
        kind, reason = docket.judge_card(marker, snap, by_policy)
        entry_id = str(marker.get("entry_id"))
        out.append(
            DocketCard(
                action_id=str(card["id"]),
                entry_id=entry_id,
                verdict=str(marker.get("verdict")),
                send_updates=str(marker.get("send_updates")),
                policy_id=str(marker.get("policy_id")),
                policy_version=int(marker.get("policy_version") or 0),
                facts=_facts(card["evidence"]),
                event=_event(snap, entry_id),
                args_digest=approval_tool_args_digest(card["tool_args"]),
                state={"current": "current", "stale": "changed"}.get(kind, "unverifiable"),
                reason=reason,
            )
        )
    unprepared = sum(snap.unprepared.values())
    if snap.failure is not None:
        unprepared = max(unprepared, 1)
    agreement = await _agreement(request)
    return ApiResponse[DocketResponse](
        data=DocketResponse(
            cards=out,
            unprepared=unprepared,
            unprepared_reasons=dict(snap.unprepared),
            complete=snap.failure is None and not snap.degraded and unprepared == 0,
            degraded=[snap.failure] if snap.failure else list(snap.degraded),
            policy_agreement=agreement,
        )
    )


async def _agreement(request: Request) -> list[dict[str, Any]] | None:
    """Agreement counts per policy and version through the dashboard definer; ``None`` if down."""
    authority = getattr(request.state, "owner_authority", None)
    service = getattr(request.app.state, "owner_auth_service", None)
    if authority is None or service is None:
        return None
    try:
        counts = (await service.rsvp_ledger_counts(authority)).get("counts")
    except Exception:  # noqa: BLE001 - informational; the docket itself still answers
        logger.warning("rsvp docket: agreement counts unavailable", exc_info=True)
        return None
    return counts if isinstance(counts, list) else None


@router.post("/submit", response_model=ApiResponse[SubmitResult])
async def submit_docket(
    body: SubmitRequest, db: Db, mcp_mgr: MCPClientManager = Depends(get_mcp_manager)
) -> ApiResponse[SubmitResult]:
    """Approve the ticked cards one by one; refuse and name any that changed."""
    from butlers.api.audit_emit import authenticated_principal
    from butlers.api.routers.approvals import (
        _decision_actor_id,
        _decision_memory_writer_for,
        approve_owning_action,
    )

    try:
        authenticated_principal()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="owner_required") from exc
    pool, policies, cards, snap = await _context(db)
    by_policy = {p.id: p for p in policies}
    open_by_id = {card["id"]: card for card in cards}
    approved: list[str] = []
    refused: list[SubmitRefusal] = []
    seen: set[UUID] = set()
    for item in body.items:
        action_id = item.action_id
        if action_id in seen:
            continue
        seen.add(action_id)
        card = open_by_id.get(action_id)
        if card is None:
            refused.append(SubmitRefusal(action_id=str(action_id), reason="no_longer_pending"))
            continue
        marker = card["marker"]
        entry_id = str(marker.get("entry_id"))
        kind, reason = docket.judge_card(marker, snap, by_policy)
        if kind != "current":
            refused.append(
                SubmitRefusal(
                    action_id=str(action_id),
                    entry_id=entry_id,
                    event=_event(snap, entry_id),
                    reason=reason or "unverifiable",
                )
            )
            continue
        if approval_tool_args_digest(card["tool_args"]) != item.args_digest:
            refused.append(
                SubmitRefusal(
                    action_id=str(action_id),
                    entry_id=entry_id,
                    event=_event(snap, entry_id),
                    reason="call_changed",
                )
            )
            continue
        try:
            await approve_owning_action(
                parsed_id=action_id,
                actor_id=_decision_actor_id(None, callback_authenticated=False),
                edits=None,
                action_butler=docket.docket_butler(),
                target_pool=pool,
                db_mgr=db,
                mcp_mgr=mcp_mgr,
                expected_tool_args=card["tool_args"],
                expected_tool_name="calendar_respond",
                promotion_eligible=False,
                decision_memory_writer=_decision_memory_writer_for(
                    db, docket.docket_butler(), pool
                ),
            )
            approved.append(str(action_id))
        except HTTPException as exc:
            refused.append(
                SubmitRefusal(
                    action_id=str(action_id),
                    entry_id=entry_id,
                    event=_event(snap, entry_id),
                    reason=f"approval_refused:{exc.status_code}",
                )
            )
    return ApiResponse[SubmitResult](data=SubmitResult(approved=approved, refused=refused))
