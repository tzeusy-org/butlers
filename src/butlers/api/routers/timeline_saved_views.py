"""Timeline saved-views API — CRUD over public.timeline_saved_views.

Provides:

- ``router`` — endpoints under ``/api/timeline/saved-views``

Endpoints
---------
GET    /api/timeline/saved-views          — list all saved views (newest first)
POST   /api/timeline/saved-views          — create a new saved view (201)
PATCH  /api/timeline/saved-views/{id}     — update name and/or filter_spec (200)
DELETE /api/timeline/saved-views/{id}     — delete a saved view (204)

Design
------
Saved views are global (single-owner system — no per-user scoping).
``filter_spec`` is stored as JSONB; keys are frontend-driven and may evolve
without requiring a schema migration.  The API validates that ``filter_spec``
is a JSON object but does not impose a fixed key schema.

Spec: openspec/changes/complete-ingestion-redesign-parity/specs/
      dashboard-ingestion-dispatch-console/spec.md §"Saved Views" §2.8
Issue: bu-att72
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/timeline/saved-views", tags=["timeline"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SavedViewEntry(BaseModel):
    """A single persisted saved view returned from the API."""

    id: UUID
    name: str
    filter_spec: dict[str, Any]
    created_at: str
    updated_at: str


class SavedViewCreateRequest(BaseModel):
    """Request body for creating a saved view."""

    name: str = Field(..., min_length=1, max_length=100)
    filter_spec: dict[str, Any] = Field(default_factory=dict)

    @field_validator("filter_spec")
    @classmethod
    def validate_filter_spec(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(v, dict):
            raise ValueError("filter_spec must be a JSON object")
        return v


class SavedViewUpdateRequest(BaseModel):
    """Request body for updating a saved view (all fields optional)."""

    name: str | None = Field(None, min_length=1, max_length=100)
    filter_spec: dict[str, Any] | None = None

    @field_validator("filter_spec")
    @classmethod
    def validate_filter_spec(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        if v is not None and not isinstance(v, dict):
            raise ValueError("filter_spec must be a JSON object")
        return v


# ---------------------------------------------------------------------------
# Pool helper
# ---------------------------------------------------------------------------


def _shared_pool(db: DatabaseManager):
    """Return the shared credential pool; raise 503 if unavailable."""
    try:
        return db.credential_shared_pool()
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="Shared database is not available",
        ) from exc


# ---------------------------------------------------------------------------
# Row → model helper
# ---------------------------------------------------------------------------


def _row_to_entry(row: Any) -> SavedViewEntry:
    created_at = row["created_at"]
    updated_at = row["updated_at"]
    return SavedViewEntry(
        id=row["id"],
        name=row["name"],
        filter_spec=dict(row["filter_spec"]) if row["filter_spec"] else {},
        created_at=created_at.isoformat() if created_at else "",
        updated_at=updated_at.isoformat() if updated_at else "",
    )


# ---------------------------------------------------------------------------
# GET /api/timeline/saved-views
# ---------------------------------------------------------------------------

_LIST_SQL = """
    SELECT id, name, filter_spec, created_at, updated_at
    FROM public.timeline_saved_views
    ORDER BY created_at DESC
"""


@router.get("", response_model=ApiResponse[list[SavedViewEntry]])
async def list_saved_views(
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[list[SavedViewEntry]]:
    """Return all persisted saved views, newest first.

    Returns 200 with an empty list when no saved views exist.
    Returns 503 when the shared database is not available.
    """
    pool = _shared_pool(db)
    rows = await pool.fetch(_LIST_SQL)
    entries = [_row_to_entry(row) for row in rows]
    return ApiResponse[list[SavedViewEntry]](data=entries)


# ---------------------------------------------------------------------------
# POST /api/timeline/saved-views
# ---------------------------------------------------------------------------

_INSERT_SQL = """
    INSERT INTO public.timeline_saved_views (name, filter_spec)
    VALUES ($1, $2)
    RETURNING id, name, filter_spec, created_at, updated_at
"""


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SavedViewEntry)
async def create_saved_view(
    body: SavedViewCreateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> SavedViewEntry:
    """Create a new saved view.

    Persists ``name`` and ``filter_spec`` to ``public.timeline_saved_views``.

    Returns HTTP 201 on success.
    Returns HTTP 503 when the shared database is not available.
    """
    pool = _shared_pool(db)

    # Pass the dict directly — the asyncpg JSONB codec handles encoding.
    # json.dumps() here would double-encode and store a JSONB string scalar.
    row = await pool.fetchrow(_INSERT_SQL, body.name, body.filter_spec)
    return _row_to_entry(row)


# ---------------------------------------------------------------------------
# PATCH /api/timeline/saved-views/{id}
# ---------------------------------------------------------------------------


@router.patch("/{view_id}", response_model=SavedViewEntry)
async def update_saved_view(
    view_id: UUID,
    body: SavedViewUpdateRequest,
    db: DatabaseManager = Depends(_get_db_manager),
) -> SavedViewEntry:
    """Update a saved view's name and/or filter_spec.

    Only the fields included in the request body are updated.
    ``updated_at`` is refreshed automatically.

    Returns HTTP 200 on success.
    Returns HTTP 404 when no saved view with ``view_id`` exists.
    Returns HTTP 400 when neither ``name`` nor ``filter_spec`` is provided.
    Returns HTTP 503 when the shared database is not available.
    """
    if body.name is None and body.filter_spec is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of 'name' or 'filter_spec' must be provided",
        )

    pool = _shared_pool(db)

    # Build a dynamic SET clause from only the supplied fields
    set_parts: list[str] = ["updated_at = now()"]
    args: list[Any] = []
    idx = 1

    if body.name is not None:
        set_parts.append(f"name = ${idx}")
        args.append(body.name)
        idx += 1

    if body.filter_spec is not None:
        set_parts.append(f"filter_spec = ${idx}")
        # Pass the dict directly — the asyncpg JSONB codec handles encoding.
        # json.dumps() here would double-encode and store a JSONB string scalar.
        args.append(body.filter_spec)
        idx += 1

    args.append(view_id)
    update_sql = (
        f"UPDATE public.timeline_saved_views "
        f"SET {', '.join(set_parts)} "
        f"WHERE id = ${idx} "
        f"RETURNING id, name, filter_spec, created_at, updated_at"
    )

    row = await pool.fetchrow(update_sql, *args)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Saved view '{view_id}' not found")

    return _row_to_entry(row)


# ---------------------------------------------------------------------------
# DELETE /api/timeline/saved-views/{id}
# ---------------------------------------------------------------------------

_DELETE_SQL = "DELETE FROM public.timeline_saved_views WHERE id = $1"


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_view(
    view_id: UUID,
    db: DatabaseManager = Depends(_get_db_manager),
) -> None:
    """Delete a saved view.

    Returns HTTP 204 on success.
    Returns HTTP 404 when no saved view with ``view_id`` exists.
    Returns HTTP 503 when the shared database is not available.
    """
    pool = _shared_pool(db)

    result = await pool.execute(_DELETE_SQL, view_id)
    deleted_count = int(result.split()[-1]) if result else 0
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail=f"Saved view '{view_id}' not found")
