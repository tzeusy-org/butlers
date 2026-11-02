"""General butler endpoints.

Provides endpoints for collections, entities and owner projects. All data is queried
directly from the general butler's PostgreSQL database via asyncpg.

Every query filters to ordinary parents with the shared
``source_authority.ORDINARY_PARENT`` predicate before counting or paginating:
a private (or inconsistently classified) collection and all of its members are
absent from lists, counts and statistics, and an exact read of one is
indistinguishable from a missing entity.  A schema without the ordinary
contract answers 503 rather than falling back to unfiltered reads.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from butlers.api.audit_emit import authenticated_principal
from butlers.api.db import DatabaseManager
from butlers.api.deps import Db, OptionalDb
from butlers.api.models import (
    ApiMeta,
    ApiResponse,
    ErrorDetail,
    ErrorResponse,
    PaginatedResponse,
    PaginationMeta,
)
from butlers.api.owner_control import require_dashboard_owner_control
from butlers.core.dashboard_stepup import verified_dashboard_session
from butlers.roster_imports import roster_module
from butlers.tools.general.source_authority import (
    ORDINARY_PARENT,
    GeneralSourceUnavailable,
    is_unavailable_error,
    require_ordinary_namespace,
)

# Dynamically load models module from the same directory
_models = roster_module("general", "api/models", api_source=__file__)

Collection = _models.Collection
Entity = _models.Entity
GeneralStats = _models.GeneralStats
SizeHistogramBucket = _models.SizeHistogramBucket
ProjectCharter = _models.ProjectCharter
ProjectCharterEditRequest = _models.ProjectCharterEditRequest
ProjectCloseRequest = _models.ProjectCloseRequest
ProjectConstraints = _models.ProjectConstraints
ProjectCreateRequest = _models.ProjectCreateRequest
ProjectDetail = _models.ProjectDetail
ProjectLink = _models.ProjectLink
ProjectLinkRequest = _models.ProjectLinkRequest
ProjectSummary = _models.ProjectSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/general", tags=["general"])

BUTLER_DB = "general"


def _pool(db: DatabaseManager):
    """Retrieve the general butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="General butler database is not available",
        ) from exc


def _ordinary_only(endpoint):
    """Map a missing ordinary-source contract to a fixed, content-blind 503."""

    @functools.wraps(endpoint)
    async def wrapper(*args, **kwargs):
        try:
            return await endpoint(*args, **kwargs)
        except GeneralSourceUnavailable as exc:
            raise HTTPException(status_code=503, detail=GeneralSourceUnavailable.MESSAGE) from exc
        except asyncpg.PostgresError as exc:
            if is_unavailable_error(exc):
                raise HTTPException(
                    status_code=503, detail=GeneralSourceUnavailable.MESSAGE
                ) from exc
            raise

    return wrapper


# ---------------------------------------------------------------------------
# GET /stats — aggregated KPIs and size histogram
# ---------------------------------------------------------------------------

_HISTOGRAM_BRACKETS = ["0", "1-10", "11-100", "101+"]


@router.get("/stats", response_model=GeneralStats)
@_ordinary_only
async def get_stats(
    *,
    db: Db,
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

    total_collections: int = (
        await pool.fetchval(f"SELECT count(*) FROM collections AS c WHERE {ORDINARY_PARENT}") or 0
    )
    total_entities: int = (
        await pool.fetchval(
            "SELECT count(*) FROM collection_items AS i"
            f" JOIN collections AS c ON c.id = i.collection_id WHERE {ORDINARY_PARENT}"
        )
        or 0
    )

    last_modified_collection: str | None = await pool.fetchval(
        f"""
        SELECT name FROM (
            (SELECT c.name, i.updated_at AS ts
             FROM collection_items i
             JOIN collections c ON c.id = i.collection_id
             WHERE {ORDINARY_PARENT}
             ORDER BY i.updated_at DESC LIMIT 1)
            UNION ALL
            (SELECT c.name, c.created_at AS ts
             FROM collections c
             WHERE {ORDINARY_PARENT}
             ORDER BY c.created_at DESC LIMIT 1)
        ) sub ORDER BY ts DESC LIMIT 1
        """
    )

    largest_collection_size: int = (
        await pool.fetchval(
            f"""
            SELECT COALESCE(MAX(cnt), 0)
            FROM (
                SELECT count(*) AS cnt
                FROM collection_items i
                JOIN collections c ON c.id = i.collection_id
                WHERE {ORDINARY_PARENT}
                GROUP BY i.collection_id
            ) sub
            """
        )
        or 0
    )

    # Build size histogram: count collections per bracket
    size_rows = await pool.fetch(
        f"""
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
            WHERE {ORDINARY_PARENT}
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
@_ordinary_only
async def list_collections(
    q: str | None = Query(
        None, description="Case-insensitive substring filter on collection name."
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    *,
    db: Db,
) -> PaginatedResponse[Collection]:
    """List collections with entity counts, paginated.

    When ``q`` is provided, filters to collections whose name matches it
    (case-insensitive substring) — the search box on the Collections tab
    (bu-4u5l6). Without it, all collections are returned.
    """
    pool = _pool(db)
    # Trim surrounding whitespace and treat a blank/whitespace-only query as no
    # filter (return all) rather than an ILIKE '%   %' that matches spuriously.
    like = f"%{q.strip()}%" if q and q.strip() else None

    total = (
        await pool.fetchval(
            f"SELECT count(*) FROM collections AS c WHERE {ORDINARY_PARENT}"
            " AND ($1::text IS NULL OR c.name ILIKE $1)",
            like,
        )
        or 0
    )

    rows = await pool.fetch(
        f"""
        SELECT
            c.id,
            c.name,
            c.description,
            c.created_at,
            count(e.id) AS entity_count
        FROM collections c
        LEFT JOIN collection_items e ON e.collection_id = c.id
        WHERE {ORDINARY_PARENT} AND ($1::text IS NULL OR c.name ILIKE $1)
        GROUP BY c.id
        ORDER BY c.name
        OFFSET $2 LIMIT $3
        """,
        like,
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
@_ordinary_only
async def list_collection_entities(
    collection_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    *,
    db: Db,
) -> PaginatedResponse[Entity]:
    """List entities within a specific collection, paginated."""
    pool = _pool(db)

    total = (
        await pool.fetchval(
            "SELECT count(*) FROM collection_items AS e"
            f" JOIN collections AS c ON c.id = e.collection_id"
            f" WHERE e.collection_id = $1 AND {ORDINARY_PARENT}",
            collection_id,
        )
        or 0
    )

    rows = await pool.fetch(
        f"""
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
        WHERE e.collection_id = $1 AND {ORDINARY_PARENT}
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
@_ordinary_only
async def list_entities(
    q: str | None = Query(None, description="Search within entity data JSONB"),
    collection: str | None = Query(None, description="Filter by collection name"),
    tag: str | None = Query(None, description="Filter by tag"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    *,
    db: Db,
) -> PaginatedResponse[Entity]:
    """Search or list all entities across collections."""
    pool = _pool(db)

    conditions: list[str] = [ORDINARY_PARENT]
    args: list[object] = []
    idx = 1

    if q is not None:
        conditions.append(f"e.data::text ILIKE '%' || ${idx} || '%'")
        args.append(q)
        idx += 1

    if collection is not None:
        await require_ordinary_namespace(pool)
        conditions.append(f"c.name = ${idx}")
        args.append(collection)
        idx += 1

    if tag is not None:
        conditions.append(f"e.tags @> ${idx}::jsonb")
        args.append([tag])
        idx += 1

    where = " WHERE " + " AND ".join(conditions)

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
@_ordinary_only
async def get_entity(
    entity_id: str,
    *,
    db: Db,
) -> ApiResponse[Entity]:
    """Get a single entity by ID."""
    pool = _pool(db)

    row = await pool.fetchrow(
        f"""
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
        WHERE e.id = $1 AND {ORDINARY_PARENT}
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


# ---------------------------------------------------------------------------
# /api/general/projects — owner projects (bu-jv1moa.12)
# ---------------------------------------------------------------------------
#
# General holds owner projects: owner-authored, time-bounded charters that
# group goals, obligations and links; a project grants no authority.  These
# routes only read and write General's three project tables: they schedule,
# arm, wake, research and park nothing.  Owner control is a dependency, so a
# non-owner is refused before any body octet or pool access; writes further
# require the owner's verified dashboard session, so an API key, connector
# callback or runtime session can read but never author.  No MCP tool reaches
# these tables.  Linked records are referenced by kind and opaque id only and
# are resolved by the dashboard through each record's own owner-authorized
# reader, never by General SQL.

_PROJECTS_LOCK = "butlers:general:owner-projects:v1"
_PROJECTS_MAX_BODY_BYTES = 16_384
_PROJECT_MAX_LINKS = 50
_PROJECT_MAX_HORIZON = timedelta(days=180)

# Effective state: a draft whose horizon has passed reads as expired at exactly
# its horizon, whether or not a write has persisted the transition yet.
_PROJECT_COLUMNS = (
    "p.id, p.kind, p.title, p.current_charter_version, p.horizon_end, "
    "CASE WHEN p.state = 'draft' AND p.horizon_end <= now() THEN 'expired' "
    "ELSE p.state END AS state, "
    "CASE WHEN p.state = 'draft' AND p.horizon_end <= now() THEN p.horizon_end "
    "ELSE p.closed_at END AS closed_at, "
    "p.created_at, p.updated_at, "
    "(SELECT count(*) FROM project_links AS l WHERE l.project_id = p.id) AS link_count"
)
_CHARTER_COLUMNS = (
    "version, title, horizon_end, objective, done_condition, constraints, created_by, created_at"
)


class _ProjectRefusal(Exception):
    """A fixed, content-blind refusal for the owner project routes."""

    def __init__(self, refusal: tuple[int, str, str]) -> None:
        super().__init__(refusal[1])
        self.refusal = refusal


_INVALID_PROJECT = (422, "INVALID_REQUEST", "Project request is invalid.")
_PROJECT_TOO_LARGE = (413, "REQUEST_BODY_TOO_LARGE", "Request body exceeds 16 KiB.")
_PROJECTS_UNAVAILABLE = (503, "PROJECTS_UNAVAILABLE", "Owner projects are unavailable.")
_PROJECT_NOT_FOUND = (404, "PROJECT_NOT_FOUND", "Project not found.")
_LINK_NOT_FOUND = (404, "LINK_NOT_FOUND", "Link not found.")
_OWNER_SESSION_REQUIRED = (
    403,
    "OWNER_SESSION_REQUIRED",
    "Only the owner's dashboard session can author projects.",
)
_PROJECT_VERSION_CONFLICT = (
    409,
    "VERSION_CONFLICT",
    "This charter changed since it was loaded. Reload and try again.",
)
_PROJECT_NOT_DRAFT = (409, "PROJECT_NOT_DRAFT", "This project is closed or expired.")
_PROJECT_CONFLICT = (
    409,
    "PROJECT_CONFLICT",
    "Another draft project already has this title and horizon.",
)
_IDEMPOTENCY_KEY_REUSED = (
    409,
    "IDEMPOTENCY_KEY_REUSED",
    "This idempotency key was already used for a different project.",
)
_LINK_LIMIT = (409, "LINK_LIMIT", "A project holds at most 50 links.")
_INVALID_HORIZON = (
    422,
    "INVALID_HORIZON",
    "The horizon must be in the future and at most 180 days away.",
)


def _project_error(refusal: tuple[int, str, str]) -> JSONResponse:
    status_code, code, message = refusal
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def _project_ok(payload: BaseModel, status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
        headers={"Cache-Control": "no-store"},
    )


def _projects_pool(db: DatabaseManager | None):
    if db is None:
        raise _ProjectRefusal(_PROJECTS_UNAVAILABLE)
    try:
        return db.pool(BUTLER_DB)
    except Exception as exc:
        raise _ProjectRefusal(_PROJECTS_UNAVAILABLE) from exc


def _require_owner_session() -> None:
    """Writes need the owner's verified dashboard session, not merely owner control."""
    if verified_dashboard_session.get() is None:
        raise _ProjectRefusal(_OWNER_SESSION_REQUIRED)


async def _read_project_body[T: BaseModel](request: Request, model: type[T]) -> T:
    """Read at most 16 KiB and validate it without echoing any input."""
    if request.url.query:
        raise _ProjectRefusal(_INVALID_PROJECT)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk[: _PROJECTS_MAX_BODY_BYTES + 1 - len(raw)])
        if len(raw) > _PROJECTS_MAX_BODY_BYTES:
            raise _ProjectRefusal(_PROJECT_TOO_LARGE)
    try:
        return model.model_validate(json.loads(bytes(raw).decode("utf-8")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        # ValidationError and JSONDecodeError are both ValueError subclasses.
        raise _ProjectRefusal(_INVALID_PROJECT) from exc


def _parse_uuid(value: str, missing: tuple[int, str, str]) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError as exc:
        raise _ProjectRefusal(missing) from exc
    if str(parsed) != value:
        raise _ProjectRefusal(missing)
    return parsed


def _check_horizon(horizon_end: datetime) -> None:
    now = datetime.now(UTC)
    if not now < horizon_end <= now + _PROJECT_MAX_HORIZON:
        raise _ProjectRefusal(_INVALID_HORIZON)


def _charter_content(body: Any) -> dict[str, Any]:
    """The canonical charter content a version stores and an edit compares."""
    return {
        "title": body.title,
        "horizon_end": body.horizon_end.astimezone(UTC).isoformat(),
        "objective": body.objective,
        "done_condition": body.done_condition,
        "constraints": body.constraints.model_dump(mode="json"),
    }


def _stored_content(row: Any) -> dict[str, Any]:
    return {
        "title": row["title"],
        "horizon_end": row["horizon_end"].astimezone(UTC).isoformat(),
        "objective": row["objective"],
        "done_condition": row["done_condition"],
        "constraints": ProjectConstraints.model_validate(row["constraints"]).model_dump(
            mode="json"
        ),
    }


def _create_digest(body: Any) -> str:
    """SHA-256 over the canonical create request (everything but the key)."""
    canonical = {"kind": body.kind, **_charter_content(body)}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _summary(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "state": row["state"],
        "current_charter_version": row["current_charter_version"],
        "horizon_end": row["horizon_end"],
        "closed_at": row["closed_at"],
        "link_count": row["link_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


async def _load_detail(connection: Any, project_id: UUID) -> Any:
    row = await connection.fetchrow(
        f"SELECT {_PROJECT_COLUMNS} FROM projects AS p WHERE p.id = $1", project_id
    )
    if row is None:
        return None
    charter = await connection.fetchrow(
        f"SELECT {_CHARTER_COLUMNS} FROM project_charter_versions "
        "WHERE project_id = $1 AND version = $2",
        project_id,
        row["current_charter_version"],
    )
    links = await connection.fetch(
        "SELECT id, ref_kind, ref_id, created_at FROM project_links "
        "WHERE project_id = $1 ORDER BY ref_kind, created_at, id",
        project_id,
    )
    return ProjectDetail(
        **_summary(row),
        charter=ProjectCharter(
            version=charter["version"],
            title=charter["title"],
            horizon_end=charter["horizon_end"],
            objective=charter["objective"],
            done_condition=charter["done_condition"],
            constraints=ProjectConstraints.model_validate(charter["constraints"]),
            created_by=charter["created_by"],
            created_at=charter["created_at"],
        ),
        links=[ProjectLink(**dict(link)) for link in links],
    )


async def _sweep_expired(connection: Any) -> None:
    """Persist every due expiry (state and ``closed_at`` at the horizon)."""
    await connection.execute(
        "UPDATE projects SET state = 'expired', closed_at = horizon_end, updated_at = now() "
        "WHERE state = 'draft' AND horizon_end <= now()"
    )


async def _lock_draft(connection: Any, project_id: UUID) -> Any:
    """Lock one project row, persisting a due expiry first; refuse unless draft."""
    row = await connection.fetchrow(
        "SELECT id, state, current_charter_version, horizon_end <= now() AS due "
        "FROM projects WHERE id = $1 FOR UPDATE",
        project_id,
    )
    if row is None:
        return _PROJECT_NOT_FOUND
    if row["state"] == "draft" and row["due"]:
        await connection.execute(
            "UPDATE projects SET state = 'expired', closed_at = horizon_end, updated_at = now() "
            "WHERE id = $1",
            project_id,
        )
        return _PROJECT_NOT_DRAFT
    if row["state"] != "draft":
        return _PROJECT_NOT_DRAFT
    return row


async def _project_transaction(pool: Any, decide: Any) -> Any:
    """Run ``decide`` in one transaction under the projects lock; map failures blind."""
    try:
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                "SELECT pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended($1, 0))",
                _PROJECTS_LOCK,
            )
            return await decide(connection)
    except _ProjectRefusal:
        raise
    except asyncpg.UniqueViolationError as exc:
        raise _ProjectRefusal(_PROJECT_CONFLICT) from exc
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        logger.error("owner project write failed: database unavailable")
        raise _ProjectRefusal(_PROJECTS_UNAVAILABLE) from exc


async def _project_read(pool: Any, read: Any) -> Any:
    try:
        async with pool.acquire() as connection:
            return await read(connection)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        logger.error("owner project read failed: database unavailable")
        raise _ProjectRefusal(_PROJECTS_UNAVAILABLE) from exc


async def _insert_charter(connection: Any, project_id: UUID, version: int, body: Any) -> None:
    content = _charter_content(body)
    await connection.execute(
        "INSERT INTO project_charter_versions "
        "(project_id, version, title, horizon_end, objective, done_condition, constraints, "
        "created_by) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8)",
        project_id,
        version,
        body.title,
        body.horizon_end,
        body.objective,
        body.done_condition,
        content["constraints"],
        authenticated_principal(),
    )


@router.get(
    "/projects",
    response_model=PaginatedResponse[ProjectSummary],
    responses={503: {"model": ErrorResponse}},
)
async def list_projects(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Every project, drafts first, then by horizon."""
    try:
        pool = _projects_pool(db)

        async def read(connection: Any) -> Any:
            total = await connection.fetchval("SELECT count(*) FROM projects")
            rows = await connection.fetch(
                f"SELECT {_PROJECT_COLUMNS} FROM projects AS p "
                "ORDER BY (p.state = 'draft' AND p.horizon_end > now()) DESC, "
                "p.horizon_end, p.id OFFSET $1 LIMIT $2",
                offset,
                limit,
            )
            return total, rows

        total, rows = await _project_read(pool, read)
    except _ProjectRefusal as refusal:
        return _project_error(refusal.refusal)
    payload = PaginatedResponse[ProjectSummary](
        data=[ProjectSummary(**_summary(row)) for row in rows],
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )
    return _project_ok(payload)


@router.get(
    "/projects/{project_id}",
    response_model=ApiResponse[ProjectDetail],
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def get_project(
    project_id: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """One project with its current charter and typed links (unresolved)."""
    try:
        target = _parse_uuid(project_id, _PROJECT_NOT_FOUND)
        pool = _projects_pool(db)
        detail = await _project_read(pool, lambda connection: _load_detail(connection, target))
    except _ProjectRefusal as refusal:
        return _project_error(refusal.refusal)
    if detail is None:
        return _project_error(_PROJECT_NOT_FOUND)
    return _project_ok(ApiResponse[ProjectDetail](data=detail))


@router.post(
    "/projects",
    status_code=201,
    response_model=ApiResponse[ProjectDetail],
    responses={
        403: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def create_project(
    request: Request,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Create a draft project at charter version 1.

    Replaying the same ``idempotency_key`` with the same request returns that
    project (``meta.outcome = "replayed"``); the same key with a different
    request is ``409 IDEMPOTENCY_KEY_REUSED``.  A create whose normalized
    title and horizon match an existing draft returns that draft
    (``meta.outcome = "deduplicated"``) instead of creating a twin.
    """
    try:
        _require_owner_session()
        body = await _read_project_body(request, ProjectCreateRequest)
        digest = _create_digest(body)
        pool = _projects_pool(db)

        async def decide(connection: Any) -> Any:
            await _sweep_expired(connection)
            keyed = await connection.fetchrow(
                "SELECT id, create_request_digest FROM projects WHERE create_idempotency_key = $1",
                body.idempotency_key,
            )
            if keyed is not None:
                if keyed["create_request_digest"] != digest:
                    return _IDEMPOTENCY_KEY_REUSED
                return "replayed", await _load_detail(connection, keyed["id"])
            _check_horizon(body.horizon_end)
            twin = await connection.fetchval(
                "SELECT id FROM projects WHERE state = 'draft' "
                "AND title_key = lower(regexp_replace($1::text, '[[:space:]]+', ' ', 'g')) "
                "AND horizon_end = $2",
                body.title,
                body.horizon_end,
            )
            if twin is not None:
                return "deduplicated", await _load_detail(connection, twin)
            project_id = await connection.fetchval(
                "INSERT INTO projects (kind, title, current_charter_version, horizon_end, "
                "create_idempotency_key, create_request_digest, created_by) "
                "VALUES ($1, $2, 1, $3, $4, $5, $6) RETURNING id",
                body.kind,
                body.title,
                body.horizon_end,
                body.idempotency_key,
                digest,
                authenticated_principal(),
            )
            await _insert_charter(connection, project_id, 1, body)
            return "created", await _load_detail(connection, project_id)

        result = await _project_transaction(pool, decide)
    except _ProjectRefusal as refusal:
        return _project_error(refusal.refusal)
    if isinstance(result[0], int):
        return _project_error(result)
    outcome, detail = result
    payload = ApiResponse[ProjectDetail](
        data=detail, meta=ApiMeta.model_validate({"outcome": outcome})
    )
    return _project_ok(payload, 201 if outcome == "created" else 200)


@router.put(
    "/projects/{project_id}/charter",
    response_model=ApiResponse[ProjectDetail],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def edit_project_charter(
    request: Request,
    project_id: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Insert charter version ``expected_version + 1`` under compare-and-swap.

    An edit identical to the current charter writes nothing and succeeds even
    from a stale version; a changed edit from a stale version is
    ``409 VERSION_CONFLICT``.  Prior versions are never modified.
    """
    try:
        _require_owner_session()
        target = _parse_uuid(project_id, _PROJECT_NOT_FOUND)
        body = await _read_project_body(request, ProjectCharterEditRequest)
        pool = _projects_pool(db)

        async def decide(connection: Any) -> Any:
            locked = await _lock_draft(connection, target)
            if isinstance(locked, tuple):
                return locked
            current = locked["current_charter_version"]
            stored = await connection.fetchrow(
                f"SELECT {_CHARTER_COLUMNS} FROM project_charter_versions "
                "WHERE project_id = $1 AND version = $2",
                target,
                current,
            )
            if _stored_content(stored) == _charter_content(body):
                return "unchanged", await _load_detail(connection, target)
            if body.expected_version != current:
                return _PROJECT_VERSION_CONFLICT
            _check_horizon(body.horizon_end)
            # Persist due expiries first, so a draft that already reads as
            # expired never holds the draft title/horizon slot against this edit.
            await _sweep_expired(connection)
            await _insert_charter(connection, target, current + 1, body)
            await connection.execute(
                "UPDATE projects SET title = $2, horizon_end = $3, "
                "current_charter_version = $4, updated_at = now() WHERE id = $1",
                target,
                body.title,
                body.horizon_end,
                current + 1,
            )
            return "updated", await _load_detail(connection, target)

        result = await _project_transaction(pool, decide)
    except _ProjectRefusal as refusal:
        return _project_error(refusal.refusal)
    if isinstance(result[0], int):
        return _project_error(result)
    outcome, detail = result
    return _project_ok(
        ApiResponse[ProjectDetail](data=detail, meta=ApiMeta.model_validate({"outcome": outcome}))
    )


@router.post(
    "/projects/{project_id}/close",
    response_model=ApiResponse[ProjectDetail],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def close_project(
    request: Request,
    project_id: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Close a draft at its current charter version; linked records are untouched."""
    try:
        _require_owner_session()
        target = _parse_uuid(project_id, _PROJECT_NOT_FOUND)
        body = await _read_project_body(request, ProjectCloseRequest)
        pool = _projects_pool(db)

        async def decide(connection: Any) -> Any:
            locked = await _lock_draft(connection, target)
            if isinstance(locked, tuple):
                return locked
            if body.expected_version != locked["current_charter_version"]:
                return _PROJECT_VERSION_CONFLICT
            await connection.execute(
                "UPDATE projects SET state = 'closed', closed_at = now(), updated_at = now() "
                "WHERE id = $1",
                target,
            )
            return "closed", await _load_detail(connection, target)

        result = await _project_transaction(pool, decide)
    except _ProjectRefusal as refusal:
        return _project_error(refusal.refusal)
    if isinstance(result[0], int):
        return _project_error(result)
    outcome, detail = result
    return _project_ok(
        ApiResponse[ProjectDetail](data=detail, meta=ApiMeta.model_validate({"outcome": outcome}))
    )


@router.post(
    "/projects/{project_id}/links",
    status_code=201,
    response_model=ApiResponse[ProjectDetail],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def add_project_link(
    request: Request,
    project_id: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Link an existing record by kind and opaque id; re-adding is a no-op."""
    try:
        _require_owner_session()
        target = _parse_uuid(project_id, _PROJECT_NOT_FOUND)
        body = await _read_project_body(request, ProjectLinkRequest)
        pool = _projects_pool(db)

        async def decide(connection: Any) -> Any:
            locked = await _lock_draft(connection, target)
            if isinstance(locked, tuple):
                return locked
            inserted = await connection.fetchval(
                "INSERT INTO project_links (project_id, ref_kind, ref_id, created_by) "
                "SELECT $1::uuid, $2::text, $3::text, $4::text "
                "WHERE (SELECT count(*) FROM project_links WHERE project_id = $1::uuid) < $5::int "
                "ON CONFLICT (project_id, ref_kind, ref_id) DO NOTHING RETURNING id",
                target,
                body.ref_kind,
                body.ref_id,
                authenticated_principal(),
                _PROJECT_MAX_LINKS,
            )
            if inserted is None:
                exists = await connection.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM project_links "
                    "WHERE project_id = $1 AND ref_kind = $2 AND ref_id = $3)",
                    target,
                    body.ref_kind,
                    body.ref_id,
                )
                if not exists:
                    return _LINK_LIMIT
                return "unchanged", await _load_detail(connection, target)
            return "linked", await _load_detail(connection, target)

        result = await _project_transaction(pool, decide)
    except _ProjectRefusal as refusal:
        return _project_error(refusal.refusal)
    if isinstance(result[0], int):
        return _project_error(result)
    outcome, detail = result
    payload = ApiResponse[ProjectDetail](
        data=detail, meta=ApiMeta.model_validate({"outcome": outcome})
    )
    return _project_ok(payload, 201 if outcome == "linked" else 200)


@router.delete(
    "/projects/{project_id}/links/{link_id}",
    response_model=ApiResponse[ProjectDetail],
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
)
async def remove_project_link(
    request: Request,
    project_id: str,
    link_id: str,
    _owner: str = Depends(require_dashboard_owner_control),
    *,
    db: OptionalDb,
) -> Any:
    """Remove one link from a draft; the referenced record is never touched."""
    try:
        _require_owner_session()
        if request.url.query:
            raise _ProjectRefusal(_INVALID_PROJECT)
        target = _parse_uuid(project_id, _PROJECT_NOT_FOUND)
        link = _parse_uuid(link_id, _LINK_NOT_FOUND)
        pool = _projects_pool(db)

        async def decide(connection: Any) -> Any:
            locked = await _lock_draft(connection, target)
            if isinstance(locked, tuple):
                return locked
            removed = await connection.fetchval(
                "DELETE FROM project_links WHERE id = $1 AND project_id = $2 RETURNING id",
                link,
                target,
            )
            if removed is None:
                return _LINK_NOT_FOUND
            return "unlinked", await _load_detail(connection, target)

        result = await _project_transaction(pool, decide)
    except _ProjectRefusal as refusal:
        return _project_error(refusal.refusal)
    if isinstance(result[0], int):
        return _project_error(result)
    outcome, detail = result
    return _project_ok(
        ApiResponse[ProjectDetail](data=detail, meta=ApiMeta.model_validate({"outcome": outcome}))
    )
