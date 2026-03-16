"""Provider configuration CRUD and connectivity-test endpoints.

Provides ``router`` at ``/api/settings/providers``:

- ``GET    /api/settings/providers``                              — list all configured providers
- ``POST   /api/settings/providers``                              — register a new provider
- ``PUT    /api/settings/providers/{provider_type}``              — update provider config
- ``DELETE /api/settings/providers/{provider_type}``              — remove provider
- ``POST   /api/settings/providers/{provider_type}/test-connectivity`` — probe base URL
- ``GET    /api/settings/providers/ollama/models``                — discover available Ollama models
- ``POST   /api/settings/providers/ollama/import``                — bulk-import models into catalog

All operations target ``shared.provider_config`` via the shared credential pool.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings/providers", tags=["provider-settings"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    """A single provider configuration entry."""

    provider_type: str
    display_name: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False


class ProviderConfigCreate(BaseModel):
    """Request body for registering a new provider."""

    provider_type: str
    display_name: str
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False


class ProviderConfigUpdate(BaseModel):
    """Request body for updating a provider (all fields optional)."""

    display_name: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class ConnectivityResult(BaseModel):
    """Response from the test-connectivity endpoint."""

    success: bool
    provider_type: str
    url: str | None = None
    status_code: int | None = None
    error: str | None = None
    latency_ms: int = 0


class OllamaDiscoveredModel(BaseModel):
    """A model discovered from the Ollama /api/tags endpoint."""

    name: str
    size: int | None = None
    modified_at: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    already_in_catalog: bool = False


class OllamaImportItem(BaseModel):
    """A single model selection for the Ollama import request."""

    name: str
    alias: str
    complexity_tier: str = "medium"


class OllamaImportRequest(BaseModel):
    """Request body for bulk-importing Ollama models into the catalog."""

    models: list[OllamaImportItem]


class OllamaImportResult(BaseModel):
    """Result of a single model import operation."""

    alias: str
    name: str
    created: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_config(raw_config: Any) -> dict[str, Any]:
    """Parse a JSONB config field from a database record."""
    if isinstance(raw_config, str):
        try:
            return json.loads(raw_config)
        except (json.JSONDecodeError, TypeError):
            return {}
    elif isinstance(raw_config, dict):
        return raw_config
    return {}


def _row_to_provider(row: Any) -> ProviderConfig:
    """Convert an asyncpg Record to a ProviderConfig."""
    return ProviderConfig(
        provider_type=row["provider_type"],
        display_name=row["display_name"],
        config=_parse_config(row["config"]),
        enabled=bool(row["enabled"]),
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


def _probe_url_for_provider(provider_type: str, config: dict[str, Any]) -> str | None:
    """Derive the URL to probe for a given provider type.

    For 'ollama', reads ``config['base_url']`` and appends ``/api/version``.
    Returns None if the config is insufficient to form a URL.
    """
    if provider_type == "ollama":
        base_url = config.get("base_url", "").rstrip("/")
        if base_url:
            return f"{base_url}/api/version"
    return None


# ---------------------------------------------------------------------------
# GET /api/settings/providers — list all configured providers
# ---------------------------------------------------------------------------


@router.get("", response_model=ApiResponse[list[ProviderConfig]])
async def list_providers(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[ProviderConfig]]:
    """Return all provider configurations ordered by provider_type."""
    pool = _shared_pool(db)
    rows = await pool.fetch(
        """
        SELECT provider_type, display_name, config, enabled
        FROM shared.provider_config
        ORDER BY provider_type ASC
        """
    )
    providers = [_row_to_provider(row) for row in rows]
    return ApiResponse[list[ProviderConfig]](data=providers)


# ---------------------------------------------------------------------------
# POST /api/settings/providers — register a new provider
# ---------------------------------------------------------------------------


@router.post("", response_model=ApiResponse[ProviderConfig], status_code=201)
async def create_provider(
    body: ProviderConfigCreate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ProviderConfig]:
    """Register a new provider configuration. Returns 409 on duplicate provider_type."""
    pool = _shared_pool(db)

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO shared.provider_config
                (provider_type, display_name, config, enabled)
            VALUES ($1, $2, $3::jsonb, $4)
            RETURNING provider_type, display_name, config, enabled
            """,
            body.provider_type,
            body.display_name,
            json.dumps(body.config),
            body.enabled,
        )
    except asyncpg.UniqueViolationError:
        raise HTTPException(
            status_code=409,
            detail=f"Provider '{body.provider_type}' already exists",
        )
    except Exception as exc:
        logger.error("Failed to create provider config: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to create provider config")

    if row is None:
        raise HTTPException(status_code=500, detail="Insert returned no row")

    return ApiResponse[ProviderConfig](data=_row_to_provider(row))


# ---------------------------------------------------------------------------
# PUT /api/settings/providers/{provider_type} — update provider config
# ---------------------------------------------------------------------------


@router.put("/{provider_type}", response_model=ApiResponse[ProviderConfig])
async def update_provider(
    provider_type: str,
    body: ProviderConfigUpdate,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ProviderConfig]:
    """Update a provider configuration. Only provided fields are changed."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=422, detail="No fields provided to update")

    pool = _shared_pool(db)

    set_parts: list[str] = []
    params: list[Any] = []
    idx = 1

    for field, value in updates.items():
        if field == "config":
            set_parts.append(f"config = ${idx}::jsonb")
            params.append(json.dumps(value))
        else:
            set_parts.append(f"{field} = ${idx}")
            params.append(value)
        idx += 1

    set_parts.append("updated_at = now()")
    params.append(provider_type)

    sql = (
        f"UPDATE shared.provider_config SET {', '.join(set_parts)} "
        f"WHERE provider_type = ${idx} "
        "RETURNING provider_type, display_name, config, enabled"
    )

    try:
        row = await pool.fetchrow(sql, *params)
    except Exception as exc:
        logger.error("Failed to update provider config '%s': %s", provider_type, exc)
        raise HTTPException(status_code=500, detail="Failed to update provider config")

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider not found: {provider_type}",
        )

    return ApiResponse[ProviderConfig](data=_row_to_provider(row))


# ---------------------------------------------------------------------------
# DELETE /api/settings/providers/{provider_type} — remove provider
# ---------------------------------------------------------------------------


@router.delete("/{provider_type}", response_model=ApiResponse[dict])
async def delete_provider(
    provider_type: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Remove a provider configuration by provider_type."""
    pool = _shared_pool(db)
    result = await pool.execute(
        "DELETE FROM shared.provider_config WHERE provider_type = $1",
        provider_type,
    )
    deleted = int(result.split()[-1]) if result else 0
    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"Provider not found: {provider_type}",
        )
    return ApiResponse[dict](data={"deleted": True, "provider_type": provider_type})


# ---------------------------------------------------------------------------
# POST /api/settings/providers/{provider_type}/test-connectivity
# ---------------------------------------------------------------------------


@router.post(
    "/{provider_type}/test-connectivity",
    response_model=ApiResponse[ConnectivityResult],
)
async def test_connectivity(
    provider_type: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[ConnectivityResult]:
    """Probe the provider's configured base URL and return success/error with latency.

    The probe is provider-type-aware:
    - For 'ollama': GETs ``<base_url>/api/version``.

    Returns a structured result even on failure (no HTTP 5xx unless the DB
    itself is unavailable).
    """
    pool = _shared_pool(db)
    row = await pool.fetchrow(
        "SELECT config FROM shared.provider_config WHERE provider_type = $1",
        provider_type,
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider not found: {provider_type}",
        )

    config: dict[str, Any] = _parse_config(row["config"])

    probe_url = _probe_url_for_provider(provider_type, config)
    if probe_url is None:
        return ApiResponse[ConnectivityResult](
            data=ConnectivityResult(
                success=False,
                provider_type=provider_type,
                error="No probe URL configured for this provider type",
            )
        )

    t0 = time.monotonic()
    result: ConnectivityResult
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(probe_url)
        success = resp.status_code < 400
        result = ConnectivityResult(
            success=success,
            provider_type=provider_type,
            url=probe_url,
            status_code=resp.status_code,
            error=None if success else f"HTTP {resp.status_code}",
        )
    except Exception as exc:
        result = ConnectivityResult(
            success=False,
            provider_type=provider_type,
            url=probe_url,
            error=str(exc),
        )
    result.latency_ms = int((time.monotonic() - t0) * 1000)
    return ApiResponse[ConnectivityResult](data=result)


# ---------------------------------------------------------------------------
# GET /api/settings/providers/ollama/models — discover available Ollama models
# ---------------------------------------------------------------------------


@router.get("/ollama/models", response_model=ApiResponse[list[OllamaDiscoveredModel]])
async def list_ollama_models(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[OllamaDiscoveredModel]]:
    """Discover models available on the configured Ollama instance.

    Queries Ollama's ``GET /api/tags`` endpoint using the ``base_url`` stored
    in ``shared.provider_config`` for ``provider_type='ollama'``.

    Each model in the response is enriched with ``already_in_catalog=True``
    when a matching entry already exists in ``shared.model_catalog``.

    Returns:
    - 404 if no Ollama provider is configured.
    - 503 if the Ollama provider is disabled or if the shared pool is unavailable.
    - 502 if the Ollama instance is unreachable.
    """
    pool = _shared_pool(db)

    row = await pool.fetchrow(
        "SELECT config, enabled FROM shared.provider_config WHERE provider_type = 'ollama'"
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No Ollama provider configured",
        )

    if not row["enabled"]:
        raise HTTPException(
            status_code=503,
            detail="Ollama provider is disabled",
        )

    config: dict[str, Any] = _parse_config(row["config"])
    base_url = config.get("base_url", "").rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=422,
            detail="Ollama provider has no base_url configured",
        )

    # Fetch available models from Ollama
    tags_url = f"{base_url}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(tags_url)
        resp.raise_for_status()
        tags_data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned HTTP {exc.response.status_code}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to reach Ollama at {base_url}: {exc}",
        )

    ollama_models: list[dict[str, Any]] = tags_data.get("models", [])

    # Fetch existing catalog entries to check already_in_catalog
    existing_rows = await pool.fetch(
        "SELECT model_id FROM shared.model_catalog WHERE runtime_type = 'ollama'"
    )
    existing_model_ids: set[str] = {r["model_id"] for r in existing_rows}

    discovered: list[OllamaDiscoveredModel] = []
    for m in ollama_models:
        name = m.get("name", "")
        details = m.get("details", {}) or {}
        discovered.append(
            OllamaDiscoveredModel(
                name=name,
                size=m.get("size"),
                modified_at=m.get("modified_at"),
                parameter_size=details.get("parameter_size"),
                quantization=details.get("quantization_level"),
                already_in_catalog=name in existing_model_ids,
            )
        )

    return ApiResponse[list[OllamaDiscoveredModel]](data=discovered)


# ---------------------------------------------------------------------------
# POST /api/settings/providers/ollama/import — bulk-import Ollama models
# ---------------------------------------------------------------------------


@router.post(
    "/ollama/import",
    response_model=ApiResponse[list[OllamaImportResult]],
    status_code=201,
)
async def import_ollama_models(
    body: OllamaImportRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[OllamaImportResult]]:
    """Bulk-import Ollama models into the shared model catalog.

    Creates catalog entries with ``runtime_type='ollama'`` for each item in
    the request.  Uses ``INSERT ... ON CONFLICT (alias) DO NOTHING`` to skip
    duplicates silently unless the alias is already taken by a *different*
    runtime type, in which case a 409 is returned.

    Returns 409 when any requested alias already exists in the catalog
    (regardless of runtime_type).
    """
    pool = _shared_pool(db)

    results: list[OllamaImportResult] = []
    for item in body.models:
        try:
            row = await pool.fetchrow(
                """
                INSERT INTO shared.model_catalog
                    (alias, runtime_type, model_id, extra_args, complexity_tier, enabled, priority)
                VALUES ($1, 'ollama', $2, '[]'::jsonb, $3, false, 10)
                ON CONFLICT (alias) DO NOTHING
                RETURNING alias
                """,
                item.alias,
                item.name,
                item.complexity_tier,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status_code=409,
                detail=f"Alias '{item.alias}' already exists in the catalog",
            )
        except Exception as exc:
            logger.error("Failed to import Ollama model '%s': %s", item.alias, exc)
            raise HTTPException(status_code=500, detail=f"Failed to import model: {item.alias}")

        created = row is not None
        results.append(OllamaImportResult(alias=item.alias, name=item.name, created=created))

    return ApiResponse[list[OllamaImportResult]](data=results)
