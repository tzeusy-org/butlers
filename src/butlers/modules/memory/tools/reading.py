"""Memory reading tools — search, recall, and retrieve."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _search, _serialize_row, _storage

logger = logging.getLogger(__name__)


async def memory_search(
    pool: Pool,
    embedding_engine,
    query: str,
    *,
    types: list[str] | None = None,
    scope: str | None = None,
    mode: str = "hybrid",
    limit: int = 10,
    min_confidence: float = 0.2,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Search across memory types using hybrid, semantic, or keyword mode.

    Delegates to _search.search() and serializes the results for JSON output.

    Args:
        filters: Optional dict of AND-conditions applied at the search layer.
            Supported keys: scope, entity_id, predicate, source_butler,
            time_from, time_to, retention_class, sensitivity.
            Unrecognized keys are silently ignored.
    """
    results = await _search.search(
        pool,
        query,
        embedding_engine,
        types=types,
        scope=scope,
        mode=mode,
        limit=limit,
        min_confidence=min_confidence,
        filters=filters,
    )
    return [_serialize_row(r) for r in results]


async def memory_recall(
    pool: Pool,
    embedding_engine,
    topic: str,
    *,
    scope: str | None = None,
    limit: int = 10,
    filters: dict[str, Any] | None = None,
    request_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """High-level composite-scored retrieval of relevant facts and rules.

    Delegates to _search.recall() and serializes the results for JSON output.

    Args:
        filters: Optional dict of AND-conditions. Supported keys: scope,
            entity_id, predicate, source_butler, time_from, time_to,
            retention_class, sensitivity. Unrecognized keys are silently ignored.
        request_context: Optional dict with 'tenant_id' and 'request_id'.
    """
    tenant_id = "owner"
    if isinstance(request_context, dict):
        rc_tenant = request_context.get("tenant_id")
        if isinstance(rc_tenant, str) and rc_tenant.strip():
            tenant_id = rc_tenant.strip()

    results = await _search.recall(
        pool,
        topic,
        embedding_engine,
        scope=scope,
        limit=limit,
        filters=filters,
        tenant_id=tenant_id,
    )
    return [_serialize_row(r) for r in results]


async def memory_get(
    pool: Pool,
    memory_type: str,
    memory_id: str,
) -> dict[str, Any] | None:
    """Retrieve a specific memory by type and ID.

    Converts the string memory_id to a UUID, delegates to _storage.get_memory(),
    and serializes the result for JSON output.
    """
    result = await _storage.get_memory(pool, memory_type, uuid.UUID(memory_id))
    if result is None:
        return None
    return _serialize_row(result)


async def memory_catalog_search(
    pool: Pool,
    embedding_engine,
    query: str,
    *,
    memory_type: str | None = None,
    limit: int = 10,
    mode: str = "hybrid",
) -> list[dict[str, Any]]:
    """Search the shared memory catalog for cross-butler memory discovery.

    Delegates to _search.search_catalog() and serializes results for JSON output.
    """
    results = await _search.search_catalog(
        pool,
        query,
        embedding_engine,
        memory_type=memory_type,
        limit=limit,
        mode=mode,
    )
    return [_serialize_row(r) for r in results]


async def predicate_search(
    pool: Pool,
    query: str,
    *,
    scope: str | None = None,
) -> list[dict[str, Any]]:
    """Search predicate_registry by name prefix and description text.

    When query is empty, all registered predicates are returned (equivalent to
    predicate_list).  When query is non-empty, rows matching by name prefix OR
    description text (case-insensitive substring) are returned.

    The optional ``scope`` parameter filters by the ``scope`` column (domain
    namespace added in mem_023: health, relationship, finance, home, global).

    Args:
        pool: asyncpg connection pool.
        query: Search string — matched as a case-insensitive prefix on ``name``
            and as a case-insensitive substring on ``description``.
        scope: Optional filter on the ``scope`` column (exact match).

    Returns:
        List of dicts with keys: name, scope, expected_subject_type,
        expected_object_type, is_edge, is_temporal, description.
        Results are ordered by name ASC.
    """
    select = (
        "SELECT name, scope, expected_subject_type, expected_object_type,"
        " is_edge, is_temporal, description"
        " FROM predicate_registry"
    )

    conditions: list[str] = []
    params: list[Any] = []

    if query:
        # Param index 1: prefix match on name
        # Param index 2: substring match on description (COALESCE so NULL desc is safe)
        params.append(query.lower())
        params.append(f"%{query.lower()}%")
        conditions.append(
            "(lower(name) LIKE $1 || '%' OR lower(COALESCE(description, '')) LIKE $2)"
        )

    if scope is not None:
        param_idx = len(params) + 1
        params.append(scope)
        conditions.append(f"scope = ${param_idx}")

    if conditions:
        select += " WHERE " + " AND ".join(conditions)

    select += " ORDER BY name ASC"

    rows = await pool.fetch(select, *params)
    return [dict(row) for row in rows]
