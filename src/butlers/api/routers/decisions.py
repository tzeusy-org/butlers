"""Decisions dashboard API endpoint (bu-ckkpz.2, epic bu-ckkpz "Owner Decision
Desk").

``GET /api/decisions`` exposes the same decision digest bu-ckkpz.4's
Switchboard schedule jobs already compute
(:func:`butlers.jobs.decision_review.compute_decision_digest`) as a
dashboard-consumable list, so the frontend Decisions lane never re-implements
the title-marker heuristic or the beads-export read path -- both stay owned
by ``decision_review.py`` (see its module docstring for why the export is a
read-only bind-mounted JSONL file rather than a live bd query).

Never fabricates an all-clear: when the beads export is missing, stale, or
unreadable, ``compute_decision_digest()`` returns ``available=False`` and
this endpoint returns an empty list with ``meta.decisions_available=False``
(mirrors the fleet-wide degraded-envelope convention -- see CLAUDE.md "API
Conventions -- Degraded-Mode Response Envelope"). A genuine zero (export
readable, zero decision-marked beads currently open) is a real all-clear and
is NOT flagged.

Decision-bead detection is a heuristic today (title markers), not yet the
structured options/default/deadline convention bu-ckkpz.1 is adding -- see
``decision_review.py``'s own "Decision-bead detection is a heuristic, not yet
a convention" docstring section and the tracked follow-up bu-97qrw.
"""

from __future__ import annotations

from fastapi import APIRouter

from butlers.api.models import ApiMeta, ApiResponse
from butlers.api.models.decision import DecisionBeadSummary
from butlers.jobs.decision_review import EscalationHit, compute_decision_digest

router = APIRouter(prefix="/api/decisions", tags=["decisions"])


def _escalation_by_decision(escalations: tuple[EscalationHit, ...]) -> dict[str, EscalationHit]:
    """First (== longest-blocked, since ``escalations`` is sorted desc) hit per decision id."""
    by_decision: dict[str, EscalationHit] = {}
    for hit in escalations:
        by_decision.setdefault(hit.decision_id, hit)
    return by_decision


@router.get("", response_model=ApiResponse[list[DecisionBeadSummary]])
async def list_decisions() -> ApiResponse[list[DecisionBeadSummary]]:
    """Open decision-marked beads, oldest first, with escalation flags."""
    digest = compute_decision_digest()

    if not digest.available:
        return ApiResponse(
            data=[],
            meta=ApiMeta(
                decisions_available=False,
                unavailable_reason=digest.unavailable_reason,
            ),
        )

    escalation_by_decision = _escalation_by_decision(digest.escalations)

    items = []
    for bead in digest.open_decisions:
        hit = escalation_by_decision.get(bead.id)
        items.append(
            DecisionBeadSummary(
                id=bead.id,
                title=bead.title,
                priority=bead.priority,
                created_at=bead.created_at,
                age_hours=bead.age.total_seconds() / 3600,
                escalated=hit is not None,
                escalated_blocked_id=hit.blocked_id if hit else None,
                escalated_blocked_title=hit.blocked_title if hit else None,
                escalated_blocked_kind=hit.blocked_kind if hit else None,
                escalated_block_hours=(hit.block_age.total_seconds() / 3600) if hit else None,
            )
        )

    return ApiResponse(data=items, meta=ApiMeta(decisions_available=True))
