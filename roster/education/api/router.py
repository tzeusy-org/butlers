"""Education butler endpoints.

Provides endpoints for mind maps (list, detail, frontier, analytics),
quiz responses, teaching flows, and cross-topic analytics. All data is
queried directly from the education butler's PostgreSQL database via asyncpg.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from butlers.api.audit_emit import emit_dashboard_audit
from butlers.api.db import DatabaseManager
from butlers.api.deps import MCPClientManager, get_mcp_manager
from butlers.api.models import PaginatedResponse, PaginationMeta
from butlers.core.attention_ledger import find_notify_dispatch_for_session
from butlers.core.state import state_get
from butlers.tools.education.analytics import (
    analytics_get_cross_topic,
    analytics_get_snapshot,
    analytics_get_trend,
)
from butlers.tools.education.mastery import mastery_detect_struggles, mastery_get_map_summary
from butlers.tools.education.mind_map_queries import mind_map_frontier
from butlers.tools.education.mind_maps import mind_map_get, mind_map_list, mind_map_update_status
from butlers.tools.education.spaced_repetition import spaced_repetition_pending_reviews
from butlers.tools.education.teaching_flows import teaching_flow_list

# Dynamically load models module from the same directory
_models_path = Path(__file__).parent / "models.py"
_spec = importlib.util.spec_from_file_location("education_api_models", _models_path)
if _spec is not None and _spec.loader is not None:
    _models = importlib.util.module_from_spec(_spec)
    sys.modules["education_api_models"] = _models
    _spec.loader.exec_module(_models)

    AnalyticsSnapshotResponse = _models.AnalyticsSnapshotResponse
    AnalyticsTrendEntry = _models.AnalyticsTrendEntry
    AnalyticsTrendResponse = _models.AnalyticsTrendResponse
    CrossTopicAnalyticsResponse = _models.CrossTopicAnalyticsResponse
    CrossTopicTopicEntry = _models.CrossTopicTopicEntry
    CurriculumRequestBody = _models.CurriculumRequestBody
    CurriculumRequestReceipt = _models.CurriculumRequestReceipt
    CurriculumRequestResponse = _models.CurriculumRequestResponse
    CurriculumRequestStatusResponse = _models.CurriculumRequestStatusResponse
    MasterySummaryResponse = _models.MasterySummaryResponse
    MindMapEdgeResponse = _models.MindMapEdgeResponse
    MindMapNodeResponse = _models.MindMapNodeResponse
    MindMapResponse = _models.MindMapResponse
    PendingReviewNodeResponse = _models.PendingReviewNodeResponse
    QuizResponseModel = _models.QuizResponseModel
    StatusUpdateRequest = _models.StatusUpdateRequest
    StrugglingNodeEntry = _models.StrugglingNodeEntry
    StrugglingNodesResponse = _models.StrugglingNodesResponse
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
        conditions.append(f"qr.mind_map_id = ${idx}::uuid")
        args.append(mind_map_id)
        idx += 1

    if node_id is not None:
        conditions.append(f"qr.node_id = ${idx}::uuid")
        args.append(node_id)
        idx += 1

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    total = (
        await pool.fetchval(f"SELECT count(*) FROM education.quiz_responses qr{where}", *args) or 0
    )

    rows = await pool.fetch(
        f"SELECT qr.id, qr.node_id, qr.mind_map_id, qr.question_text, qr.user_answer,"
        f" qr.quality, qr.response_type, qr.session_id, qr.responded_at,"
        f" qr.evaluator_notes, n.label AS node_label"
        f" FROM education.quiz_responses qr"
        f" LEFT JOIN education.mind_map_nodes n ON n.id = qr.node_id"
        f"{where}"
        f" ORDER BY qr.responded_at DESC"
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
            evaluator_notes=r["evaluator_notes"],
            node_label=r["node_label"],
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


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/pending-reviews — nodes due for review
# ---------------------------------------------------------------------------


@router.get(
    "/mind-maps/{mind_map_id}/pending-reviews",
    response_model=list[PendingReviewNodeResponse],
)
async def get_pending_reviews(
    mind_map_id: str,
    horizon_days: int | None = Query(
        default=None,
        ge=0,
        description=(
            "Include reviews due within this many days from now. "
            "Omit to return only overdue nodes (next_review_at <= now)."
        ),
    ),
    db: DatabaseManager = Depends(_get_db_manager),
) -> list[PendingReviewNodeResponse]:
    """Return nodes due (or upcoming) for spaced-repetition review.

    With no horizon_days, returns only overdue nodes (next_review_at <= now).
    With horizon_days set, also includes upcoming reviews within that window,
    enabling timeline grouping (Overdue / Today / This Week / Later).
    """
    pool = _pool(db)

    m = await mind_map_get(pool, mind_map_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    nodes = await spaced_repetition_pending_reviews(pool, mind_map_id, horizon_days=horizon_days)
    return [
        PendingReviewNodeResponse(
            node_id=n["node_id"],
            label=n["label"],
            ease_factor=float(n["ease_factor"]),
            repetitions=int(n["repetitions"]),
            next_review_at=str(n["next_review_at"]),
            mastery_status=n["mastery_status"],
            mastery_score=(
                float(n["mastery_score"]) if n.get("mastery_score") is not None else None
            ),
        )
        for n in nodes
    ]


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/mastery-summary — aggregate mastery stats
# ---------------------------------------------------------------------------


@router.get(
    "/mind-maps/{mind_map_id}/mastery-summary",
    response_model=MasterySummaryResponse,
)
async def get_mastery_summary(
    mind_map_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> MasterySummaryResponse:
    """Return aggregate mastery statistics for a mind map."""
    pool = _pool(db)

    m = await mind_map_get(pool, mind_map_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    summary = await mastery_get_map_summary(pool, mind_map_id)
    return MasterySummaryResponse(
        mind_map_id=mind_map_id,
        total_nodes=int(summary["total_nodes"]),
        mastered_count=int(summary["mastered_count"]),
        learning_count=int(summary["learning_count"]),
        reviewing_count=int(summary["reviewing_count"]),
        unseen_count=int(summary["unseen_count"]),
        diagnosed_count=int(summary["diagnosed_count"]),
        avg_mastery_score=float(summary["avg_mastery_score"]),
        struggling_node_ids=[str(nid) for nid in summary.get("struggling_node_ids", [])],
    )


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/analytics/trend — analytics trend series
# ---------------------------------------------------------------------------


@router.get(
    "/mind-maps/{mind_map_id}/analytics/trend",
    response_model=AnalyticsTrendResponse,
)
async def get_analytics_trend(
    mind_map_id: str,
    days: int = Query(7, ge=1, le=365, description="Number of days to look back"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> AnalyticsTrendResponse:
    """Return a time-series of analytics snapshots for a mind map.

    Snapshots are ordered oldest-first within the requested day window.
    Returns an empty trend list when no snapshots exist for the window.
    """
    pool = _pool(db)

    m = await mind_map_get(pool, mind_map_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    rows = await analytics_get_trend(pool, mind_map_id, days=days)

    trend = [
        AnalyticsTrendEntry(
            id=str(row["id"]) if row.get("id") else None,
            mind_map_id=str(row.get("mind_map_id", mind_map_id)),
            snapshot_date=str(row["snapshot_date"]),
            metrics=dict(row.get("metrics") or {}),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
        )
        for row in rows
    ]

    return AnalyticsTrendResponse(mind_map_id=mind_map_id, days=days, trend=trend)


# ---------------------------------------------------------------------------
# GET /api/education/mind-maps/{id}/struggling-nodes — struggling nodes
# ---------------------------------------------------------------------------


@router.get(
    "/mind-maps/{mind_map_id}/struggling-nodes",
    response_model=StrugglingNodesResponse,
)
async def get_struggling_nodes(
    mind_map_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> StrugglingNodesResponse:
    """Return concept nodes with declining or consistently low mastery.

    A node is flagged as struggling if its 3 most recent quiz responses all
    have quality <= 2, or if mastery score has declined over the last 3
    responses. Mastered nodes and nodes with fewer than 3 responses are
    excluded.

    Returns an empty nodes list when no struggling nodes are detected.
    """
    pool = _pool(db)

    m = await mind_map_get(pool, mind_map_id)
    if m is None:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    raw = await mastery_detect_struggles(pool, mind_map_id)

    nodes = [
        StrugglingNodeEntry(
            node_id=str(item["id"]),
            node_label=str(item["label"]),
            mastery_score=float(item["mastery_score"]),
            mastery_status=str(item["mastery_status"]),
            reason=str(item["reason"]),
        )
        for item in raw
    ]

    return StrugglingNodesResponse(mind_map_id=mind_map_id, nodes=nodes)


# ---------------------------------------------------------------------------
# PUT /api/education/mind-maps/{id}/status — update mind map status
# ---------------------------------------------------------------------------

_VALID_STATUSES = {"active", "completed", "abandoned"}


@router.put("/mind-maps/{mind_map_id}/status", response_model=MindMapResponse)
async def update_mind_map_status(
    mind_map_id: str,
    request: Request,
    body: StatusUpdateRequest = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
) -> MindMapResponse:
    """Update a mind map's status (active, completed, abandoned)."""
    pool = _pool(db)

    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status: {body.status!r}. Must be one of: {sorted(_VALID_STATUSES)}",
        )

    try:
        await mind_map_update_status(pool, mind_map_id, body.status)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Mind map not found: {mind_map_id}")

    m = await mind_map_get(pool, mind_map_id)

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="education",
        operation="mind_map_status_update",
        method="PUT",
        path=f"/api/education/mind-maps/{mind_map_id}/status",
        path_params={"mind_map_id": mind_map_id},
        body={"status": body.status},
        response_status=200,
        request=request,
    )

    return _map_dict_to_response(m, include_dag=False)


# ---------------------------------------------------------------------------
# Curriculum requests — durable accepted-to-outcome receipts
#
# A dashboard curriculum request is accepted work: the owner is told the butler
# took it, and then a *detached* session does the real work. The receipt is what
# makes that promise falsifiable. Every request gets an immutable row in
# ``education.curriculum_requests`` BEFORE any detached work starts, and the
# task that runs the work settles trigger/session, curriculum, calibration and
# failure evidence back onto that row.
#
# The one-pending-at-a-time guard lives on the same row (partial unique index
# ``uq_curriculum_requests_one_open``), not in the KV store, so there is exactly
# one guard and releasing it is a backend write rather than something an LLM has
# to remember to do. A receipt left non-terminal past ``_RECEIPT_TIMEOUT`` (a
# crashed API process, a session that never returns) is swept to a terminal
# ``failed`` state, so the guard can never strand the owner behind a permanent
# 409.
# ---------------------------------------------------------------------------

# Background tasks fired from the request handler must outlive the response, so we
# hold strong references until they finish (asyncio only keeps weak refs otherwise).
_CURRICULUM_TASKS: set[asyncio.Task] = set()

# How long a receipt may stay non-terminal before it is presumed abandoned. The
# curriculum session (mind map + diagnostic plan) is minutes of work, not tens of
# minutes; past this the owning task is either gone with its process or wedged,
# and either way the owner deserves a terminal answer and a released guard.
_RECEIPT_TIMEOUT = timedelta(minutes=30)

_RECEIPT_OPEN_STATUSES = ("accepted", "running")

# Terminal failure reasons. Stable strings — the UI renders them, so they are
# part of the contract, not log prose.
_FAILURE_TRIGGER_UNREACHABLE = "trigger_unreachable"
_FAILURE_SESSION_ERROR = "session_error"
_FAILURE_NO_CURRICULUM = "no_curriculum_created"
_FAILURE_TIMED_OUT = "timed_out"

_RECEIPT_COLUMNS = """
    id, topic, goal, status, session_id, mind_map_id,
    calibration_ready_at, calibration_notice_outcome, calibration_notice_accepted_at,
    failure_reason,
    requested_at, triggered_at, settled_at, updated_at
"""

# Teaching-flow states at or past the diagnostic probe. Reaching one of these
# means the calibration the owner was promised is actually live and answerable.
_CALIBRATION_READY_FLOW_STATES = frozenset(
    {"diagnosing", "planning", "teaching", "quizzing", "reviewing", "completed"}
)

# The butler this receipt's notify() calls originate from, and therefore the
# ``origin_butler`` the attention ledger files them under.
_NOTIFY_ORIGIN_BUTLER = "education"

# Two outcomes the attention ledger itself can never supply, because they
# describe our attempt to consult it rather than a dispatch it recorded. They
# live in the same column as the ledger's own words so the receipt has exactly
# one field to read, and they are deliberately not delivery-shaped:
#   ``no_record``  we read the ledger for this session and found no notify row.
#   ``unproven``   we could not read it, or had no session ID to read it with.
# A NULL outcome means the question was never asked at all.
_NOTICE_NO_RECORD = "no_record"
_NOTICE_UNPROVEN = "unproven"

# The one ledger outcome that attests the message was accepted by a delivery
# channel. Every other outcome, sentinel included, leaves the receipt silent
# about owner contact.
_NOTICE_ACCEPTED_OUTCOME = "delivered"


def _iso(value: Any) -> str | None:
    """Render a timestamp column as ISO-8601, or None when unset."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _receipt_to_response(row: Any) -> CurriculumRequestReceipt:
    """Convert a ``curriculum_requests`` row to its API model."""
    return CurriculumRequestReceipt(
        request_id=str(row["id"]),
        topic=row["topic"],
        goal=row["goal"],
        status=row["status"],
        session_id=row["session_id"],
        mind_map_id=str(row["mind_map_id"]) if row["mind_map_id"] else None,
        calibration_ready_at=_iso(row["calibration_ready_at"]),
        calibration_notice_outcome=row["calibration_notice_outcome"],
        calibration_notice_accepted_at=_iso(row["calibration_notice_accepted_at"]),
        failure_reason=row["failure_reason"],
        requested_at=_iso(row["requested_at"]) or "",
        triggered_at=_iso(row["triggered_at"]),
        settled_at=_iso(row["settled_at"]),
        updated_at=_iso(row["updated_at"]) or "",
    )


async def _sweep_abandoned_receipts(pool) -> int:
    """Settle receipts that have been non-terminal past ``_RECEIPT_TIMEOUT``.

    Restart safety for the pending guard: the task that owns an in-flight
    receipt lives in the API process, so a restart kills it mid-flight and the
    row would otherwise sit ``running`` forever, holding the one-open guard. This
    sweep runs on every submit and every status read, so recovery needs no
    schedule and no operator.

    Idempotent by construction — it only ever moves open rows to a terminal
    state, so a second run over the same rows matches nothing.
    """
    rows = await pool.fetch(
        f"""
        UPDATE education.curriculum_requests
           SET status = 'failed',
               failure_reason = COALESCE(failure_reason, $1),
               settled_at = now(),
               updated_at = now()
         WHERE status = ANY($2::text[])
           AND requested_at < now() - $3::interval
        RETURNING {_RECEIPT_COLUMNS}
        """,
        _FAILURE_TIMED_OUT,
        list(_RECEIPT_OPEN_STATUSES),
        _RECEIPT_TIMEOUT,
    )
    if rows:
        logger.warning(
            "Swept %d abandoned curriculum request receipt(s) to failed/%s",
            len(rows),
            _FAILURE_TIMED_OUT,
        )
    return len(rows)


async def _create_receipt(pool, topic: str, goal: str | None):
    """Insert the accepted receipt, or return None when one is already open.

    The partial unique index is the guard; a ``UniqueViolationError`` here is the
    concurrent-submit case, not an internal error.
    """
    try:
        return await pool.fetchrow(
            f"""
            INSERT INTO education.curriculum_requests (topic, goal, status)
            VALUES ($1, $2, 'accepted')
            RETURNING {_RECEIPT_COLUMNS}
            """,
            topic,
            goal,
        )
    except asyncpg.UniqueViolationError:
        return None


async def _mark_receipt_running(pool, request_id: str, triggered_at: datetime) -> None:
    """Stamp the receipt as running with the moment the trigger was handed off."""
    await pool.execute(
        """
        UPDATE education.curriculum_requests
           SET status = 'running',
               triggered_at = COALESCE(triggered_at, $2),
               updated_at = now()
         WHERE id = $1::uuid
           AND status = 'accepted'
        """,
        request_id,
        triggered_at,
    )


async def _settle_receipt(
    pool,
    request_id: str,
    *,
    status: str,
    session_id: str | None = None,
    mind_map_id: str | None = None,
    calibration_ready: bool = False,
    notice_outcome: str | None = None,
    notice_accepted_at: datetime | None = None,
    failure_reason: str | None = None,
) -> bool:
    """Settle a receipt onto a terminal state. Returns True if this call settled it.

    Idempotent: the ``status = ANY(open)`` predicate makes the first terminal
    write win, so a retry, a duplicate callback, or a late task racing the
    abandonment sweep is a no-op rather than a second (possibly contradictory)
    outcome. Evidence columns are ``COALESCE``-merged so a settle never blanks
    evidence an earlier write already recorded.

    ``notice_outcome`` and ``notice_accepted_at`` are written as one unit: the
    caller decides both from the same piece of ledger evidence, and the table's
    CHECK requires the timestamp to be present exactly when the outcome is
    ``delivered``. Splitting them across two writes could pair a stale timestamp
    with a fresh outcome, which is the overclaim this column pair exists to
    prevent, so passing ``notice_outcome=None`` leaves both untouched.
    """
    row = await pool.fetchrow(
        """
        UPDATE education.curriculum_requests
           SET status = $2,
               session_id = COALESCE($3, session_id),
               mind_map_id = COALESCE($4::uuid, mind_map_id),
               calibration_ready_at = CASE
                   WHEN $5 THEN COALESCE(calibration_ready_at, now())
                   ELSE calibration_ready_at
               END,
               calibration_notice_outcome = COALESCE($6, calibration_notice_outcome),
               calibration_notice_accepted_at = CASE
                   WHEN $6 IS NULL THEN calibration_notice_accepted_at
                   ELSE $7
               END,
               failure_reason = CASE WHEN $2 = 'failed' THEN $8 ELSE NULL END,
               settled_at = now(),
               updated_at = now()
         WHERE id = $1::uuid
           AND status = ANY($9::text[])
        RETURNING id
        """,
        request_id,
        status,
        session_id,
        mind_map_id,
        calibration_ready,
        notice_outcome,
        notice_accepted_at,
        failure_reason,
        list(_RECEIPT_OPEN_STATUSES),
    )
    return row is not None


async def _get_receipt(pool, request_id: str):
    """Read one receipt by its immutable request ID."""
    return await pool.fetchrow(
        f"SELECT {_RECEIPT_COLUMNS} FROM education.curriculum_requests WHERE id = $1::uuid",
        request_id,
    )


async def _latest_receipt(pool):
    """Read the most recently requested receipt, if any."""
    return await pool.fetchrow(
        f"""
        SELECT {_RECEIPT_COLUMNS}
          FROM education.curriculum_requests
         ORDER BY requested_at DESC
         LIMIT 1
        """
    )


def _curriculum_prompt(topic: str, goal: str | None) -> str:
    """Build the prompt for the ephemeral session that starts the curriculum.

    Triggered directly when the request is submitted (event-driven; there is no
    polling drain schedule). The session only has to do the teaching work — the
    request's lifecycle is settled by the backend from the trigger result, so
    nothing here depends on the session remembering to release a lock.
    """
    goal_line = f"Goal: {goal}" if goal else "Goal: (none specified)"
    return f"""\
A user submitted a curriculum request from the dashboard. Start it now.

Topic: {topic}
{goal_line}

1) Avoid duplicates: call mind_map_list(status="active"). If an active map already
   covers this topic, prefer extending it over creating a new one (see the
   curriculum-planning skill); do NOT create a redundant map.
2) Actually start the curriculum:
   teaching_flow_start(topic="{topic}", goal=<goal above, or omit if none>)
   This creates the mind map and advances the flow to DIAGNOSING.
3) Tell the user it started:
   notify(channel="telegram", intent="send",
          message="Starting your {topic} curriculum. I'll run a quick calibration
          first to see what you already know — answer when you're ready.")

If teaching_flow_start errors, report the error and exit — do not fabricate a
started curriculum. The dashboard reads the outcome from this session's result,
so an honest failure here is what lets the user retry.
"""


def _parse_trigger_result(result: Any) -> dict[str, Any]:
    """Extract ``{success, error, session_id}`` from an MCP ``trigger`` result.

    Mirrors the parsing in ``src/butlers/api/routers/butlers.py::trigger_butler``.
    An unparseable payload is reported as a success with no session ID rather
    than as a failure — the session may well have run; what we lack is evidence
    about it, and the curriculum correlation below is the authority on outcome.
    """
    parsed: dict[str, Any] = {"success": True, "error": None, "session_id": None}

    content = getattr(result, "content", None)
    if content:
        text = getattr(content[0], "text", "") or ""
        if text:
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, AttributeError):
                data = None
            if isinstance(data, dict):
                parsed["success"] = bool(data.get("success", True))
                parsed["error"] = data.get("error")
                sid = data.get("session_id")
                parsed["session_id"] = str(sid) if sid else None

    if getattr(result, "is_error", False):
        parsed["success"] = False

    return parsed


async def _correlate_curriculum(pool, triggered_at: datetime) -> tuple[str | None, bool]:
    """Find the curriculum the triggered session created, and whether it is calibrating.

    Correlation is by creation window: the session was handed the request at
    ``triggered_at``, and the pending guard means no other dashboard request can
    be starting a curriculum in parallel, so the newest mind map created at or
    after that instant is the one this request produced. Returns
    ``(mind_map_id, calibration_ready)`` — ``(None, False)`` when the session
    finished without creating one.

    ``calibration_ready`` reads the teaching flow's own state: the diagnostic
    probe the owner was promised is only real once the flow has reached
    ``diagnosing`` or beyond.
    """
    row = await pool.fetchrow(
        """
        SELECT id
          FROM education.mind_maps
         WHERE created_at >= $1
         ORDER BY created_at DESC
         LIMIT 1
        """,
        triggered_at,
    )
    if row is None:
        return None, False

    mind_map_id = str(row["id"])

    calibration_ready = False
    try:
        flow = await state_get(pool, f"flow:{mind_map_id}")
    except Exception:
        logger.exception("Failed to read teaching flow state for mind map %s", mind_map_id)
    else:
        if isinstance(flow, dict):
            calibration_ready = flow.get("status") in _CALIBRATION_READY_FLOW_STATES

    return mind_map_id, calibration_ready


async def _notice_evidence(
    pool,
    session_id: str | None,
    since: datetime,
) -> tuple[str, datetime | None]:
    """Ask the notification path what became of this session's calibration notice.

    Returns ``(outcome, accepted_at)`` for :func:`_settle_receipt`. The outcome
    is the attention ledger's own word for the dispatch, and ``accepted_at`` is
    non-None only for ``delivered`` — the one outcome the ledger writes after a
    delivery channel accepted the message. Everything else, including our own
    two sentinels, returns ``None`` and leaves the receipt claiming nothing
    about owner contact.

    Deliberately never consults the teaching flow. Flow state proves calibration
    began; only the ledger can speak to whether the owner was told, and the two
    diverge in exactly the case that matters (bu-358jk).
    """
    if pool is None or not session_id:
        # No pool, or no correlation key, so the ledger cannot be asked about
        # this session
        # specifically. Guessing by butler and time window could credit an
        # unrelated education notify, which is how "unproven" becomes a false
        # "delivered". Note this is `unproven`, not `no_record`: we did not
        # look, so we cannot report an absence.
        return _NOTICE_UNPROVEN, None

    try:
        evidence = await find_notify_dispatch_for_session(
            pool,
            origin_butler=_NOTIFY_ORIGIN_BUTLER,
            session_id=session_id,
            since=since,
        )
    except Exception:
        logger.exception(
            "Failed to read notify dispatch evidence for curriculum session %s", session_id
        )
        return _NOTICE_UNPROVEN, None

    if evidence is None:
        # ``record_attention_event`` is best-effort and never raises, so an
        # absent row is not proof the notice failed. It is proof we have no
        # proof, which is a different claim and gets a different word.
        return _NOTICE_NO_RECORD, None

    if evidence.outcome == _NOTICE_ACCEPTED_OUTCOME:
        return evidence.outcome, evidence.occurred_at
    return evidence.outcome, None


async def _run_curriculum_request(
    mcp_manager: MCPClientManager,
    pool,
    request_id: str,
    topic: str,
    goal: str | None,
) -> None:
    """Run the accepted curriculum request and settle its receipt.

    Detached from the POST so the handler can return 202 without blocking on the
    (slow) session spawn — but unlike a fire-and-forget trigger, every exit path
    here lands a terminal state on the receipt. ``trigger`` is awaited to
    completion, so its result carries the session ID and the session's own
    success/failure, and the curriculum correlation below turns "the session
    said it worked" into "a curriculum exists".

    The receipt's notice evidence is read from the attention ledger, never from
    the teaching flow: a live calibration and a notice that never reached the
    owner are independent facts, and the receipt must be able to say so.
    """
    triggered_at = datetime.now(UTC)
    try:
        await _mark_receipt_running(pool, request_id, triggered_at)
    except Exception:
        logger.exception("Failed to mark curriculum request %s as running", request_id)

    session_id: str | None = None
    try:
        client = await mcp_manager.get_client("education")
        result = await client.call_tool(
            "trigger",
            {"prompt": _curriculum_prompt(topic, goal), "complexity": "workhorse"},
        )
    except Exception as exc:
        logger.exception(
            "Failed to trigger curriculum session for request %s (topic %r)", request_id, topic
        )
        await _settle_failed(pool, request_id, _FAILURE_TRIGGER_UNREACHABLE, session_id, exc)
        return

    parsed = _parse_trigger_result(result)
    session_id = parsed["session_id"]
    if not parsed["success"]:
        logger.warning(
            "Curriculum session for request %s reported failure: %s",
            request_id,
            parsed["error"],
        )
        await _settle_failed(pool, request_id, _FAILURE_SESSION_ERROR, session_id, None)
        return

    try:
        mind_map_id, calibration_ready = await _correlate_curriculum(pool, triggered_at)
    except Exception:
        logger.exception("Failed to correlate curriculum for request %s", request_id)
        mind_map_id, calibration_ready = None, False

    if mind_map_id is None:
        # The session ran and claimed success but produced no curriculum (a
        # duplicate-topic skip, or a silent failure). "Accepted" must not become
        # "done" on the strength of a session exiting cleanly.
        await _settle_failed(pool, request_id, _FAILURE_NO_CURRICULUM, session_id, None)
        return

    notice_outcome, notice_accepted_at = await _notice_evidence(pool, session_id, triggered_at)

    try:
        await _settle_receipt(
            pool,
            request_id,
            status="completed",
            session_id=session_id,
            mind_map_id=mind_map_id,
            calibration_ready=calibration_ready,
            notice_outcome=notice_outcome,
            notice_accepted_at=notice_accepted_at,
        )
    except Exception:
        logger.exception("Failed to settle curriculum request %s as completed", request_id)


async def _settle_failed(
    pool,
    request_id: str,
    reason: str,
    session_id: str | None,
    exc: BaseException | None,
) -> None:
    """Settle a receipt as failed, never letting the settle itself throw.

    A settle that raises would strand the receipt open and hold the pending
    guard — exactly the failure mode the receipt exists to remove.
    """
    del exc  # reason is the contract; the traceback is already logged
    try:
        await _settle_receipt(
            pool,
            request_id,
            status="failed",
            session_id=session_id,
            failure_reason=reason,
        )
    except Exception:
        logger.exception("Failed to settle curriculum request %s as failed/%s", request_id, reason)


def _receipts_unavailable(exc: BaseException) -> bool:
    """True when the receipt store is legitimately absent rather than broken.

    A pre-migration education chain has no ``curriculum_requests`` table. That is
    an explicit "status unavailable", not a failure to report as an outage — and
    not something to render as "no request in flight".
    """
    return isinstance(exc, asyncpg.UndefinedTableError)


@router.post(
    "/curriculum-requests",
    response_model=CurriculumRequestResponse,
    status_code=202,
)
async def submit_curriculum_request(
    request: Request,
    body: CurriculumRequestBody = Body(...),
    db: DatabaseManager = Depends(_get_db_manager),
    mcp_manager: MCPClientManager = Depends(get_mcp_manager),
) -> CurriculumRequestResponse:
    """Accept a curriculum request and return its durable receipt ID.

    202 means *accepted and recorded* — never *set up*. The receipt row exists
    before the detached curriculum work starts, and
    ``GET /curriculum-requests/{request_id}`` is the only place a completion
    claim may come from.
    """
    pool = _pool(db)

    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Topic must not be empty")
    if len(topic) > 200:
        raise HTTPException(status_code=422, detail="Topic must be 200 characters or fewer")
    if body.goal is not None and len(body.goal) > 500:
        raise HTTPException(status_code=422, detail="Goal must be 500 characters or fewer")

    try:
        # Release any receipt whose owning task died (e.g. an API restart) before
        # testing the guard, so a crash can never wedge the owner at 409.
        await _sweep_abandoned_receipts(pool)
        receipt = await _create_receipt(pool, topic, body.goal)
    except Exception as exc:
        if _receipts_unavailable(exc):
            raise HTTPException(
                status_code=503,
                detail="Curriculum request receipts are unavailable"
                " — the education database is not migrated",
            )
        raise

    if receipt is None:
        raise HTTPException(
            status_code=409,
            detail="A curriculum request is already pending"
            " — please wait for the butler to process it",
        )

    request_id = str(receipt["id"])

    # Fire the curriculum-start session in the background; return 202 without
    # waiting for the (slow) session to complete. The task settles the receipt.
    task = asyncio.create_task(
        _run_curriculum_request(mcp_manager, pool, request_id, topic, body.goal)
    )
    _CURRICULUM_TASKS.add(task)
    task.add_done_callback(_CURRICULUM_TASKS.discard)

    # Explicit audit — middleware also fires; this carries the semantic operation label.
    await emit_dashboard_audit(
        db,
        butler="education",
        operation="curriculum_request_create",
        method="POST",
        path="/api/education/curriculum-requests",
        body={"topic": topic, "request_id": request_id},
        response_status=202,
        request=request,
    )

    return CurriculumRequestResponse(status="accepted", topic=topic, request_id=request_id)


# ---------------------------------------------------------------------------
# GET /api/education/curriculum-requests/latest — most recent receipt
# ---------------------------------------------------------------------------


@router.get("/curriculum-requests/latest", response_model=CurriculumRequestStatusResponse)
async def get_latest_curriculum_request(
    db: DatabaseManager = Depends(_get_db_manager),
) -> CurriculumRequestStatusResponse:
    """Read the most recent curriculum request receipt.

    ``receipt: null`` with ``receipts_available: true`` means no request has ever
    been made. ``receipts_available: false`` means the store could not be read —
    which the UI must render as unknown, not as "nothing in flight".
    """
    pool = _pool(db)

    try:
        await _sweep_abandoned_receipts(pool)
        row = await _latest_receipt(pool)
    except Exception as exc:
        if _receipts_unavailable(exc):
            return CurriculumRequestStatusResponse(receipts_available=False, receipt=None)
        raise

    return CurriculumRequestStatusResponse(
        receipts_available=True,
        receipt=_receipt_to_response(row) if row is not None else None,
    )


# ---------------------------------------------------------------------------
# GET /api/education/curriculum-requests/{request_id} — one receipt
# ---------------------------------------------------------------------------


@router.get("/curriculum-requests/{request_id}", response_model=CurriculumRequestStatusResponse)
async def get_curriculum_request(
    request_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> CurriculumRequestStatusResponse:
    """Read one curriculum request receipt by its immutable request ID."""
    pool = _pool(db)

    try:
        await _sweep_abandoned_receipts(pool)
        row = await _get_receipt(pool, request_id)
    except Exception as exc:
        if _receipts_unavailable(exc):
            return CurriculumRequestStatusResponse(receipts_available=False, receipt=None)
        if isinstance(exc, asyncpg.DataError):
            raise HTTPException(
                status_code=422, detail=f"Malformed curriculum request ID: {request_id}"
            )
        raise

    if row is None:
        raise HTTPException(status_code=404, detail=f"Curriculum request not found: {request_id}")

    return CurriculumRequestStatusResponse(
        receipts_available=True, receipt=_receipt_to_response(row)
    )
