"""Education butler endpoints.

Provides endpoints for mind maps (list, detail, frontier, analytics),
quiz responses, teaching flows, and cross-topic analytics. All data is
queried directly from the education butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.tools.education.analytics import (
    analytics_get_cross_topic,
    analytics_get_snapshot,
    analytics_get_trend,
)
from butlers.tools.education.mind_map_queries import mind_map_frontier
from butlers.tools.education.mind_maps import mind_map_get, mind_map_list
from butlers.tools.education.teaching_flows import teaching_flow_list

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("education_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["education_api_models"] = _models
    _spec.loader.exec_module(_models)

    AnalyticsSnapshotResponse = _models.AnalyticsSnapshotResponse
    CrossTopicAnalyticsResponse = _models.CrossTopicAnalyticsResponse
    CrossTopicTopicEntry = _models.CrossTopicTopicEntry
    MasterySummaryResponse = _models.MasterySummaryResponse
    MindMapEdgeResponse = _models.MindMapEdgeResponse
    MindMapNodeResponse = _models.MindMapNodeResponse
    MindMapResponse = _models.MindMapResponse
    QuizResponseModel = _models.QuizResponseModel
    TeachingFlowResponse = _models.TeachingFlowResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/education", tags=["education"])

BUTLER_DB = "education"


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


def _pool(db: DatabaseManager):
    """Retrieve the education butler's connection pool.

    Raises HTTPException 503 if the pool is not available.
    """
    try:
        return db.pool(BUTLER_DB)
    except KeyError:
        raise HTTPException(
            status_code=503,
            detail="Education butler database is not available",
        )


# ---------------------------------------------------------------------------
# Helper: convert raw dict to MindMapNodeResponse
# ---------------------------------------------------------------------------


def _node_dict_to_response(n: dict) -> MindMapNodeResponse:
    """Convert a node dict (from mind_map_node_list or mind_map_get) to a response model."""
    return MindMapNodeResponse(
        id=str(n["id"]),
        mind_map_id=str(n["mind_map_id"]),
        label=n["label"],
        description=n.get("description"),
        depth=int(n.get("depth", 0)),
        mastery_score=float(n.get("mastery_score", 0.0)),
        mastery_status=n.get("mastery_status", "unseen"),
        ease_factor=float(n.get("ease_factor", 2.5)),
        repetitions=int(n.get("repetitions", 0)),
        next_review_at=str(n["next_review_at"]) if n.get("next_review_at") else None,
        last_reviewed_at=str(n["last_reviewed_at"]) if n.get("last_reviewed_at") else None,
        effort_minutes=int(n["effort_minutes"]) if n.get("effort_minutes") is not None else None,
        metadata=dict(n.get("metadata") or {}),
        created_at=str(n["created_at"]),
        updated_at=str(n["updated_at"]),
    )


# ---------------------------------------------------------------------------
# Helper: convert raw mind map dict to MindMapResponse
# ---------------------------------------------------------------------------


def _map_dict_to_response(m: dict, include_dag: bool = False) -> MindMapResponse:
    """Convert a mind map dict (from mind_map_list/mind_map_get) to a response model."""
    nodes: list[MindMapNodeResponse] = []
    edges: list[MindMapEdgeResponse] = []

    if include_dag:
        for n in m.get("nodes", []):
            nodes.append(_node_dict_to_response(n))
        for e in m.get("edges", []):
            edges.append(
                MindMapEdgeResponse(
                    parent_node_id=str(e["parent_node_id"]),
                    child_node_id=str(e["child_node_id"]),
                    edge_type=e.get("edge_type", "prerequisite"),
                )
            )

    return MindMapResponse(
        id=str(m["id"]),
        title=m["title"],
        root_node_id=str(m["root_node_id"]) if m.get("root_node_id") else None,
        status=m["status"],
        created_at=str(m["created_at"]),
        updated_at=str(m["updated_at"]),
        nodes=nodes,
        edges=edges,
    )


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps — paginated list
# ---------------------------------------------------------------------------


@router.get("/mind-maps", response_model=PaginatedResponse[MindMapResponse])
async def list_mind_maps(
    status: str | None = Query(None, description="Filter by status (active, completed, abandoned)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[MindMapResponse]:
    """List mind maps with optional status filter and pagination."""
    pool = _pool(db)

    all_maps = await mind_map_list(pool, status=status)
    total = len(all_maps)

    page = all_maps[offset : offset + limit]
    data = [_map_dict_to_response(m, include_dag=False) for m in page]

    return PaginatedResponse[MindMapResponse](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id} — full DAG with nodes and edges
# ---------------------------------------------------------------------------


@router.get("/mind-maps/{mind_map_id}", response_model=MindMapResponse)
async def get_mind_map(
    mind_map_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> MindMapResponse:
    """Retrieve a mind map by ID with full node and edge DAG."""
    pool = _pool(db)

    m = await mind_map_get(pool, mind_map_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    return _map_dict_to_response(m, include_dag=True)


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/frontier — frontier nodes
# ---------------------------------------------------------------------------


@router.get("/mind-maps/{mind_map_id}/frontier", response_model=list[MindMapNodeResponse])
async def get_mind_map_frontier(
    mind_map_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[MindMapNodeResponse]:
    """Return frontier nodes for a mind map.

    Frontier = nodes where prerequisites are all mastered and the node itself
    is not yet mastered.
    """
    pool = _pool(db)

    # Verify the mind map exists first
    m = await mind_map_get(pool, mind_map_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    nodes = await mind_map_frontier(pool, mind_map_id)
    return [_node_dict_to_response(n) for n in nodes]


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/analytics — analytics snapshot + trend
# ---------------------------------------------------------------------------


@router.get("/mind-maps/{mind_map_id}/analytics", response_model=AnalyticsSnapshotResponse)
async def get_mind_map_analytics(
    mind_map_id: str,
    trend_days: int | None = Query(
        None, ge=1, le=365, description="Include trend for this many days"
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> AnalyticsSnapshotResponse:
    """Return the latest analytics snapshot for a mind map, with optional trend."""
    pool = _pool(db)

    # Verify the mind map exists
    m = await mind_map_get(pool, mind_map_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    snapshot = await analytics_get_snapshot(pool, mind_map_id)
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=f"No analytics snapshot found for mind map: {mind_map_id}",
        )

    trend: list[dict] = []
    if trend_days is not None:
        trend_rows = await analytics_get_trend(pool, mind_map_id, days=trend_days)
        for row in trend_rows:
            trend.append(
                {
                    "id": str(row.get("id", "")),
                    "mind_map_id": str(row.get("mind_map_id", mind_map_id)),
                    "snapshot_date": str(row.get("snapshot_date", "")),
                    "metrics": dict(row.get("metrics") or {}),
                    "created_at": str(row.get("created_at", "")),
                }
            )

    return AnalyticsSnapshotResponse(
        id=str(snapshot.get("id", "")) if snapshot.get("id") else None,
        mind_map_id=str(snapshot.get("mind_map_id", mind_map_id)),
        snapshot_date=str(snapshot["snapshot_date"]),
        metrics=dict(snapshot.get("metrics") or {}),
        created_at=str(snapshot.get("created_at", "")) if snapshot.get("created_at") else None,
        trend=trend,
    )


# ---------------------------------------------------------------------------
# GET /api/education/quiz-responses — paginated quiz history
# ---------------------------------------------------------------------------


@router.get("/quiz-responses", response_model=PaginatedResponse[QuizResponseModel])
async def list_quiz_responses(
    mind_map_id: str | None = Query(None, description="Filter by mind map ID"),
    node_id: str | None = Query(None, description="Filter by node ID"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[QuizResponseModel]:
    """List quiz responses with optional mind_map_id and node_id filters."""
    pool = _pool(db)

    conditions: list[str] = []
    args: list[object] = []
    idx = 1

    if mind_map_id is not None:
        conditions.append(f"mind_map_id = ${idx}::uuid")
        args.append(mind_map_id)
        idx += 1

    if node_id is not None:
        conditions.append(f"node_id = ${idx}::uuid")
        args.append(node_id)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = await pool.fetchval(f"SELECT count(*) FROM education.quiz_responses{where}", *args) or 0

    rows = await pool.fetch(
        f"SELECT id, node_id, mind_map_id, question_text, user_answer, quality,"
        f" response_type, session_id, responded_at"
        f" FROM education.quiz_responses{where}"
        f" ORDER BY responded_at DESC"
        f" OFFSET ${idx} LIMIT ${idx + 1}",
        *args,
        offset,
        limit,
    )

    data = [
        QuizResponseModel(
            id=str(r["id"]),
            node_id=str(r["node_id"]),
            mind_map_id=str(r["mind_map_id"]),
            question_text=r["question_text"],
            user_answer=r["user_answer"],
            quality=int(r["quality"]),
            response_type=r["response_type"],
            session_id=str(r["session_id"]) if r["session_id"] else None,
            responded_at=str(r["responded_at"]),
        )
        for r in rows
    ]

    return PaginatedResponse[QuizResponseModel](
        data=data,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/education/flows — teaching flows list
# ---------------------------------------------------------------------------


@router.get("/flows", response_model=list[TeachingFlowResponse])
async def list_flows(
    status: str | None = Query(
        None,
        description=(
            "Filter by flow status (pending, diagnosing, planning, teaching, "
            "quizzing, reviewing, completed, abandoned)"
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[TeachingFlowResponse]:
    """List teaching flows with optional status filter."""
    pool = _pool(db)

    flows = await teaching_flow_list(pool, status=status)

    return [
        TeachingFlowResponse(
            mind_map_id=f["mind_map_id"],
            title=f["title"],
            status=f["status"],
            session_count=int(f.get("session_count", 0)),
            started_at=str(f["started_at"]) if f.get("started_at") else None,
            last_session_at=str(f["last_session_at"]) if f.get("last_session_at") else None,
            mastery_pct=float(f.get("mastery_pct", 0.0)),
        )
        for f in flows
    ]


# ---------------------------------------------------------------------------
# GET /api/education/analytics/cross-topic — cross-topic comparison
# ---------------------------------------------------------------------------


@router.get("/analytics/cross-topic", response_model=CrossTopicAnalyticsResponse)
async def get_cross_topic_analytics(
    db: DatabaseManager = Depends(_get_db_manager),
) -> CrossTopicAnalyticsResponse:
    """Return comparative analytics across all active mind maps."""
    pool = _pool(db)

    result = await analytics_get_cross_topic(pool)

    topics = [
        CrossTopicTopicEntry(
            mind_map_id=t["mind_map_id"],
            title=t["title"],
            mastery_pct=float(t.get("mastery_pct", 0.0)),
            retention_rate_7d=(
                float(t["retention_rate_7d"]) if t.get("retention_rate_7d") is not None else None
            ),
            velocity=float(t.get("velocity", 0.0)),
        )
        for t in result.get("topics", [])
    ]

    return CrossTopicAnalyticsResponse(
        topics=topics,
        strongest_topic=result.get("strongest_topic"),
        weakest_topic=result.get("weakest_topic"),
        portfolio_mastery=float(result.get("portfolio_mastery", 0.0)),
    )
