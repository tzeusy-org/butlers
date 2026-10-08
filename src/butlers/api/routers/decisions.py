"""Decisions dashboard API endpoint (bu-ckkpz.2, epic bu-ckkpz "Owner Decision
Desk").

``GET /api/decisions`` exposes the same decision digest bu-ckkpz.4's
Switchboard schedule jobs already compute
(:func:`butlers.jobs.decision_review.compute_decision_digest`) as a
dashboard-consumable list, so the frontend Decisions lane never re-implements
the label-only decision classifier or the beads-export read path -- both stay
owned by ``decision_review.py`` (see its module docstring for why the export
is a read-only bind-mounted JSONL file rather than a live bd query).

Never fabricates an all-clear: when the beads export is missing, stale, or
unreadable, ``compute_decision_digest()`` returns ``available=False`` and
this endpoint returns an empty list with ``meta.decisions_available=False``
(mirrors the fleet-wide degraded-envelope convention -- see CLAUDE.md "API
Conventions -- Degraded-Mode Response Envelope"). A genuine zero (export
readable, zero decision-marked beads currently open) is a real all-clear and
is NOT flagged.

Decision-bead detection is label-only: an open, non-epic bead must carry the
``decision`` label to enter the digest. Title text alone never creates a
decision result; the separate strict lint path identifies legacy-shaped
unlabeled beads for migration. This read-only summary projects validated,
source-authored description/options/default/deadline context, but exposes no
per-decision mutations.

``meta.export_as_of`` (bu-hmdqz.6) carries the beads export file's own
mtime, whenever known, so the frontend can render an honest "as of" plaque
instead of trusting hour-precision computed ages against a single-file
bind-mount that tolerates up to 14 days of staleness before
``decisions_available`` flips to ``False`` (``_STALE_EXPORT_AGE`` in
``decision_review.py``) -- a stale-but-not-yet-14-days-stale export must
still be visible as stale, not rendered as calm current data.

Decision intents (bu-ckkpz.3, ``REQ-owner-decision-desk-001``)
------------------------------------------------------------------
The digest itself stays read-only. A choice is recorded, never applied, here:
``POST /api/decisions/{bead_id}/intent`` (owner) and
``POST /api/decisions/prompts/{prompt_id}/choose`` (the Telegram connector's
scoped callback credential, see ``butlers.api.middleware``) both write one
``switchboard.decision_intents`` row through ``butlers.core.decision_desk``.
The beads CronJob applies it with ``bd``; this process never reaches the
tracker. Each digest item carries its recorded intent so the lane and the
Telegram prompt show the same honest ``pending``/``applied``/``failed`` state.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiMeta, ApiResponse
from butlers.api.models.decision import (
    ChooseDecisionPromptRequest,
    DecisionBeadSummary,
    DecisionIntentSummary,
    DecisionPromptDetail,
    RecordDecisionIntentRequest,
)
from butlers.core.decision_desk import (
    DecisionIntent,
    DecisionIntentError,
    RecordResult,
    get_prompt,
    latest_intents,
    live_intent,
    record_decision_intent,
    record_prompt_choice,
)
from butlers.jobs.decision_review import EscalationHit, compute_decision_digest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/decisions", tags=["decisions"])

_DASHBOARD_ACTOR = "owner@dashboard"
_TELEGRAM_ACTOR = "owner@telegram"
_INTENT_SOURCE = "decision_intents"
_ERROR_STATUS = {"unavailable": 503, "invalid": 422, "conflict": 409}


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _switchboard_pool(db: DatabaseManager) -> asyncpg.Pool:
    try:
        return db.pool("switchboard")
    except KeyError as exc:
        raise HTTPException(status_code=503, detail="decision_intents_unavailable") from exc


def _optional_switchboard_pool(request: Request) -> asyncpg.Pool | None:
    """The digest's intent lookup is best-effort, so it must not 500 without a DB.

    ``GET /api/decisions`` predates intents and must keep answering from the
    export alone; an unwired or failing DB degrades only the ``intent`` field.
    """
    provider = request.app.dependency_overrides.get(_get_db_manager, _get_db_manager)
    try:
        return provider().pool("switchboard")
    except (RuntimeError, KeyError):
        return None


def _intent_summary(intent: DecisionIntent | None) -> DecisionIntentSummary | None:
    if intent is None:
        return None
    return DecisionIntentSummary(
        id=intent.id,
        bead_id=intent.bead_id,
        option=intent.option,
        status=intent.status,
        source=intent.source,
        created_at=intent.created_at,
        failure_reason=intent.failure_reason,
        last_error=intent.last_error,
    )


def _intent_response(result: RecordResult) -> ApiResponse[DecisionIntentSummary]:
    return ApiResponse(data=_intent_summary(result.intent), meta=ApiMeta(created=result.created))


def _refusal(exc: DecisionIntentError) -> HTTPException:
    return HTTPException(status_code=_ERROR_STATUS[exc.kind], detail=exc.reason)


def _escalation_by_decision(escalations: tuple[EscalationHit, ...]) -> dict[str, EscalationHit]:
    """First (== longest-blocked, since ``escalations`` is sorted desc) hit per decision id."""
    by_decision: dict[str, EscalationHit] = {}
    for hit in escalations:
        by_decision.setdefault(hit.decision_id, hit)
    return by_decision


@router.get("", response_model=ApiResponse[list[DecisionBeadSummary]])
async def list_decisions(request: Request) -> ApiResponse[list[DecisionBeadSummary]]:
    """Open decision-marked beads, oldest first, with escalation flags and intent state."""
    digest = compute_decision_digest()

    if not digest.available:
        return ApiResponse(
            data=[],
            meta=ApiMeta(
                decisions_available=False,
                unavailable_reason=digest.unavailable_reason,
                export_as_of=digest.export_as_of,
            ),
        )

    escalation_by_decision = _escalation_by_decision(digest.escalations)

    intents: dict[str, DecisionIntent] = {}
    sources_degraded: list[str] = []
    if digest.open_decisions:
        pool = _optional_switchboard_pool(request)
        try:
            if pool is None:
                raise RuntimeError("switchboard pool unavailable")
            intents = await latest_intents(pool, [bead.id for bead in digest.open_decisions])
        except Exception:  # noqa: BLE001 - degrade the intent field, never the digest
            logger.warning("decisions: intent lookup failed; serving digest without intents")
            sources_degraded.append(_INTENT_SOURCE)

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
                description=bead.description,
                options=list(bead.options) if bead.options is not None else None,
                default=bead.default,
                due_at=bead.due_at,
                structured_details_available=bead.structured_details_available,
                structured_details_unavailable_reason=bead.structured_details_unavailable_reason,
                intent=_intent_summary(intents.get(bead.id)),
            )
        )

    degraded = {"sources_degraded": sources_degraded} if sources_degraded else {}
    return ApiResponse(
        data=items,
        meta=ApiMeta(decisions_available=True, export_as_of=digest.export_as_of, **degraded),
    )


@router.post("/prompts/{prompt_id}/choose", response_model=ApiResponse[DecisionIntentSummary])
async def choose_decision_prompt(
    prompt_id: uuid.UUID,
    body: ChooseDecisionPromptRequest,
    http_request: Request,
    callback_actor: str | None = Header(default=None, alias="X-Butlers-Decision-Actor"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DecisionIntentSummary]:
    """Record the Telegram one-tap choice at ``option_index`` of a prompt.

    Telegram provenance only: the connector's scoped callback credential with
    actor ``owner@telegram``. A dashboard owner records through
    ``POST /api/decisions/{bead_id}/intent`` instead.
    """
    if not getattr(http_request.state, "approval_callback_authenticated", False):
        raise HTTPException(status_code=403, detail="telegram_provenance_required")
    if callback_actor != _TELEGRAM_ACTOR:
        raise HTTPException(status_code=403, detail="telegram_provenance_required")
    pool = _switchboard_pool(db)
    prompt = await get_prompt(pool, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="prompt_not_found")
    try:
        result = await record_prompt_choice(
            pool,
            prompt=prompt,
            option_index=body.option_index,
            actor=_TELEGRAM_ACTOR,
            digest=compute_decision_digest(),
        )
    except DecisionIntentError as exc:
        raise _refusal(exc) from exc
    return _intent_response(result)


@router.get("/prompts/{prompt_id}", response_model=ApiResponse[DecisionPromptDetail])
async def get_decision_prompt(
    prompt_id: uuid.UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DecisionPromptDetail]:
    """Prompt snapshot the connector verifies a ``dsk1`` callback against."""
    pool = _switchboard_pool(db)
    prompt = await get_prompt(pool, prompt_id)
    if prompt is None:
        raise HTTPException(status_code=404, detail="prompt_not_found")
    return ApiResponse(
        data=DecisionPromptDetail(
            id=prompt.id,
            bead_id=prompt.bead_id,
            options=list(prompt.options),
            created_at=prompt.created_at,
            delivery_outcome=prompt.delivery_outcome,
            intent=_intent_summary(await live_intent(pool, prompt.bead_id)),
        )
    )


@router.post("/{bead_id}/intent", response_model=ApiResponse[DecisionIntentSummary])
async def record_intent(
    bead_id: str,
    body: RecordDecisionIntentRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[DecisionIntentSummary]:
    """Record the owner's choice for one open decision. Applied later by the tracker bridge."""
    try:
        result = await record_decision_intent(
            _switchboard_pool(db),
            bead_id=bead_id,
            option=body.option,
            source="dashboard",
            actor=_DASHBOARD_ACTOR,
            digest=compute_decision_digest(),
        )
    except DecisionIntentError as exc:
        raise _refusal(exc) from exc
    return _intent_response(result)
