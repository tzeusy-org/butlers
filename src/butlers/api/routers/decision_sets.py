"""Owner-facing decision set read route (RFC 0038 D13, M7; bu-jv1moa.27).

``GET /api/decision-sets/{set_id}`` returns one version (the latest unless ``version`` is given)
with each item's exact stored call, server lineage facts and an ``exact`` flag. It reads the
producer's immutable set rows and each item's parked approval row; it never creates or changes
either, and grants no authority. An item or set that cannot be shown completely says so
(``exact: false``, ``decidable: false``) and the owner falls back to the approval page; a pool
that could not be read is named in ``meta.sources_degraded``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from butlers.api.degraded import DegradedSources
from butlers.api.deps import Db
from butlers.api.models import ApiMeta, ApiResponse
from butlers.api.models.approval import ApprovalGoalLineage, ApprovalLineageFact
from butlers.api.models.decision_set import (
    DecisionSetFact,
    DecisionSetItemView,
    DecisionSetView,
)
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.api.routers.approvals import _find_named_approvals_pools
from butlers.capability_broker.owner_decisions import OwnerContext, OwnerDecisionRefused
from butlers.modules.approvals.decision_set_card import approve_selection
from butlers.modules.approvals.decision_set_delivery import (
    Resolver,
    SelectionRefused,
    close_set_subject,
    decide_selected,
)
from butlers.modules.approvals.decision_set_view import ItemView, SetView, load_set_view

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decision-sets", tags=["decision-sets"])


def _item(item: ItemView, *, default: bool) -> DecisionSetItemView:
    lineage = item.lineage
    return DecisionSetItemView(
        position=item.position,
        effect_id=str(item.effect_id),
        capability=item.capability,
        explicit_only=item.explicit_only,
        in_default_selection=default,
        item_digest=item.item_digest,
        recipient_channel=item.recipient_channel,
        exact=item.exact,
        reason=item.reason,  # type: ignore[arg-type]  # closed vocabulary
        title=item.title,
        facts=[DecisionSetFact(label=f.label, value=f.value) for f in item.facts],
        claims=[DecisionSetFact(label=c.label, value=c.value) for c in item.claims],
        lineage=None
        if lineage is None
        else ApprovalGoalLineage(
            goal_id=lineage.goal_id,
            invocation_id=lineage.invocation_id,
            depth=lineage.depth,
            exposed=lineage.exposed,
            origin=lineage.origin,  # type: ignore[arg-type]  # approvals_020 CHECK
            exposure_cause=lineage.exposure_cause,
            rule_possible=lineage.rule_possible,
            facts=[ApprovalLineageFact(label=f.label, value=f.value) for f in lineage.facts],
        ),
    )


def _view(view: SetView) -> DecisionSetView:
    chosen = approve_selection(view) or {}
    return DecisionSetView(
        set_id=str(view.set_id),
        version=view.version,
        goal_id=str(view.goal_id),
        state=view.state,
        expires_at=view.expires_at,
        digest=view.digest,
        items=[_item(item, default=item.effect_id in chosen) for item in view.items],
        decidable=view.decidable,
        reason=view.reason,  # type: ignore[arg-type]  # closed vocabulary
        unticked_outcome=view.unticked_outcome,
    )


_REFUSAL_STATUS = {"resource": 404, "unavailable": 503}


def get_decision_set_resolver(request: Request) -> Resolver | None:
    """The app's decision-set resolver, or ``None`` while the path is not wired (dark)."""
    return getattr(request.app.state, "decision_set_resolver", None)


def refusal(reason: str) -> HTTPException:
    """The closed refusal of a decision that decided nothing."""
    return HTTPException(
        status_code=_REFUSAL_STATUS.get(reason, 409),
        detail={"error": "decision_set_refused", "reason": reason},
    )


async def read_set_view(
    db_mgr: Any, set_id: UUID, version: int | None, tracker: DegradedSources
) -> tuple[Any, SetView | None]:
    """``(switchboard pool, view)`` of one set version; 503 when the sets cannot be read."""
    try:
        ledger_pool = db_mgr.pool("switchboard")
    except KeyError as exc:
        raise HTTPException(status_code=503, detail="Decision sets unavailable") from exc
    action_pools = await _find_named_approvals_pools(db_mgr, "pending_actions", tracker=tracker)
    try:
        view = await load_set_view(
            set_id,
            version,
            ledger_pool=ledger_pool,
            action_pools=action_pools,
            now=datetime.now(UTC),
            on_pool_failure=lambda name: tracker.mark(name, msg="Decision set item pool failed"),
        )
    except Exception as exc:  # noqa: BLE001 - the set rows themselves were unreadable
        logger.warning("decision set read failed", exc_info=True)
        raise HTTPException(status_code=503, detail="Decision sets unavailable") from exc
    return ledger_pool, view


@router.get("/{set_id}")
async def get_decision_set(
    set_id: UUID,
    *,
    db_mgr: Db,
    _owner: Annotated[str, Depends(require_dashboard_owner_control)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ApiResponse[DecisionSetView]:
    """One decision set version as the owner is shown it."""
    tracker = DegradedSources(logger)
    _, view = await read_set_view(db_mgr, set_id, version, tracker)
    if view is None:
        raise HTTPException(status_code=404, detail="Decision set not found")
    # ApiMeta allows the fleet-wide additive ``sources_degraded`` key (response-conventions).
    meta = ApiMeta.model_validate({"sources_degraded": tracker.names} if tracker.failed else {})
    return ApiResponse(data=_view(view), meta=meta)


class DecisionSetPick(BaseModel):
    effect_id: UUID
    #: The ``item_digest`` the owner was shown for this item.
    item_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DecisionSetDecisionRequest(BaseModel):
    """Only the version and the ticked items with their shown digests: no call content."""

    version: int = Field(ge=1)
    decision: Literal["approve", "reject"]
    selected: list[DecisionSetPick] = Field(min_length=1, max_length=50)


class DecisionSetItemOutcome(BaseModel):
    effect_id: str
    outcome: str
    status: str


class DecisionSetDecisionResult(BaseModel):
    status: Literal["approved", "rejected"]
    set_id: str
    version: int
    outcomes: list[DecisionSetItemOutcome]


@router.post("/{set_id}/decision")
async def decide_decision_set(
    set_id: UUID,
    request: Annotated[DecisionSetDecisionRequest, Body()],
    *,
    db_mgr: Db,
    owner: Annotated[str, Depends(require_dashboard_owner_control)],
    resolver: Annotated[Resolver | None, Depends(get_decision_set_resolver)],
) -> ApiResponse[DecisionSetDecisionResult]:
    """Decide the owner's ticked items of one shown version, exactly or not at all."""
    if resolver is None:
        raise refusal("unavailable")
    tracker = DegradedSources(logger)
    ledger_pool, view = await read_set_view(db_mgr, set_id, request.version, tracker)
    if view is None or view.version != request.version:
        raise refusal("resource")
    picks = {pick.effect_id: pick.item_digest for pick in request.selected}
    if len(picks) != len(request.selected):
        raise refusal("policy")
    try:
        decided = await decide_selected(
            resolver, view, request.decision, picks, OwnerContext(owner, "dashboard")
        )
    except SelectionRefused as exc:
        raise refusal("stale" if exc.reason == "not_decidable" else "policy") from exc
    except OwnerDecisionRefused as exc:
        raise refusal(exc.reason) from exc
    try:
        async with ledger_pool.acquire() as conn, conn.transaction():
            await close_set_subject(conn, view, request.decision)
    except Exception:  # noqa: BLE001 - the decision stands; a card left open is retracted later
        logger.warning("decision set card subject close failed", exc_info=True)
    return ApiResponse(
        data=DecisionSetDecisionResult(
            status="approved" if request.decision == "approve" else "rejected",
            set_id=str(decided.set_id),
            version=decided.version,
            outcomes=[
                DecisionSetItemOutcome(
                    effect_id=str(o.effect_id), outcome=o.outcome, status=o.status
                )
                for o in decided.outcomes
            ],
        ),
        meta=ApiMeta.model_validate({"sources_degraded": tracker.names} if tracker.failed else {}),
    )
