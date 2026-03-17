"""Model catalog CRUD and per-butler override endpoints.

Provides:

- ``catalog_router`` — catalog management at ``/api/settings/models``
- ``butler_model_router`` — per-butler override endpoints at
  ``/api/butlers/{name}/model-overrides`` and ``/api/butlers/{name}/resolve-model``

All reads query ``shared.model_catalog`` and ``shared.butler_model_overrides``
directly via the shared credential pool.  Writes mutate those tables directly
(the catalog is managed via the API, not via butler MCP tools).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse

logger = logging.getLogger(__name__)

catalog_router = APIRouter(prefix="/api/settings/models", tags=["model-catalog"])
butler_model_router = APIRouter(prefix="/api/butlers", tags=["butlers", "model-overrides"])

_COMPLEXITY_TIERS = ("trivial", "medium", "high", "extra_high", "discretion", "self_healing")


def _get_db_manager() -> DatabaseManager:
    """Dependency stub -- overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ModelCatalogEntry(BaseModel):
    """A single entry in the shared model catalog."""

    id: UUID
    alias: str
    runtime_type: str
    model_id: str
    extra_args: list[str] = Field(default_factory=list)
    complexity_tier: str
    enabled: bool = True
    priority: int = 0


class ModelCatalogCreate(BaseModel):
    """Request body for creating a catalog entry."""

    alias: str
    runtime_type: str
    model_id: str
    extra_args: list[str] = Field(default_factory=list)
    complexity_tier: str = "medium"
    enabled: bool = True
    priority: int = 0


class ModelCatalogUpdate(BaseModel):
    """Request body for updating a catalog entry (all fields optional)."""

    alias: str | None = None
    runtime_type: str | None = None
    model_id: str | None = None
    extra_args: list[str] | None = None
    complexity_tier: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class ButlerModelOverride(BaseModel):
    """A single per-butler model override joined with catalog alias."""

    id: UUID
    butler_name: str
    catalog_entry_id: UUID
    alias: str
    enabled: bool
    priority: int | None = None
    complexity_tier: str | None = None


class ButlerModelOverrideUpsert(BaseModel):
    """One item in a batch upsert request for butler model overrides."""

    catalog_entry_id: UUID
    enabled: bool = True
    priority: int | None = None
    complexity_tier: str | None = None


class ResolveModelResponse(BaseModel):
    """Response from the resolve-model preview endpoint."""

    butler_name: str
    complexity: str
    runtime_type: str | None = None
    model_id: str | None = None
    extra_args: list[str] = Field(default_factory=list)
    resolved: bool


class ModelTestResult(BaseModel):
    """Response from the model test endpoint."""

    success: bool
    reply: str | None = None
    error: str | None = None
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    """Read a mapping key with compatibility for asyncpg Record and plain dict."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _validate_complexity_tier(tier: str) -> None:
    """Raise HTTPException(422) if tier is not a valid complexity value."""
    if tier not in _COMPLEXITY_TIERS:
        valid = ", ".join(_COMPLEXITY_TIERS)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid complexity_tier '{tier}'. Must be one of: {valid}",
        )


def _coerce_extra_args(raw: Any) -> list[str]:
    """Safely coerce asyncpg JSONB result to list[str]."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(v) for v in parsed]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _row_to_catalog_entry(row: Any) -> ModelCatalogEntry:
    """Convert an asyncpg Record to a ModelCatalogEntry."""
    return ModelCatalogEntry(
        id=row["id"],
        alias=row["alias"],
        runtime_type=row["runtime_type"],
        model_id=row["model_id"],
        extra_args=_coerce_extra_args(_row_value(row, "extra_args")),
        complexity_tier=row["complexity_tier"],
        enabled=bool(_row_value(row, "enabled", True)),
        priority=int(_row_value(row, "priority", 0)),
    )


def _row_to_override(row: Any) -> ButlerModelOverride:
    """Convert an asyncpg Record to a ButlerModelOverride."""
    raw_priority = _row_value(row, "priority")
    priority = int(raw_priority) if raw_priority is not None else None
    return ButlerModelOverride(
        id=row["id"],
        butler_name=row["butler_name"],
        catalog_entry_id=row["catalog_entry_id"],
        alias=_row_value(row, "alias", ""),
        enabled=bool(_row_value(row, "enabled", True)),
        priority=priority,
        complexity_tier=_row_value(row, "complexity_tier"),
    )


def _shared_pool(db: DatabaseManager):
    """Return the shared credential pool, raising 503 if unavailable."""
    try:
        return db.credential_shared_pool()
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Shared database pool is not available",
        )


# ---------------------------------------------------------------------------
# GET /api/settings/models — list catalog entries
# ---------------------------------------------------------------------------


@catalog_router.get("", response_model=ApiResponse[list[ModelCatalogEntry]])
async def list_catalog_entries(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ModelCatalogEntry]]:
    """Return all catalog entries ordered by complexity_tier, priority, alias."""
    pool = _shared_pool(db)
    rows = await pool.fetch(
        """
        SELECT id, alias, runtime_type, model_id, extra_args,
               complexity_tier, enabled, priority
        FROM shared.model_catalog
        ORDER BY
            CASE complexity_tier
                WHEN 'trivial'     THEN 1
                WHEN 'medium'      THEN 2
                WHEN 'high'        THEN 3
                WHEN 'extra_high'  THEN 4
                WHEN 'discretion'  THEN 5
                ELSE 6
            END,
            priority DESC,
            alias ASC
        """
    )
    entries = [_row_to_catalog_entry(row) for row in rows]
    return ApiResponse[list[ModelCatalogEntry]](data=entries)


# ---------------------------------------------------------------------------
# POST /api/settings/models — create catalog entry
# ---------------------------------------------------------------------------


@catalog_router.post("", response_model=ApiResponse[ModelCatalogEntry], status_code=201)
async def create_catalog_entry(
    body: ModelCatalogCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelCatalogEntry]:
    """Create a new catalog entry. Returns 409 on duplicate alias."""
    _validate_complexity_tier(body.complexity_tier)
    pool = _shared_pool(db)

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO shared.model_catalog
                (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7)
            RETURNING id, alias, runtime_type, model_id, extra_args,
                      complexity_tier, enabled, priority
            """,
            body.alias,
            body.runtime_type,
            body.model_id,
            json.dumps(body.extra_args),
            body.complexity_tier,
            body.enabled,
            body.priority,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"Catalog entry with alias '{body.alias}' already exists",
        )
    except Exception as exc:
        logger.error("Failed to create catalog entry: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create catalog entry")

    if row is None:
        raise HTTPException(status_code=500, detail="Insert returned no row")

    return ApiResponse[ModelCatalogEntry](data=_row_to_catalog_entry(row))


# ---------------------------------------------------------------------------
# PUT /api/settings/models/{entry_id} — update catalog entry
# ---------------------------------------------------------------------------


@catalog_router.put("/{entry_id}", response_model=ApiResponse[ModelCatalogEntry])
async def update_catalog_entry(
    entry_id: UUID,
    body: ModelCatalogUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelCatalogEntry]:
    """Update a catalog entry by ID. Only provided fields are changed."""
    if body.complexity_tier is not None:
        _validate_complexity_tier(body.complexity_tier)

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided to update")

    pool = _shared_pool(db)

    # Build SET clause dynamically
    set_parts = []
    params: list[Any] = []
    idx = 1

    for field, value in updates.items():
        if field == "extra_args":
            set_parts.append(f"extra_args = ${idx}::jsonb")
            params.append(json.dumps(value))
        else:
            set_parts.append(f"{field} = ${idx}")
            params.append(value)
        idx += 1

    set_parts.append("updated_at = now()")
    params.append(entry_id)

    sql = (
        f"UPDATE shared.model_catalog SET {', '.join(set_parts)} "
        f"WHERE id = ${idx} "
        "RETURNING id, alias, runtime_type, model_id, extra_args, "
        "complexity_tier, enabled, priority"
    )

    try:
        row = await pool.fetchrow(sql, *params)
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"Catalog entry with alias '{updates.get('alias')}' already exists",
        )
    except Exception as exc:
        logger.error("Failed to update catalog entry %s: %s", entry_id, exc)
        raise HTTPException(status_code=500, detail="Failed to update catalog entry")

    if row is None:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    return ApiResponse[ModelCatalogEntry](data=_row_to_catalog_entry(row))


# ---------------------------------------------------------------------------
# DELETE /api/settings/models/{entry_id} — delete catalog entry with cascade
# ---------------------------------------------------------------------------


@catalog_router.delete("/{entry_id}", response_model=ApiResponse[dict])
async def delete_catalog_entry(
    entry_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Delete a catalog entry by ID. Cascades to butler_model_overrides."""
    pool = _shared_pool(db)
    result = await pool.execute(
        "DELETE FROM shared.model_catalog WHERE id = $1",
        entry_id,
    )
    # asyncpg returns "DELETE N" where N is the row count
    deleted = int(result.split()[-1]) if result else 0
    if deleted == 0:
        raise HTTPException(status_code=404, detail=f"Catalog entry not found: {entry_id}")

    return ApiResponse[dict](data={"deleted": True, "id": str(entry_id)})


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/model-overrides — list overrides
# ---------------------------------------------------------------------------


@butler_model_router.get(
    "/{name}/model-overrides",
    response_model=ApiResponse[list[ButlerModelOverride]],
)
async def list_butler_model_overrides(
    name: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ButlerModelOverride]]:
    """Return all model overrides for a butler, joined with catalog alias."""
    pool = _shared_pool(db)
    rows = await pool.fetch(
        """
        SELECT bmo.id, bmo.butler_name, bmo.catalog_entry_id,
               mc.alias, bmo.enabled, bmo.priority, bmo.complexity_tier
        FROM shared.butler_model_overrides bmo
        JOIN shared.model_catalog mc ON mc.id = bmo.catalog_entry_id
        WHERE bmo.butler_name = $1
        ORDER BY mc.alias ASC
        """,
        name,
    )
    overrides = [_row_to_override(row) for row in rows]
    return ApiResponse[list[ButlerModelOverride]](data=overrides)


# ---------------------------------------------------------------------------
# PUT /api/butlers/{name}/model-overrides — batch upsert overrides
# ---------------------------------------------------------------------------


@butler_model_router.put(
    "/{name}/model-overrides",
    response_model=ApiResponse[list[ButlerModelOverride]],
)
async def upsert_butler_model_overrides(
    name: str,
    body: list[ButlerModelOverrideUpsert],
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ButlerModelOverride]]:
    """Batch upsert model overrides for a butler.

    Each item is upserted by (butler_name, catalog_entry_id).
    Returns the full list of updated override rows.
    """
    if not body:
        raise HTTPException(status_code=422, detail="Request body must contain at least one item")

    for item in body:
        if item.complexity_tier is not None:
            _validate_complexity_tier(item.complexity_tier)

    pool = _shared_pool(db)

    upserted_ids: list[UUID] = []
    async with pool.acquire() as conn:
        async with conn.transaction():
            for item in body:
                row = await conn.fetchrow(
                    """
                    INSERT INTO shared.butler_model_overrides
                        (butler_name, catalog_entry_id, enabled, priority, complexity_tier)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (butler_name, catalog_entry_id) DO UPDATE
                        SET enabled = EXCLUDED.enabled,
                            priority = EXCLUDED.priority,
                            complexity_tier = EXCLUDED.complexity_tier
                    RETURNING id
                    """,
                    name,
                    item.catalog_entry_id,
                    item.enabled,
                    item.priority,
                    item.complexity_tier,
                )
                if row is not None:
                    upserted_ids.append(row["id"])

    if not upserted_ids:
        return ApiResponse[list[ButlerModelOverride]](data=[])

    rows = await pool.fetch(
        """
        SELECT bmo.id, bmo.butler_name, bmo.catalog_entry_id,
               mc.alias, bmo.enabled, bmo.priority, bmo.complexity_tier
        FROM shared.butler_model_overrides bmo
        JOIN shared.model_catalog mc ON mc.id = bmo.catalog_entry_id
        WHERE bmo.id = ANY($1::uuid[])
        ORDER BY mc.alias ASC
        """,
        upserted_ids,
    )
    return ApiResponse[list[ButlerModelOverride]](data=[_row_to_override(r) for r in rows])


# ---------------------------------------------------------------------------
# DELETE /api/butlers/{name}/model-overrides/{override_id}
# ---------------------------------------------------------------------------


@butler_model_router.delete(
    "/{name}/model-overrides/{override_id}",
    response_model=ApiResponse[dict],
)
async def delete_butler_model_override(
    name: str,
    override_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Remove a single butler model override by ID."""
    pool = _shared_pool(db)
    result = await pool.execute(
        "DELETE FROM shared.butler_model_overrides WHERE id = $1 AND butler_name = $2",
        override_id,
        name,
    )
    deleted = int(result.split()[-1]) if result else 0
    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Override not found: {override_id} for butler '{name}'",
        )
    return ApiResponse[dict](data={"deleted": True, "id": str(override_id)})


# ---------------------------------------------------------------------------
# GET /api/butlers/{name}/resolve-model?complexity=X — preview endpoint
# ---------------------------------------------------------------------------


@butler_model_router.get(
    "/{name}/resolve-model",
    response_model=ApiResponse[ResolveModelResponse],
)
async def resolve_model_preview(
    name: str,
    complexity: str = Query(default="medium", description="Complexity tier to resolve"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ResolveModelResponse]:
    """Preview which model would be selected for a butler + complexity tier.

    Executes the same resolution logic as the spawner: LEFT JOIN the catalog
    with per-butler overrides, COALESCE enabled/priority/tier, return the
    highest-priority matching row.
    """
    _validate_complexity_tier(complexity)
    pool = _shared_pool(db)

    row = await pool.fetchrow(
        """
        SELECT
            mc.runtime_type,
            mc.model_id,
            mc.extra_args
        FROM shared.model_catalog mc
        LEFT JOIN shared.butler_model_overrides bmo
            ON bmo.catalog_entry_id = mc.id
            AND bmo.butler_name = $1
        WHERE
            COALESCE(bmo.enabled, mc.enabled) = true
            AND COALESCE(bmo.complexity_tier, mc.complexity_tier) = $2
        ORDER BY
            COALESCE(bmo.priority, mc.priority) DESC,
            mc.created_at ASC
        LIMIT 1
        """,
        name,
        complexity,
    )

    if row is None:
        return ApiResponse[ResolveModelResponse](
            data=ResolveModelResponse(
                butler_name=name,
                complexity=complexity,
                resolved=False,
            )
        )

    return ApiResponse[ResolveModelResponse](
        data=ResolveModelResponse(
            butler_name=name,
            complexity=complexity,
            runtime_type=row["runtime_type"],
            model_id=row["model_id"],
            extra_args=_coerce_extra_args(_row_value(row, "extra_args")),
            resolved=True,
        )
    )


# ---------------------------------------------------------------------------
# POST /api/settings/models/{entry_id}/test — test a model config
# ---------------------------------------------------------------------------


@catalog_router.post(
    "/{entry_id}/test",
    response_model=ApiResponse[ModelTestResult],
)
async def test_catalog_entry(
    entry_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ModelTestResult]:
    """Spawn a minimal LLM session to verify the model config works.

    Sends a simple prompt with no MCP servers and returns the reply.
    """
    pool = _shared_pool(db)
    row = await pool.fetchrow(
        """
        SELECT runtime_type, model_id, extra_args
        FROM shared.model_catalog
        WHERE id = $1
        """,
        entry_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Catalog entry not found")

    runtime_type = row["runtime_type"]
    model_id = row["model_id"]
    extra_args = _coerce_extra_args(_row_value(row, "extra_args"))

    try:
        from butlers.core.runtimes.base import get_adapter
        from butlers.core.spawner import resolve_provider_config

        adapter_cls = get_adapter(runtime_type)
        provider_config = await resolve_provider_config(pool, model_id)
        try:
            adapter = adapter_cls(provider_config=provider_config)
        except TypeError:
            adapter = adapter_cls()
    except ValueError as exc:
        return ApiResponse[ModelTestResult](data=ModelTestResult(success=False, error=str(exc)))

    import os

    t0 = time.monotonic()
    try:
        result_text, _, _ = await adapter.invoke(
            prompt="Reply with exactly: OK",
            system_prompt="You are a test assistant. Reply concisely.",
            mcp_servers={},
            env=dict(os.environ),
            max_turns=1,
            model=model_id,
            runtime_args=extra_args or None,
            timeout=30,
        )
        duration_ms = int((time.monotonic() - t0) * 1000)
        if not result_text or not result_text.strip():
            # Surface process-level diagnostics for subprocess-based adapters
            proc_info = getattr(adapter, "last_process_info", None)
            stderr_hint = ""
            if isinstance(proc_info, dict):
                stderr_raw = proc_info.get("stderr", "")
                exit_code = proc_info.get("exit_code")
                if stderr_raw:
                    stderr_hint = f" stderr: {stderr_raw[:1000]}"
                if exit_code and exit_code != 0:
                    stderr_hint = f" (exit code {exit_code}){stderr_hint}"
            error_msg = f"Model returned an empty response{stderr_hint}"
            logger.warning(
                "Model test empty response for %s/%s: %s", runtime_type, model_id, error_msg
            )
            return ApiResponse[ModelTestResult](
                data=ModelTestResult(
                    success=False,
                    error=error_msg,
                    duration_ms=duration_ms,
                )
            )
        return ApiResponse[ModelTestResult](
            data=ModelTestResult(
                success=True,
                reply=result_text.strip(),
                duration_ms=duration_ms,
            )
        )
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        proc_info = getattr(adapter, "last_process_info", None)
        stderr_hint = ""
        if isinstance(proc_info, dict):
            stderr_raw = proc_info.get("stderr", "")
            if stderr_raw:
                stderr_hint = f" | stderr: {stderr_raw[:1000]}"
        logger.warning(
            "Model test failed for %s/%s: %s%s", runtime_type, model_id, exc, stderr_hint
        )
        return ApiResponse[ModelTestResult](
            data=ModelTestResult(
                success=False,
                error=f"{exc}{stderr_hint}",
                duration_ms=duration_ms,
            )
        )
