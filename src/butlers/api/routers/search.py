"""Search endpoint — cross-butler fan-out ILIKE search.

Provides:

- ``router`` — search endpoint at ``GET /api/search``

Searches sessions (prompt, result) and state (key, value::text) across all
butler databases using ``DatabaseManager.fan_out()``. Returns grouped
results with butler name, matched field, and text snippets.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from butlers.api.db import DatabaseManager
from butlers.api.models.search import SearchResponse, SearchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Snippet helper
# ---------------------------------------------------------------------------


def _extract_snippet(text: str, query: str, max_len: int = 200) -> str:
    """Extract a snippet around the first occurrence of the query string.

    Returns at most ``max_len`` characters centered around the match.
    """
    if not text:
        return ""

    lower_text = text.lower()
    lower_query = query.lower()
    idx = lower_text.find(lower_query)

    if idx == -1:
        # No direct match (could be pattern match); return start of text
        return text[:max_len] + ("..." if len(text) > max_len else "")

    # Center the snippet around the match
    half = max_len // 2
    start = max(0, idx - half)
    end = min(len(text), idx + len(query) + half)

    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."

    return snippet


# ---------------------------------------------------------------------------
# GET /api/search — cross-butler fan-out search
# ---------------------------------------------------------------------------


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query("", description="Search query (ILIKE pattern)"),
    limit: int = Query(20, ge=1, le=100, description="Max results per category"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> SearchResponse:
    """Search across all butler databases for sessions and state entries.

    Fans out ILIKE queries to every registered butler database. Sessions are
    searched by ``prompt`` and ``result`` columns; state entries by ``key``
    and ``value::text``.

    Returns grouped results: ``{sessions: [...], state: [...]}``.
    """
    if not q.strip():
        return SearchResponse()

    pattern = f"%{q}%"
    session_results: list[SearchResult] = []
    state_results: list[SearchResult] = []

    # --- Sessions search ---
    session_sql = """
        SELECT id, prompt, result, trigger_source, success, started_at,
               duration_ms,
               CASE
                   WHEN prompt ILIKE $1 THEN 'prompt'
                   ELSE 'result'
               END AS matched_field
        FROM sessions
        WHERE prompt ILIKE $1 OR result ILIKE $1
        ORDER BY started_at DESC
        LIMIT $2
    """

    session_fan_out = await db.fan_out(session_sql, (pattern, limit))

    for butler_name, rows in session_fan_out.items():
        for row in rows:
            matched_field = row["matched_field"]
            source_text = row["prompt"] if matched_field == "prompt" else (row["result"] or "")
            snippet = _extract_snippet(source_text, q)

            session_results.append(
                SearchResult(
                    butler=butler_name,
                    matched_field=matched_field,
                    snippet=snippet,
                    data={
                        "id": str(row["id"]),
                        "prompt": row["prompt"],
                        "trigger_source": row["trigger_source"],
                        "success": row["success"],
                        "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                        "duration_ms": row["duration_ms"],
                    },
                )
            )

    # --- State search ---
    state_sql = """
        SELECT key, value::text AS value_text, updated_at,
               CASE
                   WHEN key ILIKE $1 THEN 'key'
                   ELSE 'value'
               END AS matched_field
        FROM state
        WHERE key ILIKE $1 OR value::text ILIKE $1
        ORDER BY updated_at DESC
        LIMIT $2
    """

    state_fan_out = await db.fan_out(state_sql, (pattern, limit))

    for butler_name, rows in state_fan_out.items():
        for row in rows:
            matched_field = row["matched_field"]
            source_text = row["key"] if matched_field == "key" else (row["value_text"] or "")
            snippet = _extract_snippet(source_text, q)

            state_results.append(
                SearchResult(
                    butler=butler_name,
                    matched_field=matched_field,
                    snippet=snippet,
                    data={
                        "key": row["key"],
                        "value": row["value_text"],
                        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                    },
                )
            )

    # Trim to limit (fan_out may return limit per butler, need global trim)
    return SearchResponse(
        sessions=session_results[:limit],
        state=state_results[:limit],
    )
