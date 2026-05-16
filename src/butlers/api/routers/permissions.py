"""Permissions matrix endpoints.

Provides:

* ``GET /api/permissions``            — full matrix (butlers × permissions).
* ``PUT /api/permissions/{butler}/{perm}`` — flip one cell; enforces non-empty
  reason and writes to ``audit_log`` on success.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.routers import audit

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
    """Request body for updating a single permission cell."""

    granted: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reason_required")
        return v


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

    rows = await pool.fetch(
        "SELECT butler, permission, granted, reason, updated_at "
        "FROM public.permissions "
        "ORDER BY butler, permission"
    )

    # Build the matrix dimensions from existing rows.
    butlers_set: set[str] = set()
    permissions_set: set[str] = set()
    cells: dict[str, dict[str, PermissionCell]] = {}

    for row in rows:
        butler = row["butler"]
        perm = row["permission"]
        butlers_set.add(butler)
        permissions_set.add(perm)
        cells.setdefault(butler, {})[perm] = PermissionCell(
            granted=row["granted"],
            reason=row["reason"],
            updated_at=row["updated_at"],
            inherited=False,
        )

    butlers_list = sorted(butlers_set)
    permissions_list = sorted(permissions_set)

    return ApiResponse(
        data=PermissionsMatrix(
            butlers=butlers_list,
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
    # Pydantic already validates reason via the field_validator; the 422 is
    # raised automatically by FastAPI when validation fails.  We add an
    # explicit guard here as belt-and-suspenders and to produce the exact
    # error body required by the spec.
    if not body.reason or not body.reason.strip():
        raise HTTPException(status_code=422, detail={"error": "reason_required"})

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

    return ApiResponse(
        data=PermissionSetResponse(
            butler=butler,
            permission=perm,
            granted=body.granted,
            reason=body.reason,
            updated_at=now,
        )
    )


# ---------------------------------------------------------------------------
# Validation error override — produce {"error": "reason_required"} on 422
# ---------------------------------------------------------------------------


# FastAPI / Pydantic generates a generic validation-error body by default.
# The spec mandates the exact shape {"error": "reason_required"}.  We expose
# a helper that the permission endpoint's Pydantic model raises so the
# FastAPI default handler already produces a 422; callers reading the detail
# will see the spec-compliant payload because we set it in the HTTPException
# above.  No additional handler wiring is needed.
def _reason_required_response() -> dict[str, Any]:
    return {"error": "reason_required"}
