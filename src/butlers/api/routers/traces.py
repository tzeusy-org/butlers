"""Trace endpoints — cross-butler distributed trace aggregation.

Provides:

- ``router`` — trace endpoints at ``GET /api/traces`` and ``GET /api/traces/{trace_id}``

A trace is a collection of sessions sharing the same ``trace_id``. Sessions
are linked via ``parent_session_id`` to form a span tree. The trace API
aggregates sessions from all butler databases using ``DatabaseManager.fan_out()``.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse, PaginatedResponse, PaginationMeta
from butlers.api.models.trace import SpanNode, TraceDetail, TraceSummary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/traces", tags=["traces"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


def _determine_trace_status(spans: list[dict]) -> str:
    """Determine the overall status of a trace from its spans.

    Returns one of: "success", "failed", "running", "partial".

    - "running" if any span has success=None (still in progress)
    - "failed" if all completed spans failed
    - "success" if all completed spans succeeded
    - "partial" if there is a mix of success and failure among completed spans
    """
    if not spans:
        return "running"

    completed = [s for s in spans if s.get("success") is not None]

    if not completed:
        return "running"

    successes = sum(1 for s in completed if s["success"])
    failures = len(completed) - successes

    # If some spans are still running, factor that in
    has_running = len(completed) < len(spans)

    if has_running:
        if failures > 0:
            return "partial"
        return "running"

    if failures == 0:
        return "success"
    if successes == 0:
        return "failed"
    return "partial"


def assemble_span_tree(sessions: list[dict]) -> list[SpanNode]:
    """Build a tree of SpanNode from a flat list of session dicts.

    Each session dict must include at minimum: id, butler, prompt,
    trigger_source, started_at, and optionally: success, completed_at,
    duration_ms, model, input_tokens, output_tokens, parent_session_id.

    Algorithm:
    1. Create a SpanNode for each session
    2. Index nodes by their id
    3. For each node with a parent_session_id, add it to its parent's children
    4. Root nodes are those with parent_session_id=None
    5. Sort each node's children by started_at
    """
    # Step 1: Create SpanNode for each session
    nodes: dict[UUID, SpanNode] = {}
    for session in sessions:
        node = SpanNode(
            id=session["id"],
            butler=session["butler"],
            prompt=session["prompt"],
            trigger_source=session["trigger_source"],
            success=session.get("success"),
            started_at=session["started_at"],
            completed_at=session.get("completed_at"),
            duration_ms=session.get("duration_ms"),
            model=session.get("model"),
            input_tokens=session.get("input_tokens"),
            output_tokens=session.get("output_tokens"),
            parent_session_id=session.get("parent_session_id"),
        )
        nodes[node.id] = node

    # Step 2-3: Link children to parents
    roots: list[SpanNode] = []
    for node in nodes.values():
        if node.parent_session_id is not None and node.parent_session_id in nodes:
            nodes[node.parent_session_id].children.append(node)
        else:
            # Root node (parent_session_id is None or parent not found in this trace)
            roots.append(node)

    # Step 4-5: Sort children and roots by started_at
    def _sort_children(node: SpanNode) -> None:
        node.children.sort(key=lambda n: n.started_at)
        for child in node.children:
            _sort_children(child)

    for root in roots:
        _sort_children(root)

    roots.sort(key=lambda n: n.started_at)
    return roots


# ---------------------------------------------------------------------------
# GET /api/traces — cross-butler trace list
# ---------------------------------------------------------------------------


@router.get("", response_model=PaginatedResponse[TraceSummary])
async def list_traces(
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(50, ge=1, le=200, description="Max records to return"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> PaginatedResponse[TraceSummary]:
    """Return paginated trace summaries aggregated across all butler databases.

    Uses ``DatabaseManager.fan_out()`` to query every registered butler DB
    concurrently for unique trace_ids with aggregated metadata, then merges
    and paginates the combined results.
    """
    # Query each butler for trace summaries
    trace_sql = """
        SELECT
            trace_id,
            count(*) AS span_count,
            min(started_at) AS started_at,
            sum(duration_ms) AS total_duration_ms,
            bool_and(success) AS all_success,
            bool_or(success IS NULL) AS has_running,
            bool_or(success = false) AS has_failure
        FROM sessions
        WHERE trace_id IS NOT NULL
        GROUP BY trace_id
    """

    results = await db.fan_out(trace_sql)

    # Aggregate across butlers: merge by trace_id
    # For each trace_id, we need to combine data from multiple butlers
    trace_map: dict[str, dict] = {}

    for butler_name, rows in results.items():
        for row in rows:
            tid = row["trace_id"]
            if tid not in trace_map:
                trace_map[tid] = {
                    "trace_id": tid,
                    "root_butler": butler_name,
                    "span_count": 0,
                    "total_duration_ms": 0,
                    "started_at": row["started_at"],
                    "has_running": False,
                    "has_failure": False,
                    "all_success": True,
                }

            entry = trace_map[tid]
            entry["span_count"] += row["span_count"]

            if row["total_duration_ms"] is not None:
                entry["total_duration_ms"] = (entry["total_duration_ms"] or 0) + row[
                    "total_duration_ms"
                ]

            # Track the earliest started_at as the trace start
            if row["started_at"] < entry["started_at"]:
                entry["started_at"] = row["started_at"]
                entry["root_butler"] = butler_name

            # Merge status flags
            if row["has_running"]:
                entry["has_running"] = True
            if row["has_failure"]:
                entry["has_failure"] = True
            if not row["all_success"]:
                entry["all_success"] = False

    # Build summaries with status determination
    summaries: list[TraceSummary] = []
    for entry in trace_map.values():
        if entry["has_running"]:
            if entry["has_failure"]:
                status = "partial"
            else:
                status = "running"
        elif entry["has_failure"]:
            if entry["all_success"]:
                # Some butlers succeeded, some failed
                status = "partial"
            else:
                status = "failed" if not entry.get("has_success_anywhere") else "partial"
                # Refine: if all_success is False and has_failure, check if there are successes
                # all_success being False just means not ALL succeeded
                # We need to know if ANY succeeded
                status = "failed"
        else:
            status = "success"

        # Re-derive status more cleanly
        if entry["has_running"]:
            status = "partial" if entry["has_failure"] else "running"
        elif not entry["has_failure"]:
            status = "success"
        elif entry["all_success"]:
            # all_success is True at this butler but has_failure from another
            status = "partial"
        else:
            # all_success is False — could be mixed or all failed
            status = "failed"

        summaries.append(
            TraceSummary(
                trace_id=entry["trace_id"],
                root_butler=entry["root_butler"],
                span_count=entry["span_count"],
                total_duration_ms=entry["total_duration_ms"] or None,
                started_at=entry["started_at"],
                status=status,
            )
        )

    # Sort by started_at descending (newest first)
    summaries.sort(key=lambda s: s.started_at, reverse=True)

    total = len(summaries)
    page = summaries[offset : offset + limit]

    return PaginatedResponse[TraceSummary](
        data=page,
        meta=PaginationMeta(total=total, offset=offset, limit=limit),
    )


# ---------------------------------------------------------------------------
# GET /api/traces/{trace_id} — trace detail with span tree
# ---------------------------------------------------------------------------


@router.get("/{trace_id}", response_model=ApiResponse[TraceDetail])
async def get_trace(
    trace_id: str,
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[TraceDetail]:
    """Return full trace detail with assembled span tree.

    Fans out to all butler databases to fetch sessions matching the given
    trace_id, then assembles them into a tree based on parent_session_id
    relationships.
    """
    session_sql = """
        SELECT
            id, prompt, trigger_source, success, started_at, completed_at,
            duration_ms, model, input_tokens, output_tokens, parent_session_id
        FROM sessions
        WHERE trace_id = $1
        ORDER BY started_at
    """

    results = await db.fan_out(session_sql, (trace_id,))

    # Collect all sessions with butler name attached
    all_sessions: list[dict] = []
    for butler_name, rows in results.items():
        for row in rows:
            all_sessions.append(
                {
                    "id": row["id"],
                    "butler": butler_name,
                    "prompt": row["prompt"],
                    "trigger_source": row["trigger_source"],
                    "success": row["success"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "duration_ms": row["duration_ms"],
                    "model": row["model"],
                    "input_tokens": row["input_tokens"],
                    "output_tokens": row["output_tokens"],
                    "parent_session_id": row["parent_session_id"],
                }
            )

    if not all_sessions:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found")

    # Determine root butler (earliest session)
    all_sessions.sort(key=lambda s: s["started_at"])
    root_butler = all_sessions[0]["butler"]

    # Calculate totals
    total_duration = sum(s["duration_ms"] for s in all_sessions if s["duration_ms"] is not None)
    status = _determine_trace_status(all_sessions)

    # Assemble span tree
    spans = assemble_span_tree(all_sessions)

    detail = TraceDetail(
        trace_id=trace_id,
        root_butler=root_butler,
        span_count=len(all_sessions),
        total_duration_ms=total_duration or None,
        started_at=all_sessions[0]["started_at"],
        status=status,
        spans=spans,
    )

    return ApiResponse[TraceDetail](data=detail)
