"""Permissions matrix endpoints.

Provides:

* ``GET /api/permissions``            — full matrix (butlers × permissions).
* ``PUT /api/permissions/{butler}/{perm}`` — flip one cell; enforces non-empty
  reason and writes to ``audit_log`` on success.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers import audit
from butlers.api.routers.webhooks import dispatch_event
from butlers.api.security import validate_no_secrets
from butlers.core.permissions import ENFORCED_PERMISSIONS, PERMISSION_DEFAULT_GRANTED

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/permissions", tags=["permissions"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PermissionCell(BaseModel):
    """One cell in the permissions matrix."""

    granted: bool
    reason: str | None = None
    updated_at: datetime | None = None
    inherited: bool = False


class PermissionsMatrix(BaseModel):
    """Full permissions × butlers matrix.

    ``cells`` maps ``butler_name → {permission_name → PermissionCell}``.
    """

    butlers: list[str]
    permissions: list[str]
    cells: dict[str, dict[str, PermissionCell]]


class PermissionUpdate(BaseModel):
    """Request body for updating a single permission cell.

    ``reason`` defaults to an empty string so that a missing field flows into
    the route handler's ``reason_required`` guard (which returns the
    spec-mandated ``{"error": "reason_required"}`` body) rather than tripping a
    generic Pydantic "field required" validation error.
    """

    granted: bool
    reason: str = ""


# ---------------------------------------------------------------------------
# GET /api/permissions
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[PermissionsMatrix])
async def get_permissions_matrix(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[PermissionsMatrix]:
    """Return the full permissions matrix across all butlers and permissions."""
    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    # All registered butlers (the "rows" of the matrix).
    try:
        registry_rows = await pool.fetch("SELECT name FROM butler_registry ORDER BY name")
        butlers_from_registry: list[str] = [r["name"] for r in registry_rows]
    except Exception:
        logger.warning("Failed to query butler_registry; falling back to perm-table butlers only")
        butlers_from_registry = []

    # Explicit permission rows.
    perm_rows = await pool.fetch(
        "SELECT butler, permission, granted, reason, updated_at "
        "FROM public.permissions "
        "ORDER BY butler, permission"
    )

    # Index explicit rows; also collect any butler names present only in perm rows.
    explicit: dict[tuple[str, str], PermissionCell] = {}
    extra_butlers: set[str] = set()
    for row in perm_rows:
        butler = row["butler"]
        perm = row["permission"]
        extra_butlers.add(butler)
        explicit[(butler, perm)] = PermissionCell(
            granted=row["granted"],
            reason=row["reason"],
            updated_at=row["updated_at"],
            inherited=False,
        )

    all_butlers = sorted(set(butlers_from_registry) | extra_butlers)
    permissions_list = sorted(ENFORCED_PERMISSIONS)

    # Build DENSE matrix: every (butler × enforced-perm) cell is populated.
    cells: dict[str, dict[str, PermissionCell]] = {}
    for butler in all_butlers:
        cells[butler] = {}
        for perm in permissions_list:
            if (butler, perm) in explicit:
                cells[butler][perm] = explicit[(butler, perm)]
            else:
                cells[butler][perm] = PermissionCell(
                    granted=PERMISSION_DEFAULT_GRANTED,
                    inherited=True,
                )

    return ApiResponse(
        data=PermissionsMatrix(
            butlers=all_butlers,
            permissions=permissions_list,
            cells=cells,
        )
    )


# ---------------------------------------------------------------------------
# PUT /api/permissions/{butler}/{perm}
# ---------------------------------------------------------------------------


class PermissionSetResponse(BaseModel):
    """Response body for a successful permission update."""

    butler: str
    permission: str
    granted: bool
    reason: str
    updated_at: datetime


@router.put("/{butler}/{perm}", response_model=ApiResponse[PermissionSetResponse])
async def set_permission(
    butler: str,
    perm: str,
    body: PermissionUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[PermissionSetResponse]:
    """Flip one permission cell.

    Returns HTTP 422 with ``{"error": "reason_required"}`` when ``reason``
    is empty or whitespace-only.  On success calls ``audit.append``.
    """
    # Enforce the non-empty reason here (not via a Pydantic validator) so the
    # spec-mandated {"error": "reason_required"} body is returned for empty,
    # missing, or whitespace-only reasons.
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=422, detail={"error": "reason_required"})

    if not validate_no_secrets(body.reason):
        raise HTTPException(status_code=422, detail={"error": "reason_contains_credential"})

    try:
        pool = db.pool("switchboard")
    except KeyError:
        raise HTTPException(status_code=503, detail="Switchboard database is not available")

    now = datetime.now(UTC)

    await pool.execute(
        "INSERT INTO public.permissions (butler, permission, granted, reason, updated_at) "
        "VALUES ($1, $2, $3, $4, $5) "
        "ON CONFLICT (butler, permission) DO UPDATE "
        "SET granted = EXCLUDED.granted, "
        "    reason  = EXCLUDED.reason, "
        "    updated_at = EXCLUDED.updated_at",
        butler,
        perm,
        body.granted,
        body.reason,
        now,
    )

    await audit.append(pool, "owner", "permission.set", target=f"{butler}.{perm}", note=body.reason)
    dispatch_event(
        pool,
        "permission.set",
        {"target": f"{butler}.{perm}", "granted": body.granted},
    )

    return ApiResponse(
        data=PermissionSetResponse(
            butler=butler,
            permission=perm,
            granted=body.granted,
            reason=body.reason,
            updated_at=now,
        )
    )
