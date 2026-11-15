"""Public MCP kill switches: owner read and write of ``mcp_surface.controls`` (bu-y66o00.9).

  GET /api/settings/public-mcp            the eight switches and the console summary
  GET /api/settings/public-mcp/summary    the summary alone (the console panel's own fetch)
  PUT /api/settings/public-mcp/{domain}   set one switch (owner dashboard session only)

REQ-dashboard-settings-console-001 and REQ-public-mcp-authorization-009. The write needs the
owner's verified dashboard session (an API key, connector or runtime session never carries
one) and answers 401 without it, before any database work. The actor recorded is always the
dashboard owner; the request cannot name one. The table is owned by the migration principal,
which is this API's database login, so no grant is involved (see
``butlers.public_mcp_controls``). Disabling and re-enabling are one request each.

A change reaches the public server by replication, within 15 minutes; the Relationship proposal
importer reads the primary at its next pass. Nothing here claims otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from butlers.api.deps import Db
from butlers.api.models import ApiResponse
from butlers.api.models.public_mcp_controls import (
    PublicMcpControl,
    PublicMcpControls,
    PublicMcpControlWrite,
    PublicMcpSummary,
)
from butlers.core.dashboard_stepup import verified_dashboard_session
from butlers.public_mcp_controls import (
    ACTOR_DASHBOARD,
    CONTROL_DOMAINS,
    LIST_SQL,
    READ_ONE_SQL,
    WRITE_SQL,
    UnknownDomain,
    check_domain,
    summarize,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/public-mcp", tags=["settings-public-mcp"])

# Any dashboard pool reaches the same database as the same login; switchboard is always present.
_POOL = "switchboard"


def _refuse(status: int, error: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": error})


def _pool(db: Any) -> Any:
    try:
        return db.pool(_POOL)
    except KeyError:
        raise _refuse(503, "public_mcp_controls_unavailable") from None


def _control(domain: str, row: Any | None) -> PublicMcpControl:
    if row is None:
        return PublicMcpControl(domain=domain, state=None, changed_at=None, changed_by=None)
    return PublicMcpControl(
        domain=domain,
        state=row["state"],
        changed_at=row["changed_at"],
        changed_by=row["changed_by"],
    )


async def _read(db: Any) -> PublicMcpControls:
    try:
        rows = await _pool(db).fetch(LIST_SQL.format("$1::text[]"), list(CONTROL_DOMAINS))
    except HTTPException:
        raise
    except Exception:
        # A driver message can carry statement text; the closed error is enough.
        logger.warning("public MCP controls: read failed")
        raise _refuse(503, "public_mcp_controls_unavailable") from None
    by_domain = {row["domain"]: row for row in rows}
    controls = [_control(domain, by_domain.get(domain)) for domain in CONTROL_DOMAINS]
    states = {c.domain: c.state for c in controls if c.state is not None}
    return PublicMcpControls(
        controls=controls, summary=PublicMcpSummary(**asdict(summarize(states)))
    )


@router.get("", response_model=ApiResponse[PublicMcpControls])
async def get_public_mcp_controls(*, db: Db) -> ApiResponse[PublicMcpControls]:
    """The ``all`` switch and each domain, with state, change time and actor."""
    return ApiResponse(data=await _read(db))


@router.get("/summary", response_model=ApiResponse[PublicMcpSummary])
async def get_public_mcp_summary(*, db: Db) -> ApiResponse[PublicMcpSummary]:
    """How many of the seven domains are disabled and whether ``all`` is."""
    return ApiResponse(data=(await _read(db)).summary)


@router.put("/{domain}", response_model=ApiResponse[PublicMcpControl])
async def set_public_mcp_control(
    domain: str, body: PublicMcpControlWrite = Body(...), *, db: Db
) -> ApiResponse[PublicMcpControl]:
    """Set one switch, recording the owner as the actor."""
    if verified_dashboard_session.get() is None:
        raise _refuse(401, "owner_dashboard_session_required")
    try:
        check_domain(domain)
    except UnknownDomain:
        raise _refuse(404, "unknown_domain") from None
    pool = _pool(db)
    try:
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    WRITE_SQL.format(domain="$1", state="$2", actor="$3"),
                    domain,
                    body.state,
                    ACTOR_DASHBOARD,
                )
                if row is None:  # already in that state: report the real last change
                    row = await connection.fetchrow(READ_ONE_SQL.format(domain="$1"), domain)
    except Exception:
        logger.warning("public MCP controls: write failed for %s", domain)
        raise _refuse(503, "public_mcp_controls_unavailable") from None
    return ApiResponse(data=_control(domain, row))
