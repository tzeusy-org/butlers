"""Runtime config API endpoints for reading and patching per-butler operational config.

GET  /api/butlers/{name}/runtime-config — read effective runtime config from DB
PATCH /api/butlers/{name}/runtime-config — partial update of runtime config fields
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["runtime-config"])

# Known core tool groups — PATCH rejects unknown group names to prevent typos.
KNOWN_CORE_GROUPS: frozenset[str] = frozenset(
    {
        "infra",
        "state",
        "scheduling",
        "sessions",
        "notifications",
        "media",
        "temporal",
        "module_mgmt",
        "switchboard_routing",
        "switchboard_backfill",
    }
)

# Fields that require a daemon restart to take effect.
COLD_FIELDS: frozenset[str] = frozenset({"core_groups", "max_concurrent", "max_queued"})

# Field tier map included in GET responses.
FIELD_TIERS: dict[str, str] = {
    "core_groups": "cold",
    "max_concurrent": "cold",
    "max_queued": "cold",
}


class RuntimeConfigResponse(BaseModel):
    """Response model for GET /api/butlers/{name}/runtime-config."""

    butler_name: str
    core_groups: list[str] | None = None
    max_concurrent: int = 3
    max_queued: int = 10
    seeded_at: str | None = None
    updated_at: str | None = None
    field_tiers: dict[str, str] = FIELD_TIERS


class RuntimeConfigPatch(BaseModel):
    """Request model for PATCH /api/butlers/{name}/runtime-config."""

    model_config = ConfigDict(extra="forbid")

    core_groups: list[str] | None = None
    max_concurrent: int | None = None
    max_queued: int | None = None

    @field_validator("core_groups")
    @classmethod
    def validate_core_groups(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        unknown = set(v) - KNOWN_CORE_GROUPS
        if unknown:
            raise ValueError(
                f"Unknown core_group(s): {', '.join(sorted(unknown))}. "
                f"Known groups: {', '.join(sorted(KNOWN_CORE_GROUPS))}"
            )
        return v

    @field_validator("max_concurrent")
    @classmethod
    def validate_max_concurrent(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("max_concurrent must be a positive integer")
        return v

    @field_validator("max_queued")
    @classmethod
    def validate_max_queued(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("max_queued must be a positive integer")
        return v

def _get_db_manager() -> DatabaseManager:
    return get_db_manager()


def _row_to_response(row: Any) -> RuntimeConfigResponse:
    """Convert an asyncpg Record to a RuntimeConfigResponse."""
    core_groups = list(row["core_groups"]) if row["core_groups"] is not None else None

    return RuntimeConfigResponse(
        butler_name=row["butler_name"],
        core_groups=core_groups,
        max_concurrent=row["max_concurrent"],
        max_queued=row["max_queued"],
        seeded_at=str(row["seeded_at"]) if row["seeded_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


@router.get("/{name}/runtime-config")
async def get_runtime_config(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> RuntimeConfigResponse:
    """Read the effective runtime config for a butler from the DB."""
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Butler '{name}' not found")

    row = await pool.fetchrow("SELECT * FROM runtime_config LIMIT 1")
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No runtime_config row found for butler '{name}'",
        )

    return _row_to_response(row)


class PatchResponse(BaseModel):
    """Response model for PATCH /api/butlers/{name}/runtime-config."""

    config: RuntimeConfigResponse
    restart_required: list[str] = []


@router.patch("/{name}/runtime-config")
async def patch_runtime_config(
    name: str,
    patch: RuntimeConfigPatch,
    db: DatabaseManager = Depends(_get_db_manager),
) -> PatchResponse:
    """Partially update the runtime config for a butler."""
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Butler '{name}' not found")

    # Build SET clauses from non-None patch fields
    updates: dict[str, Any] = {}
    if patch.core_groups is not None:
        updates["core_groups"] = patch.core_groups
    if patch.max_concurrent is not None:
        updates["max_concurrent"] = patch.max_concurrent
    if patch.max_queued is not None:
        updates["max_queued"] = patch.max_queued

    restart_required: list[str] = []
    if updates:
        # Identify cold fields that changed
        for field_name in updates:
            if field_name in COLD_FIELDS:
                restart_required.append(field_name)

        # Build dynamic UPDATE SQL
        set_clauses: list[str] = []
        params: list[Any] = []
        idx = 1
        for col, val in updates.items():
            set_clauses.append(f"{col} = ${idx}")
            params.append(val)
            idx += 1

        set_clauses.append(f"updated_at = ${idx}")
        params.append(datetime.now(UTC))

        sql = f"UPDATE runtime_config SET {', '.join(set_clauses)}"
        await pool.execute(sql, *params)

    # Read back the updated row
    row = await pool.fetchrow("SELECT * FROM runtime_config LIMIT 1")
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No runtime_config row found for butler '{name}'",
        )

    return PatchResponse(
        config=_row_to_response(row),
        restart_required=restart_required,
    )
