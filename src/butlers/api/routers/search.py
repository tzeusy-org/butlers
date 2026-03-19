"""Search endpoint — cross-butler fan-out ILIKE search.

Provides:

- ``router`` — search endpoint at ``GET /api/search``

Searches sessions (prompt, result) and state (key, value::text) across all
butler databases using ``DatabaseManager.fan_out()``.  Also searches
entities and contacts in the shared schema.  Returns grouped results with
id, butler name, type, title, snippet, and navigation URL.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.models.search import SearchResponse, SearchResult

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/search", tags=["search"])


def _get_db_manager() -> DatabaseManager:
    """Dependency stub — overridden at app startup or in tests."""
    raise RuntimeError("DatabaseManager not initialized")


# ---------------------------------------------------------------------------
# Pool helper — get any pool for shared-schema queries
# ---------------------------------------------------------------------------


def _any_pool(db: DatabaseManager) -> object:
    """Return any available pool for querying shared schema tables."""
    for name in db.butler_names:
        try:
            return db.pool(name)
        except KeyError:
            continue
    raise RuntimeError("No database pools available for shared-schema query")


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


@router.get("", response_model=ApiResponse[SearchResponse])
async def search(
    q: str = Query("", description="Search query (ILIKE pattern)"),
    limit: int = Query(20, ge=1, le=100, description="Max results per category"),
    db: DatabaseManager = Depends(_get_db_manager),
) -> ApiResponse[SearchResponse]:
    """Search across butler databases and shared schema.

    Searches:
    - **entities** — canonical name and aliases in ``shared.entities``
    - **contacts** — name, email, and phone in ``shared.contacts`` / ``shared.contact_info``
    - **sessions** — prompt and result columns across all butler databases
    - **state** — key and value columns across all butler databases

    Returns grouped results: ``{entities: [...], contacts: [...], sessions: [...], state: [...]}``.
    """
    if not q.strip():
        return ApiResponse[SearchResponse](data=SearchResponse())

    pattern = f"%{q}%"
    entity_results: list[SearchResult] = []
    contact_results: list[SearchResult] = []
    session_results: list[SearchResult] = []
    state_results: list[SearchResult] = []

    # --- Entities search (shared schema) ---
    try:
        pool = _any_pool(db)
        entity_rows = await pool.fetch(
            "SELECT e.id, e.canonical_name, e.entity_type, e.aliases"
            " FROM shared.entities e"
            " WHERE (e.metadata->>'merged_into') IS NULL"
            "   AND (e.metadata->>'deleted_at') IS NULL"
            "   AND ("
            "     e.canonical_name ILIKE $1"
            "     OR EXISTS ("
            "       SELECT 1 FROM unnest(e.aliases) AS a WHERE a ILIKE $1"
            "     )"
            "   )"
            " ORDER BY e.canonical_name"
            " LIMIT $2",
            pattern,
            limit,
        )
        for row in entity_rows:
            aliases = list(row["aliases"]) if row["aliases"] else []
            alias_text = ", ".join(aliases[:3])
            snippet = row["entity_type"]
            if alias_text:
                snippet += f" · {alias_text}"
            entity_results.append(
                SearchResult(
                    id=str(row["id"]),
                    butler="memory",
                    type="entity",
                    title=row["canonical_name"],
                    snippet=snippet,
                    url=f"/entities/{row['id']}",
                )
            )
    except Exception:
        logger.warning("Entity search failed", exc_info=True)

    # --- Contacts search (shared schema) ---
    try:
        pool = _any_pool(db)
        contact_rows = await pool.fetch(
            "SELECT DISTINCT ON (c.id) c.id, c.name,"
            "  (SELECT ci.value FROM shared.contact_info ci"
            "   WHERE ci.contact_id = c.id AND ci.type = 'email'"
            "     AND NOT ci.secured"
            "   ORDER BY ci.is_primary DESC LIMIT 1) AS email,"
            "  (SELECT ci.value FROM shared.contact_info ci"
            "   WHERE ci.contact_id = c.id AND ci.type = 'phone'"
            "     AND NOT ci.secured"
            "   ORDER BY ci.is_primary DESC LIMIT 1) AS phone"
            " FROM shared.contacts c"
            " LEFT JOIN shared.contact_info ci"
            "   ON ci.contact_id = c.id AND NOT ci.secured"
            " WHERE c.archived_at IS NULL"
            "   AND (c.name ILIKE $1 OR ci.value ILIKE $1)"
            " ORDER BY c.id, c.name"
            " LIMIT $2",
            pattern,
            limit,
        )
        for row in contact_rows:
            parts = []
            if row["email"]:
                parts.append(row["email"])
            if row["phone"]:
                parts.append(row["phone"])
            snippet = " · ".join(parts) if parts else ""
            contact_results.append(
                SearchResult(
                    id=str(row["id"]),
                    butler="relationship",
                    type="contact",
                    title=row["name"] or "Unnamed",
                    snippet=snippet,
                    url=f"/contacts/{row['id']}",
                )
            )
    except Exception:
        logger.warning("Contact search failed", exc_info=True)

    # --- Sessions search (per-butler fan-out) ---
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
                    id=str(row["id"]),
                    butler=butler_name,
                    type="session",
                    title=row["prompt"][:120] if row["prompt"] else "Session",
                    snippet=snippet,
                    url=f"/sessions/{row['id']}",
                )
            )

    # --- State search (per-butler fan-out) ---
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
                    id=f"{butler_name}:{row['key']}",
                    butler=butler_name,
                    type="state",
                    title=row["key"],
                    snippet=snippet,
                    url=f"/butlers/{butler_name}",
                )
            )

    # Trim to limit (fan_out may return limit per butler, need global trim)
    return ApiResponse[SearchResponse](
        data=SearchResponse(
            entities=entity_results[:limit],
            contacts=contact_results[:limit],
            sessions=session_results[:limit],
            state=state_results[:limit],
        )
    )
