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
