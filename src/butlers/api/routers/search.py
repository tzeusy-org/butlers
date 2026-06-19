"""Search endpoint — cross-butler fan-out ILIKE search.

Provides:

- ``router`` — search endpoint at ``GET /api/search``

Searches sessions (prompt, result) and state (key, value::text) across all
butler databases using the ``search_v1`` read-model boundary.  Also searches
entities and contacts (person entities) in the shared schema.  Returns grouped
results with id, butler name, type, title, snippet, and navigation URL.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from butlers.api.db import DatabaseManager
from butlers.api.models import ApiResponse
from butlers.api.models.search import SearchResponse, SearchResult
from butlers.api.read_models.search_v1 import (
    query_contact_search,
    query_entity_search,
    query_session_search,
    query_state_search,
)

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
    - **entities** — canonical name and aliases in ``public.entities``
    - **contacts** — person entities by canonical name, email, and phone via
      ``public.entities`` / ``relationship.entity_facts``
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

    # --- Entities search (shared schema) via search_v1 read-model ---
    pool = _any_pool(db)
    entity_rows = await query_entity_search(pool, pattern, limit)
    for entity_row in entity_rows:
        alias_text = ", ".join(entity_row.aliases[:3])
        snippet = entity_row.entity_type or ""
        if alias_text:
            snippet += f" · {alias_text}"
        entity_results.append(
            SearchResult(
                id=str(entity_row.id),
                butler="memory",
                type="entity",
                title=entity_row.canonical_name,
                snippet=snippet,
                url=f"/entities/{entity_row.id}",
            )
        )

    # --- Contacts search (shared schema) via search_v1 read-model ---
    # Channel identifiers now come from relationship.entity_facts (bu-hjo3i).
    contact_rows = await query_contact_search(pool, pattern, limit)
    for contact_row in contact_rows:
        parts = []
        if contact_row.email:
            parts.append(contact_row.email)
        if contact_row.phone:
            parts.append(contact_row.phone)
        snippet = " · ".join(parts) if parts else ""
        contact_results.append(
            SearchResult(
                id=str(contact_row.id),
                butler="relationship",
                type="contact",
                title=contact_row.name or "Unnamed",
                snippet=snippet,
                url=f"/contacts/{contact_row.id}",
            )
        )

    # --- Sessions search (per-butler fan-out) via search_v1 read-model ---
    session_fan_out = await query_session_search(db, pattern, limit)
    for butler_name, rows in session_fan_out.items():
        for session_row in rows:
            source_text = (
                session_row.prompt
                if session_row.matched_field == "prompt"
                else (session_row.result or "")
            )
            snippet = _extract_snippet(source_text, q)
            session_results.append(
                SearchResult(
                    id=str(session_row.id),
                    butler=butler_name,
                    type="session",
                    title=session_row.prompt[:120] if session_row.prompt else "Session",
                    snippet=snippet,
                    url=f"/sessions/{session_row.id}",
                )
            )

    # --- State search (per-butler fan-out) via search_v1 read-model ---
    state_fan_out = await query_state_search(db, pattern, limit)
    for butler_name, rows in state_fan_out.items():
        for state_row in rows:
            source_text = (
                state_row.key if state_row.matched_field == "key" else (state_row.value_text or "")
            )
            snippet = _extract_snippet(source_text, q)
            state_results.append(
                SearchResult(
                    id=f"{butler_name}:{state_row.key}",
                    butler=butler_name,
                    type="state",
                    title=state_row.key,
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
