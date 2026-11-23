"""Owner policy: the one mutation path (REQ-capability-authorization-020).

Every route needs the owner's verified dashboard session. An API key,
connector callback, runtime session or model tool never carries one, and no
MCP tool, domain or connector reaches ``OwnerPolicyWriter``; sw_059's RLS keeps
the switchboard daemon role (where model sessions' tools run) read-only on the
law. ``tighten`` admits only a tightening. A loosening goes through the
``policy_loosen`` dashboard step-up (bu-dhloi1.29): ``step-up/options`` derives
the exact change from the ledger and asks for a passkey assertion bound to it
(``{"required": false}`` when the change is not a loosening), and ``loosen``
spends that step-up, sent as ``X-Dashboard-Step-Up``, with the write. Without
an admitted step-up it is refused ``dashboard_step_up_required``. Expiry still
lapses a term. The holds read returns the same typed ``{stage, constraint,
generation, terms}`` receipt the broker records on the refusal, the effect and
its pending action.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from butlers.api.db import DatabaseManager
from butlers.api.deps import Db
from butlers.api.models import ApiResponse
from butlers.api.models.owner_policy import (
    OwnerPolicy,
    OwnerPolicyHold,
    OwnerPolicyLoosenOptions,
    OwnerPolicyReceipt,
    OwnerPolicyTermCreate,
    OwnerPolicyTermLoosen,
    OwnerPolicyTermTighten,
)
from butlers.api.owner_auth.service import AuthError
from butlers.capability_broker.catalog import CatalogSource, PublishedCatalogSource
from butlers.capability_broker.contracts import BrokerRefused
from butlers.core.dashboard_stepup import verified_dashboard_session
from butlers.core.owner_policy import (
    DeclaredResources,
    NarrowingUnsupported,
    NotLoosening,
    OwnerPolicyWriter,
    PolicyConflict,
    PolicyUnavailable,
    StepUpRequired,
    declared_resource_params,
    holds_for,
    list_terms,
    loosen_binding,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/owner-policy", tags=["owner-policy"])

_LEDGER = "switchboard"


def catalog_source() -> CatalogSource:
    """The published fleet catalog the broker matches terms against."""
    return PublishedCatalogSource()


def _refuse(status: int, error: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error})


def _owner_pool(db: DatabaseManager):
    if verified_dashboard_session.get() is None:
        raise HTTPException(
            status_code=403,
            detail={"error": "owner_dashboard_session_required", "open_dashboard": "/settings"},
        )
    try:
        return db.pool(_LEDGER)
    except KeyError as exc:
        raise _refuse(503, "owner_policy_unavailable") from exc


async def _change(db: DatabaseManager, apply) -> ApiResponse[OwnerPolicyReceipt]:
    pool = _owner_pool(db)
    try:
        async with pool.acquire() as connection:
            receipt = await apply(OwnerPolicyWriter(connection), connection)
    except BrokerRefused as exc:  # the published catalog is unreadable
        raise _refuse(503, "owner_policy_unavailable") from exc
    except StepUpRequired as exc:
        raise _refuse(403, "dashboard_step_up_required") from exc
    except PolicyConflict as exc:
        raise _refuse(409, "owner_policy_conflict") from exc
    except NarrowingUnsupported as exc:
        raise _refuse(422, "owner_policy_narrowing_unsupported") from exc
    except ValueError as exc:
        raise _refuse(422, "owner_policy_invalid") from exc
    except PolicyUnavailable as exc:
        raise _refuse(503, "owner_policy_unavailable") from exc
    return ApiResponse(data=OwnerPolicyReceipt(**receipt))


@router.get("", response_model=ApiResponse[OwnerPolicy])
async def get_owner_policy(*, db: Db) -> ApiResponse[OwnerPolicy]:
    """Every term, active or lapsed, with its version, and the policy generation."""
    pool = _owner_pool(db)
    try:
        async with pool.acquire() as connection:
            policy = await list_terms(connection)
    except PolicyUnavailable as exc:
        raise _refuse(503, "owner_policy_unavailable") from exc
    return ApiResponse(data=OwnerPolicy(**policy))


@router.post("/terms", response_model=ApiResponse[OwnerPolicyReceipt], status_code=201)
async def create_owner_policy_term(
    body: OwnerPolicyTermCreate, *, db: Db
) -> ApiResponse[OwnerPolicyReceipt]:
    """Add a ``never`` or ``always_ask`` term; a retried action returns its receipt.

    A resource narrowing may only name resource arguments the current catalog
    declares for the selected capabilities; an unknown key is refused.
    """

    async def create(writer: OwnerPolicyWriter, connection) -> dict:
        declared = DeclaredResources(frozenset(), frozenset())
        if body.resources:
            catalog = await catalog_source().load(connection)
            declared = declared_resource_params(
                catalog.capabilities, body.selector_kind, body.selector
            )
        return await writer.create(
            body.owner_action_id,
            body.selector_kind,
            body.selector,
            body.term,
            resources=body.resources,
            resource_params=declared.params,
            alias_free_params=declared.alias_free,
            expires_at=body.expires_at,
        )

    return await _change(db, create)


@router.post("/terms/{term_id}/tighten", response_model=ApiResponse[OwnerPolicyReceipt])
async def tighten_owner_policy_term(
    term_id: UUID, body: OwnerPolicyTermTighten, *, db: Db
) -> ApiResponse[OwnerPolicyReceipt]:
    """Tighten a term at its expected version; a loosening is refused here (use ``loosen``)."""
    return await _change(
        db,
        lambda writer, _connection: writer.tighten(
            body.owner_action_id,
            term_id,
            body.expected_version,
            constraint=body.term,
            expires_at=body.expires_at,
        ),
    )


@router.post("/step-up/options", response_model=ApiResponse[dict])
async def owner_policy_stepup_options(
    body: OwnerPolicyLoosenOptions, request: Request, *, db: Db
) -> ApiResponse[dict]:
    """Ask for a passkey assertion bound to one exact loosening; nothing is written.

    The change is derived from the ledger as it is now (term version and
    policy generation), so the confirmation is spent only on that state.
    """
    pool = _owner_pool(db)
    session = verified_dashboard_session.get()
    try:
        async with pool.acquire() as connection:
            # This read scope ends BEFORE the separate authentication call.
            binding = await loosen_binding(
                connection,
                body.owner_action_id,
                body.term_id,
                body.expected_version,
                body.term,
                body.expires_at,
            )
    except NotLoosening:
        return ApiResponse(data={"required": False})
    except PolicyConflict as exc:
        raise _refuse(409, "owner_policy_conflict") from exc
    except ValueError as exc:
        raise _refuse(422, "owner_policy_invalid") from exc
    except PolicyUnavailable as exc:
        raise _refuse(503, "owner_policy_unavailable") from exc
    service = getattr(request.app.state, "owner_auth_service", None)
    if service is None:
        raise _refuse(503, "owner_policy_unavailable")
    try:
        return ApiResponse(data=await service.stepup_options(session, binding))
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None


@router.post("/terms/{term_id}/loosen", response_model=ApiResponse[OwnerPolicyReceipt])
async def loosen_owner_policy_term(
    term_id: UUID, body: OwnerPolicyTermLoosen, *, db: Db
) -> ApiResponse[OwnerPolicyReceipt]:
    """Loosen a term at its expected version, spending the request's step-up."""
    return await _change(
        db,
        lambda writer, _connection: writer.loosen(
            body.owner_action_id,
            term_id,
            body.expected_version,
            constraint=body.term,
            expires_at=body.expires_at,
        ),
    )


@router.get("/holds", response_model=ApiResponse[list[OwnerPolicyHold]])
async def list_owner_policy_holds(
    goal_id: UUID | None = Query(None, description="The goal whose held calls to list."),
    effect_id: UUID | None = Query(
        None, description="An effect, or the pending action parked under its id."
    ),
    refusal_id: UUID | None = Query(None, description="A broker refusal record."),
    *,
    db: Db,
) -> ApiResponse[list[OwnerPolicyHold]]:
    """The owner-law holds on exactly one goal, effect or refusal, with typed receipts."""
    subjects = {
        name: value
        for name, value in (
            ("goal_id", goal_id),
            ("effect_id", effect_id),
            ("refusal_id", refusal_id),
        )
        if value is not None
    }
    pool = _owner_pool(db)
    if len(subjects) != 1:
        raise _refuse(422, "owner_policy_invalid")
    [(subject, subject_id)] = subjects.items()
    try:
        async with pool.acquire() as connection:
            holds = await holds_for(connection, subject, subject_id)
    except Exception as exc:
        logger.warning("owner policy holds unreadable", exc_info=True)
        raise _refuse(503, "owner_policy_unavailable") from exc
    return ApiResponse(data=[OwnerPolicyHold(**hold) for hold in holds])
