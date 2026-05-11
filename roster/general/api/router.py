"""General butler endpoints.

Provides endpoints for collections and entities. All data is queried
directly from the general butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("general_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["general_api_models"] = _models
    _spec.loader.exec_module(_models)

    Collection = _models.Collection
    Entity = _models.Entity
    GeneralStats = _models.GeneralStats
    SizeHistogramBucket = _models.SizeHistogramBucket
else:
    raise RuntimeError("Failed to load general API models")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/general", tags=["general"])

BUTLER_DB = "general"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the general butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="General butler database is not available",
        )


# ---------------------------------------------------------------------------
# GET /stats — aggregated KPIs and size histogram
# ---------------------------------------------------------------------------

_HISTOGRAM_BRACKETS = ["0", "1-10", "11-100", "101+"]


@router.get("/stats", response_model=GeneralStats)
async def get_stats(
    db: DatabaseManager = Depends(_get_db_manager),
) -> GeneralStats:
    """Return aggregated KPIs and collection size histogram.

    - total_collections: number of collections
    - total_entities: total items across all collections
    - last_modified_collection: name of the collection with the most-recently
      modified item (falls back to the most-recently created collection when
      no items exist)
    - largest_collection_size: item count of the biggest collection
    - size_histogram: distribution of collections by item count
    """
    pool = _pool(db)

    total_collections: int = await pool.fetchval("SELECT count(*) FROM collections") or 0
    total_entities: int = await pool.fetchval("SELECT count(*) FROM collection_items") or 0

    last_modified_collection: str | None = await pool.fetchval(
        """
        SELECT name FROM (
            (SELECT c.name, i.updated_at AS ts
             FROM collection_items i
             JOIN collections c ON c.id = i.collection_id
             ORDER BY i.updated_at DESC LIMIT 1)
            UNION ALL
            (SELECT name, created_at AS ts
             FROM collections
             ORDER BY created_at DESC LIMIT 1)
        ) sub ORDER BY ts DESC LIMIT 1
        """
    )

    largest_collection_size: int = (
        await pool.fetchval(
            """
            SELECT COALESCE(MAX(cnt), 0)
            FROM (
                SELECT count(*) AS cnt
                FROM collection_items
                GROUP BY collection_id
            ) sub
            """
        )
        or 0
    )

    # Build size histogram: count collections per bracket
    size_rows = await pool.fetch(
        """
        SELECT
            CASE
                WHEN item_count = 0 THEN '0'
                WHEN item_count BETWEEN 1 AND 10 THEN '1-10'
                WHEN item_count BETWEEN 11 AND 100 THEN '11-100'
                ELSE '101+'
            END AS bracket,
            count(*) AS count
        FROM (
            SELECT c.id, count(i.id) AS item_count
            FROM collections c
            LEFT JOIN collection_items i ON i.collection_id = c.id
            GROUP BY c.id
        ) sub
        GROUP BY bracket
        """
    )

    bracket_map: dict[str, int] = {r["bracket"]: r["count"] for r in size_rows}
    # Always emit all brackets so the histogram shape is predictable for the frontend.
    size_histogram = [
        SizeHistogramBucket(bracket=label, count=bracket_map.get(label, 0))
        for label in _HISTOGRAM_BRACKETS
    ]

    return GeneralStats(
        total_collections=total_collections,
        total_entities=total_entities,
        last_modified_collection=last_modified_collection,
        largest_collection_size=largest_collection_size,
        size_histogram=size_histogram,
    )


# ---------------------------------------------------------------------------
# GET /collections — list collections with entity counts
# ---------------------------------------------------------------------------


@router.get("/collections", response_model=PaginatedResponse[Collection])
async def list_collections(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Collection]:
    """List collections with entity counts, paginated."""
    pool = _pool(db)

    total = await pool.fetchval("SELECT count(*) FROM collections") or 0

    rows = await pool.fetch(
        """
        SELECT
            c.id,
            c.name,
            c.description,
            c.created_at,
            count(e.id) AS entity_count
        FROM collections c
        LEFT JOIN collection_items e ON e.collection_id = c.id
        GROUP BY c.id
        ORDER BY c.name
        OFFSET $1 LIMIT $2
        """,
        offset,
        limit,
    )

    data = [
        Collection(
            id=str(r["id"]),
            name=r["name"],
            description=r["description"],
            entity_count=r["entity_count"],
            created_at=str(r["created_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Collection](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /collections/{collection_id}/entities — list entities in a collection
# ---------------------------------------------------------------------------


@router.get(
    "/collections/{collection_id}/entities",
    response_model=PaginatedResponse[Entity],
)
async def list_collection_entities(
    collection_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Entity]:
    """List entities within a specific collection, paginated."""
    pool = _pool(db)

    total = (
        await pool.fetchval(
            "SELECT count(*) FROM collection_items WHERE collection_id = $1",
            collection_id,
        )
        or 0
    )

    rows = await pool.fetch(
        """
        SELECT
            e.id,
            e.collection_id,
            c.name AS collection_name,
            e.data,
            e.tags,
            e.created_at,
            e.updated_at
        FROM collection_items e
        JOIN collections c ON c.id = e.collection_id
        WHERE e.collection_id = $1
        ORDER BY e.created_at DESC
        OFFSET $2 LIMIT $3
        """,
        collection_id,
        offset,
        limit,
    )

    data = [
        Entity(
            id=str(r["id"]),
            collection_id=str(r["collection_id"]),
            collection_name=r["collection_name"],
            data=dict(r["data"]) if r["data"] else {},
            tags=list(r["tags"]) if r["tags"] else [],
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Entity](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /entities — search/list all entities
# ---------------------------------------------------------------------------


@router.get("/entities", response_model=PaginatedResponse[Entity])
async def list_entities(
    q: str | None = Query(None, description="Search within entity data JSONB"),
    collection: str | None = Query(None, description="Filter by collection name"),
    tag: str | None = Query(None, description="Filter by tag"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[Entity]:
    """Search or list all entities across collections."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(f"e.data::text ILIKE '%' || ${idx} || '%'")
        args.append(q)
        idx += 1

    if collection is not None:
        conditions.append(f"c.name = ${idx}")
        args.append(collection)
        idx += 1

    if tag is not None:
        conditions.append(f"e.tags @> ${idx}::jsonb")
        args.append([tag])
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = (
        "SELECT count(*) FROM collection_items e"
        f" JOIN collections c ON c.id = e.collection_id{where}"
    )
    total = await pool.fetchval(count_sql, *args) or 0

    data_sql = (
        f"SELECT e.id, e.collection_id, c.name AS collection_name,"
        f" e.data, e.tags, e.created_at, e.updated_at"
        f" FROM collection_items e"
        f" JOIN collections c ON c.id = e.collection_id{where}"
        f" ORDER BY e.created_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}"
    )
    rows = await pool.fetch(data_sql, *args, offset, limit)

    data = [
        Entity(
            id=str(r["id"]),
            collection_id=str(r["collection_id"]),
            collection_name=r["collection_name"],
            data=dict(r["data"]) if r["data"] else {},
            tags=list(r["tags"]) if r["tags"] else [],
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[Entity](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /entities/{entity_id} — entity detail
# ---------------------------------------------------------------------------


@router.get("/entities/{entity_id}", response_model=ApiResponse[Entity])
async def get_entity(
    entity_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[Entity]:
    """Get a single entity by ID."""
    pool = _pool(db)

    row = await pool.fetchrow(
        """
        SELECT
            e.id,
            e.collection_id,
            c.name AS collection_name,
            e.data,
            e.tags,
            e.created_at,
            e.updated_at
        FROM collection_items e
        JOIN collections c ON c.id = e.collection_id
        WHERE e.id = $1
        """,
        entity_id,
    )

    if row is None:
        raise HTTPException(status_code=404, detail="Entity not found")

    entity = Entity(
        id=str(row["id"]),
        collection_id=str(row["collection_id"]),
        collection_name=row["collection_name"],
        data=dict(row["data"]) if row["data"] else {},
        tags=list(row["tags"]) if row["tags"] else [],
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )

    return ApiResponse[Entity](data=entity)
