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
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

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
from butlers.modules.approvals.decision_set_view import ItemView, SetView, load_set_view

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decision-sets", tags=["decision-sets"])


def _item(item: ItemView) -> DecisionSetItemView:
    lineage = item.lineage
    return DecisionSetItemView(
        position=item.position,
        effect_id=str(item.effect_id),
        capability=item.capability,
        explicit_only=item.explicit_only,
        item_digest=item.item_digest,
        recipient_channel=item.recipient_channel,
        exact=item.exact,
        reason=item.reason,  # type: ignore[arg-type]  # closed vocabulary
        title=item.title,
        facts=[DecisionSetFact(label=f.label, value=f.value) for f in item.facts],
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
    return DecisionSetView(
        set_id=str(view.set_id),
        version=view.version,
        goal_id=str(view.goal_id),
        state=view.state,
        expires_at=view.expires_at,
        digest=view.digest,
        items=[_item(item) for item in view.items],
        decidable=view.decidable,
        reason=view.reason,  # type: ignore[arg-type]  # closed vocabulary
        unticked_outcome=view.unticked_outcome,
    )


@router.get("/{set_id}")
async def get_decision_set(
    set_id: UUID,
    *,
    db_mgr: Db,
    _owner: Annotated[str, Depends(require_dashboard_owner_control)],
    version: Annotated[int | None, Query(ge=1)] = None,
) -> ApiResponse[DecisionSetView]:
    """One decision set version as the owner is shown it."""
    try:
        ledger_pool = db_mgr.pool("switchboard")
    except KeyError as exc:
        raise HTTPException(status_code=503, detail="Decision sets unavailable") from exc
    tracker = DegradedSources(logger)
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
    if view is None:
        raise HTTPException(status_code=404, detail="Decision set not found")
    # ApiMeta allows the fleet-wide additive ``sources_degraded`` key (response-conventions).
    meta = ApiMeta.model_validate({"sources_degraded": tracker.names} if tracker.failed else {})
    return ApiResponse(data=_view(view), meta=meta)
