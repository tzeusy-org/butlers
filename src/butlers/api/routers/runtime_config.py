"""Runtime config API endpoints for reading and patching per-butler operational config.

GET  /api/butlers/{name}/runtime-config — read effective runtime config from DB
PATCH /api/butlers/{name}/runtime-config — partial update of runtime config fields
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_db_manager, get_mcp_manager
from butlers.config import ConfigError, load_config
from butlers.core.runtime_config import resolve_effective_core_groups

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["runtime-config"])
_DEFAULT_ROSTER_DIR = Path(__file__).resolve().parents[4] / "roster"

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
        "delegation",
        "domain_events",
        "fleet_cases",
        "graph",
    }
)

# Fields that require a daemon restart to take effect.
COLD_FIELDS: frozenset[str] = frozenset(
    {"core_groups", "core_groups_narrowing_reason", "max_concurrent", "max_queued"}
)

# Field tier map included in GET responses.
FIELD_TIERS: dict[str, str] = {
    "core_groups": "cold",
    "core_groups_narrowing_reason": "cold",
    "catalog_read_sensitivity": "hot",
    "max_concurrent": "cold",
    "max_queued": "cold",
    "tool_exposure_policy": "hot",
}

# Closed set of accepted tool_exposure_policy values.
TOOL_EXPOSURE_POLICIES: frozenset[str] = frozenset({"eager_filtered", "auto"})


class RuntimeConfigResponse(BaseModel):
    """Response model for GET /api/butlers/{name}/runtime-config."""

    butler_name: str
    core_groups: list[str] | None = None
    declared_core_groups: list[str] | None = None
    effective_core_groups: list[str] | None = None
    core_groups_source: str = "git"
    core_groups_narrowing_reason: str | None = None
    declared_tool_names: list[str] | None = None
    effective_tool_names: list[str] | None = None
    tool_declaration_complete: bool | None = None
    tool_snapshot_status: Literal["available", "unavailable"] = "unavailable"
    catalog_read_sensitivity: str = "normal"
    max_concurrent: int = 3
    max_queued: int = 10
    tool_exposure_policy: str = "eager_filtered"
    seeded_at: str | None = None
    updated_at: str | None = None
    field_tiers: dict[str, str] = FIELD_TIERS


class RuntimeConfigPatch(BaseModel):
    """Request model for PATCH /api/butlers/{name}/runtime-config."""

    model_config = ConfigDict(extra="forbid")

    core_groups: list[str] | None = None
    core_groups_narrowing_reason: str | None = None
    catalog_read_sensitivity: str | None = None
    max_concurrent: int | None = None
    max_queued: int | None = None
    tool_exposure_policy: str | None = None

    @field_validator("catalog_read_sensitivity")
    @classmethod
    def validate_catalog_read_sensitivity(cls, value: str | None) -> str | None:
        if value is not None and value not in {"normal", "internal", "confidential"}:
            raise ValueError("catalog_read_sensitivity must be normal, internal, or confidential")
        return value

    @field_validator("tool_exposure_policy")
    @classmethod
    def validate_tool_exposure_policy(cls, value: str | None) -> str | None:
        if value is not None and value not in TOOL_EXPOSURE_POLICIES:
            raise ValueError("tool_exposure_policy must be eager_filtered or auto")
        return value

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

    @field_validator("core_groups_narrowing_reason")
    @classmethod
    def validate_narrowing_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("core_groups_narrowing_reason must not be blank")
        if len(value) > 500:
            raise ValueError("core_groups_narrowing_reason must be at most 500 characters")
        return value

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


def _get_roster_dir() -> Path:
    return _DEFAULT_ROSTER_DIR


def _get_mcp_client_manager() -> MCPClientManager:
    return get_mcp_manager()


async def _tool_surface_snapshot(
    mcp_manager: MCPClientManager | None,
    name: str,
) -> dict[str, Any]:
    """Read the daemon's content-blind registration snapshot best-effort."""
    unavailable = {"tool_snapshot_status": "unavailable"}
    if mcp_manager is None:
        return unavailable
    try:
        client = await asyncio.wait_for(mcp_manager.get_client(name), timeout=5.0)
        result = await asyncio.wait_for(client.call_tool("status", {}), timeout=5.0)
        if not result.content or not hasattr(result.content[0], "text"):
            return unavailable
        payload = json.loads(result.content[0].text)
        surface = payload.get("tool_surface")
        if not isinstance(surface, dict):
            return unavailable
        declared = surface.get("declared_names")
        effective = surface.get("effective_names")
        complete = surface.get("declaration_complete")
        if not isinstance(declared, list) or not all(isinstance(name, str) for name in declared):
            return unavailable
        if not isinstance(effective, list) or not all(isinstance(name, str) for name in effective):
            return unavailable
        return {
            "declared_tool_names": declared,
            "effective_tool_names": effective,
            "tool_declaration_complete": complete if isinstance(complete, bool) else None,
            "tool_snapshot_status": "available",
        }
    except Exception:
        logger.warning("Tool-surface snapshot unavailable for butler=%s", name, exc_info=True)
        return unavailable


def _declared_core_groups(roster_dir: Path, name: str) -> tuple[str, ...] | None:
    try:
        return load_config(roster_dir / name).runtime_seed.core_groups
    except ConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Invalid Git config for butler '{name}'",
        ) from exc


def _row_to_response(
    row: Any,
    *,
    declared_core_groups: tuple[str, ...] | None,
) -> RuntimeConfigResponse:
    """Convert an asyncpg Record to a RuntimeConfigResponse."""
    core_groups = list(row["core_groups"]) if row["core_groups"] is not None else None
    try:
        catalog_read_sensitivity = row["catalog_read_sensitivity"]
    except (KeyError, IndexError):
        # Legacy/partial rows have no evidence of elevated read authority.
        catalog_read_sensitivity = "normal"
    try:
        tool_exposure_policy = row["tool_exposure_policy"]
    except (KeyError, IndexError):
        # Legacy/partial rows preserve the conservative eager behavior.
        tool_exposure_policy = "eager_filtered"
    try:
        narrowing_reason = row["core_groups_narrowing_reason"]
    except (KeyError, IndexError):
        narrowing_reason = None
    runtime_groups = None if core_groups is None else tuple(core_groups)
    resolution = resolve_effective_core_groups(
        declared_core_groups,
        runtime_groups,
        narrowing_reason=narrowing_reason,
    )

    return RuntimeConfigResponse(
        butler_name=row["butler_name"],
        core_groups=core_groups,
        declared_core_groups=(None if declared_core_groups is None else list(declared_core_groups)),
        effective_core_groups=(
            None if resolution.effective is None else list(resolution.effective)
        ),
        core_groups_source=resolution.source,
        core_groups_narrowing_reason=narrowing_reason,
        catalog_read_sensitivity=catalog_read_sensitivity,
        max_concurrent=row["max_concurrent"],
        max_queued=row["max_queued"],
        tool_exposure_policy=tool_exposure_policy,
        seeded_at=str(row["seeded_at"]) if row["seeded_at"] else None,
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


@router.get("/{name}/runtime-config")
async def get_runtime_config(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
    roster_dir: Path = Depends(_get_roster_dir),
    mcp_manager: MCPClientManager = Depends(_get_mcp_client_manager),
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

    response = _row_to_response(
        row,
        declared_core_groups=_declared_core_groups(roster_dir, name),
    )
    return response.model_copy(update=await _tool_surface_snapshot(mcp_manager, name))


class PatchResponse(BaseModel):
    """Response model for PATCH /api/butlers/{name}/runtime-config."""

    config: RuntimeConfigResponse
    restart_required: list[str] = []


@router.patch("/{name}/runtime-config")
async def patch_runtime_config(
    name: str,
    request: Request,
    patch: RuntimeConfigPatch,
    db: DatabaseManager = Depends(_get_db_manager),
    roster_dir: Path = Depends(_get_roster_dir),
) -> PatchResponse:
    """Partially update the runtime config for a butler."""
    try:
        pool = db.pool(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Butler '{name}' not found")

    current_row = await pool.fetchrow("SELECT * FROM runtime_config LIMIT 1")
    if current_row is None:
        raise HTTPException(
            status_code=404,
            detail=f"No runtime_config row found for butler '{name}'",
        )
    declared_groups = _declared_core_groups(roster_dir, name)

    # Build SET clauses from supplied patch fields.
    updates: dict[str, Any] = {}
    if "core_groups" in patch.model_fields_set:
        updates["core_groups"] = patch.core_groups
    if "core_groups_narrowing_reason" in patch.model_fields_set:
        updates["core_groups_narrowing_reason"] = patch.core_groups_narrowing_reason
        # Clearing the reason revokes the runtime override immediately. Do not
        # leave a stale subset in the row until some future daemon restart.
        if (
            patch.core_groups_narrowing_reason is None
            and "core_groups" not in patch.model_fields_set
        ):
            updates["core_groups"] = None if declared_groups is None else list(declared_groups)
    if patch.catalog_read_sensitivity is not None:
        updates["catalog_read_sensitivity"] = patch.catalog_read_sensitivity
    if patch.max_concurrent is not None:
        updates["max_concurrent"] = patch.max_concurrent
    if patch.max_queued is not None:
        updates["max_queued"] = patch.max_queued
    if patch.tool_exposure_policy is not None:
        updates["tool_exposure_policy"] = patch.tool_exposure_policy

    current_groups = (
        tuple(current_row["core_groups"]) if current_row["core_groups"] is not None else None
    )
    try:
        current_reason = current_row["core_groups_narrowing_reason"]
    except (KeyError, IndexError):
        current_reason = None
    target_groups_raw = updates.get("core_groups", current_groups)
    target_groups = None if target_groups_raw is None else tuple(target_groups_raw)
    target_reason = updates.get("core_groups_narrowing_reason", current_reason)
    authority = resolve_effective_core_groups(
        declared_groups,
        target_groups,
        narrowing_reason=target_reason,
    )
    authority_fields_changed = bool(
        {"core_groups", "core_groups_narrowing_reason"} & patch.model_fields_set
    )
    if authority_fields_changed:
        if target_groups != declared_groups and authority.source != "runtime_narrowing":
            raise HTTPException(
                status_code=422,
                detail=(
                    "core_groups may only be a strict subset of Git-declared groups and requires "
                    "a non-empty core_groups_narrowing_reason"
                ),
            )
        if target_groups == declared_groups and target_reason is not None:
            raise HTTPException(
                status_code=422,
                detail="core_groups_narrowing_reason is only valid for a strict runtime narrowing",
            )

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

    response = PatchResponse(
        config=_row_to_response(row, declared_core_groups=declared_groups),
        restart_required=restart_required,
    )

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler=name,
        operation="runtime_config_patch",
        method="PATCH",
        path=f"/api/butlers/{name}/runtime-config",
        path_params={"butler_name": name},
        body=updates if updates else None,
        response_status=200,
        request=request,
    )

    return response
