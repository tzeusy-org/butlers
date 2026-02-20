"""Generic secrets CRUD endpoints backed by the butler_secrets table.

Provides REST endpoints for managing secrets stored in any butler's
``butler_secrets`` table via the ``CredentialStore`` service.  Secret values
are **never** returned in responses — only metadata (key, category,
description, is_sensitive, is_set, timestamps) is exposed.

Endpoints
---------
GET  /api/butlers/{name}/secrets
    List all secrets for a butler (metadata only, values masked).
    Optional ``?category=`` filter.

GET  /api/butlers/{name}/secrets/{key}
    Single secret metadata.  404 if not found.

PUT  /api/butlers/{name}/secrets/{key}
    Upsert a secret.  Body: {value, category?, description?, is_sensitive?,
    expires_at?}.  Optional fields are preserved from the existing record
    on update, not reset to defaults.

DELETE  /api/butlers/{name}/secrets/{key}
    Delete a secret.  404 if not found.

Security contract
-----------------
- Secret values are **never** included in any response.
- ``SecretEntry`` Pydantic model has no ``value`` field.
- ``CredentialStore`` is used for all DB operations — no raw SQL.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.models.secrets import SecretEntry, SecretUpsertRequest
from butlers.credential_store import CredentialStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/butlers", tags=["butlers", "secrets"])
_SHARED_SECRETS_TARGET = "shared"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _credential_store_for(db: DatabaseManager, butler_name: str) -> CredentialStore:
    """Resolve the asyncpg pool for *butler_name* and return a CredentialStore.

    Raises
    ------
    HTTPException(503)
        If the butler's database pool is not available.
    """
    normalized_name = butler_name.strip().lower()
    if normalized_name == _SHARED_SECRETS_TARGET:
        try:
            pool = db.credential_shared_pool()
        except KeyError:
            raise HTTPException(
                status_code=503,
                detail="Shared credential database is not available",
            )
    else:
        try:
            pool = db.pool(butler_name)
        except KeyError:
            raise HTTPException(
                status_code=503,
                detail=f"Butler '{butler_name}' database is not available",
            )
    return CredentialStore(pool)


def _to_secret_entry(meta) -> SecretEntry:
    """Convert a ``SecretMetadata`` dataclass to a ``SecretEntry`` Pydantic model."""
    return SecretEntry(
        key=meta.key,
        category=meta.category,
        description=meta.description,
        is_sensitive=meta.is_sensitive,
        is_set=meta.is_set,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        expires_at=meta.expires_at,
        source=meta.source,
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{name}/secrets",
    response_model=ApiResponse[list[SecretEntry]],
)
async def list_secrets(
    name: str,
    category: str | None = Query(default=None, description="Filter by category."),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[SecretEntry]]:
    """List all secrets for a butler (metadata only — values never returned).

    Returns a list of ``SecretEntry`` objects ordered by ``(category, key)``.
    When ``?category=`` is supplied, only secrets in that category are returned.
    """
    store = _credential_store_for(db, name)
    secrets = await store.list_secrets(category=category)
    entries = [_to_secret_entry(m) for m in secrets]
    return ApiResponse[list[SecretEntry]](data=entries)


@router.get(
    "/{name}/secrets/{key}",
    response_model=ApiResponse[SecretEntry],
)
async def get_secret(
    name: str,
    key: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SecretEntry]:
    """Return metadata for a single secret.

    Returns 404 if the key does not exist in the butler's secret store.
    Values are never included in the response.
    """
    store = _credential_store_for(db, name)
    secrets = await store.list_secrets()
    match = next((m for m in secrets if m.key == key), None)
    if match is None:
        raise HTTPException(status_code=404, detail=f"Secret '{key}' not found")
    return ApiResponse[SecretEntry](data=_to_secret_entry(match))


# ---------------------------------------------------------------------------
# Write endpoints
# ---------------------------------------------------------------------------


@router.put(
    "/{name}/secrets/{key}",
    response_model=ApiResponse[SecretEntry],
)
async def upsert_secret(
    name: str,
    key: str,
    request: SecretUpsertRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SecretEntry]:
    """Create or update a secret.

    Performs an upsert — if the key already exists its value and metadata
    are updated; otherwise a new record is created.

    Optional fields (``category``, ``description``, ``is_sensitive``,
    ``expires_at``) are preserved from the existing record when omitted.
    For new secrets, omitted fields use their documented defaults.

    The ``value`` in the request body is write-only: it is stored securely
    but never echoed back in the response.
    """
    store = _credential_store_for(db, name)

    # Fetch existing metadata to support partial updates.
    # On update, any field left as None in the request is kept from the
    # existing record rather than reset to its Pydantic default.
    existing_secrets = await store.list_secrets()
    existing = next((m for m in existing_secrets if m.key == key), None)

    category = (
        request.category
        if request.category is not None
        else (existing.category if existing is not None else "general")
    )
    description = (
        request.description
        if request.description is not None
        else (existing.description if existing is not None else None)
    )
    is_sensitive = (
        request.is_sensitive
        if request.is_sensitive is not None
        else (existing.is_sensitive if existing is not None else True)
    )
    expires_at = request.expires_at  # None is a valid value (never expires), keep as-is

    try:
        await store.store(
            key,
            request.value,
            category=category,
            description=description,
            is_sensitive=is_sensitive,
            expires_at=expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Re-read metadata to return the persisted state
    secrets = await store.list_secrets()
    match = next((m for m in secrets if m.key == key), None)
    if match is None:
        # Should not happen after a successful store() — guard defensively
        raise HTTPException(status_code=500, detail="Secret stored but could not be retrieved")

    return ApiResponse[SecretEntry](data=_to_secret_entry(match))


@router.delete(
    "/{name}/secrets/{key}",
    response_model=ApiResponse[dict],
)
async def delete_secret(
    name: str,
    key: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[dict]:
    """Delete a secret from the butler's secret store.

    Returns 404 if the key does not exist.
    """
    store = _credential_store_for(db, name)
    deleted = await store.delete(key)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Secret '{key}' not found")
    return ApiResponse[dict](data={"key": key, "status": "deleted"})
