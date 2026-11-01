"""Ambient projections: owner-minted glance grants and the glance payload.

Owner session only (the central owner-authentication boundary):
    GET    /api/projections              list grants (never tokens or digests)
    POST   /api/projections              mint a grant; the token is returned once
    DELETE /api/projections/{id}         revoke a grant

Owner session or a grant scoped to this exact route (``OwnerAuthMiddleware``):
    GET    /api/projections/{id}/glance  deterministic glance payload, no LLM call;
                                         ?format=text|png renders it for dumb surfaces

Spec: openspec/changes/add-ambient-projection-grants (REQ-ambient-projections-001..005).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel, Field

from butlers.api.briefing.glance import GlanceCache, compose_glance, glance_png, glance_text
from butlers.api.deps import (
    ButlerConnectionInfo,
    Db,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
    get_pricing,
)
from butlers.api.models import ApiResponse
from butlers.api.projection_grants import (
    ProjectionGrant,
    ProjectionGrantStore,
    projection_grant_store,
)
from butlers.api.routers import dashboard_briefing
from butlers.core.pricing import PricingConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projections", tags=["projections"])


class ProjectionMintRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    surface_kind: Literal["wall_display", "eink", "home_assistant", "other"]
    audience_class: Literal["household", "owner_private"]
    route_scope: Literal["glance"] = "glance"
    expected_fetch_interval_seconds: int = Field(default=300, ge=60, le=86400)


class ProjectionView(BaseModel):
    id: uuid.UUID
    label: str
    surface_kind: str
    audience_class: str
    route_scope: str
    expected_fetch_interval_seconds: int
    created_at: datetime
    revoked_at: datetime | None
    last_fetch_at: datetime | None
    last_fetch_status: str | None


class MintedProjection(ProjectionView):
    token: str
    path: str


class GlanceTopItem(BaseModel):
    kind: str
    label: str
    door_url: str | None


class GlanceCoverage(BaseModel):
    ok: list[str]
    failed: list[str]
    stale: list[str]


class Glance(BaseModel):
    state_class: str
    line: str
    needs_you: int
    top_item: GlanceTopItem | None
    as_of: str
    valid_until: str
    coverage: GlanceCoverage
    audience_class: str


def get_projection_store(request: Request) -> ProjectionGrantStore:
    try:
        return projection_grant_store(request.app.state)
    except (KeyError, RuntimeError) as exc:
        raise HTTPException(
            status_code=503, detail="Projection grant store is not available"
        ) from exc


def _view(grant: ProjectionGrant) -> ProjectionView:
    return ProjectionView(
        id=grant.id,
        label=grant.label,
        surface_kind=grant.surface_kind,
        audience_class=grant.audience_class,
        route_scope=grant.route_scope,
        expected_fetch_interval_seconds=int(grant.expected_fetch_interval.total_seconds()),
        created_at=grant.created_at,
        revoked_at=grant.revoked_at,
        last_fetch_at=grant.last_fetch_at,
        last_fetch_status=grant.last_fetch_status,
    )


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Projection not found")


@router.get("", response_model=ApiResponse[list[ProjectionView]])
async def list_projections(
    store: ProjectionGrantStore = Depends(get_projection_store),
) -> ApiResponse[list[ProjectionView]]:
    return ApiResponse(data=[_view(grant) for grant in await store.list()])


@router.post("", response_model=ApiResponse[MintedProjection], status_code=201)
async def mint_projection(
    body: ProjectionMintRequest,
    store: ProjectionGrantStore = Depends(get_projection_store),
) -> ApiResponse[MintedProjection]:
    grant, token = await store.mint(
        label=body.label,
        surface_kind=body.surface_kind,
        audience_class=body.audience_class,
        route_scope=body.route_scope,
        expected_fetch_interval=timedelta(seconds=body.expected_fetch_interval_seconds),
    )
    return ApiResponse(
        data=MintedProjection(
            **_view(grant).model_dump(),
            token=token,
            path=f"/api/projections/{grant.id}/{grant.route_scope}",
        )
    )


@router.delete("/{projection_id}", response_model=ApiResponse[ProjectionView])
async def revoke_projection(
    projection_id: uuid.UUID,
    store: ProjectionGrantStore = Depends(get_projection_store),
) -> ApiResponse[ProjectionView]:
    grant = await store.revoke(projection_id)
    if grant is None:
        raise _not_found()
    return ApiResponse(data=_view(grant))


def get_glance_cache(request: Request) -> GlanceCache:
    cache = getattr(request.app.state, "glance_cache", None)
    if cache is None:
        cache = request.app.state.glance_cache = GlanceCache()
    return cache


@router.get(
    "/{projection_id}/glance",
    response_model=ApiResponse[Glance],
    responses={
        200: {
            "content": {"text/plain": {}, "image/png": {}},
            "description": "JSON envelope, or the text or 1-bit PNG rendering",
        }
    },
)
async def get_glance(
    projection_id: uuid.UUID,
    request: Request,
    db: Db,
    format: Literal["json", "text", "png"] = "json",
    width: int = Query(default=400, ge=128, le=1600),
    height: int = Query(default=300, ge=128, le=1600),
    store: ProjectionGrantStore = Depends(get_projection_store),
    cache: GlanceCache = Depends(get_glance_cache),
    configs: list[ButlerConnectionInfo] = Depends(get_butler_configs),
    mgr: MCPClientManager = Depends(get_mcp_manager),
    pricing: PricingConfig = Depends(get_pricing),
) -> ApiResponse[Glance] | Response:
    """Return the glance for one projection, under its grant or an owner session.

    Reads the same composed attention model and classifier as
    ``GET /api/dashboard/briefing`` and never calls a model. ``format=text`` and
    ``format=png`` (``width`` x ``height``, 1-bit) render the same payload and
    print its ``valid_until``. Grant fetches share one composition per grant for
    ``GLANCE_CACHE_SECONDS``; an owner preview always recomposes.
    """
    grant: ProjectionGrant | None = getattr(request.state, "projection_grant", None)
    via_grant = grant is not None
    if grant is None:
        # Owner-session preview: the middleware already authenticated the owner.
        grant = await store.get_active(projection_id)
    if grant is None or grant.id != projection_id:
        raise _not_found()

    glance = cache.get(grant.id) if via_grant else None
    if glance is None:
        glance = await _compose(grant, via_grant, db, configs, mgr, pricing)
        if via_grant:
            cache.put(grant.id, glance)
    if via_grant:
        status = "degraded" if glance["coverage"]["failed"] else "ok"
        try:
            await store.record_fetch(grant.id, status)
        except Exception as exc:
            logger.warning("Could not stamp projection fetch for %s: %s", grant.id, exc)
    if format == "text":
        return PlainTextResponse(glance_text(glance))
    if format == "png":
        return Response(glance_png(glance, width, height), media_type="image/png")
    return ApiResponse(data=Glance(**glance))


async def _compose(
    grant: ProjectionGrant,
    via_grant: bool,
    db: Any,
    configs: list[ButlerConnectionInfo],
    mgr: MCPClientManager,
    pricing: PricingConfig,
) -> dict:
    try:
        sw_pool = db.pool("switchboard")
    except KeyError as exc:
        raise HTTPException(
            status_code=503, detail="Switchboard database is not available"
        ) from exc

    as_of = datetime.now(UTC)
    state = await dashboard_briefing._fetch_dashboard_state(
        sw_pool, as_of, db=db, configs=configs, mgr=mgr, pricing=pricing
    )
    if via_grant:
        # This surface is fetching right now, so its own lapse is already over.
        state["attention_items"] = [
            item for item in state["attention_items"] if item.get("projection_id") != str(grant.id)
        ]
    return compose_glance(
        state,
        audience_class=grant.audience_class,
        as_of=as_of,
        refresh=grant.expected_fetch_interval,
    )
