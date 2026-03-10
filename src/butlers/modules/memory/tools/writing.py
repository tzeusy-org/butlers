"""Memory writing tools — store episodes, facts, and rules."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _storage, get_embedding_engine

logger = logging.getLogger(__name__)


def _extract_request_context(
    request_context: dict[str, Any] | None,
) -> tuple[str, str | None]:
    """Extract tenant_id and request_id from an optional request_context dict.

    Args:
        request_context: Optional dict with 'tenant_id' and/or 'request_id' keys.

    Returns:
        Tuple of (tenant_id, request_id). Defaults to ('owner', None) when
        request_context is None or keys are absent.
    """
    if not request_context:
        return "owner", None
    tenant_id_val = request_context.get("tenant_id")
    request_id = request_context.get("request_id") or None
    tenant_id = "owner" if tenant_id_val in (None, "") else str(tenant_id_val)
    return tenant_id, str(request_id) if request_id is not None else None


async def memory_store_episode(
    pool: Pool,
    content: str,
    butler: str,
    *,
    session_id: str | None = None,
    importance: float = 5.0,
    request_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Store a raw episode from a runtime session.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new episode's ID and expiry timestamp.

    Args:
        pool: asyncpg connection pool.
        content: Episode text.
        butler: Name of the source butler.
        session_id: Optional UUID string of the source runtime session.
        importance: Importance rating (default 5.0).
        request_context: Optional dict with 'tenant_id' and 'request_id' for
            multi-tenant isolation and request trace correlation.
    """
    parsed_session_id = uuid.UUID(session_id) if session_id is not None else None
    tenant_id, request_id = _extract_request_context(request_context)
    result = await _storage.store_episode(
        pool,
        content,
        butler,
        get_embedding_engine(),
        session_id=parsed_session_id,
        importance=importance,
        tenant_id=tenant_id,
        request_id=request_id,
    )

    # Backward-compatible: older storage variants may return a mapping.
    if isinstance(result, dict):
        episode_id = result["id"]
        expires_at = result["expires_at"]
    else:
        episode_id = result
        expires_at = await pool.fetchval(
            "SELECT expires_at FROM episodes WHERE id = $1",
            episode_id,
        )
        if expires_at is None:
            ttl_days = getattr(_storage, "_DEFAULT_EPISODE_TTL_DAYS", 7)
            expires_at = datetime.now(UTC) + timedelta(days=ttl_days)

    return {
        "id": str(episode_id),
        "expires_at": expires_at.isoformat(),
    }


async def memory_store_fact(
    pool: Pool,
    embedding_engine,
    subject: str,
    predicate: str,
    content: str,
    *,
    importance: float = 5.0,
    permanence: str = "standard",
    scope: str = "global",
    tags: list[str] | None = None,
    entity_id: str | None = None,
    object_entity_id: str | None = None,
    valid_at: str | None = None,
    idempotency_key: str | None = None,
    request_context: dict[str, Any] | None = None,
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
) -> dict[str, Any]:
    """Store a distilled fact, automatically superseding any existing match.

    Accepts an optional ``entity_id`` (UUID string) to anchor the fact to a
    resolved entity.  When ``entity_id`` is provided, uniqueness is enforced
    via ``(entity_id, scope, predicate)``; the ``subject`` field is stored as
    a human-readable label only.  When omitted, existing ``(subject, predicate)``
    behaviour is preserved (backward compatible).

    Accepts an optional ``object_entity_id`` (UUID string) to create an edge-fact
    linking ``entity_id`` (subject) to ``object_entity_id`` (object).  When
    provided, uniqueness is enforced via
    ``(entity_id, object_entity_id, scope, predicate)``.

    Accepts an optional ``valid_at`` ISO-8601 string.  When omitted, the fact
    is stored as a *property fact* (``valid_at = NULL``) and supersedes any
    existing active property fact with the same uniqueness key.  When
    provided, the fact is stored as a *temporal fact* and always coexists with
    other active facts — temporal facts never supersede each other or property
    facts.

    Accepts an optional ``request_context`` dict with 'tenant_id' and 'request_id'
    for multi-tenant isolation and request trace correlation.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new fact's ID and the superseded fact's ID (if any).
    """
    import uuid as _uuid

    parsed_entity_id = _uuid.UUID(entity_id) if entity_id is not None else None
    parsed_object_entity_id = _uuid.UUID(object_entity_id) if object_entity_id is not None else None
    parsed_valid_at: datetime | None = None
    if valid_at is not None:
        parsed_valid_at = datetime.fromisoformat(valid_at)
        if parsed_valid_at.tzinfo is None:
            parsed_valid_at = parsed_valid_at.replace(tzinfo=UTC)

    tenant_id, request_id = _extract_request_context(request_context)

    result = await _storage.store_fact(
        pool,
        subject,
        predicate,
        content,
        embedding_engine,
        importance=importance,
        permanence=permanence,
        scope=scope,
        tags=tags,
        entity_id=parsed_entity_id,
        object_entity_id=parsed_object_entity_id,
        valid_at=parsed_valid_at,
        idempotency_key=idempotency_key,
        tenant_id=tenant_id,
        request_id=request_id,
        enable_shared_catalog=enable_shared_catalog,
        source_schema=source_schema,
    )

    # Backward-compatible: older storage variants may return a mapping.
    if isinstance(result, dict):
        fact_id = result["id"]
        superseded_id = result.get("superseded_id")
    else:
        fact_id = result
        superseded_id = await pool.fetchval(
            "SELECT supersedes_id FROM facts WHERE id = $1",
            fact_id,
        )

    return {
        "id": str(fact_id),
        "superseded_id": str(superseded_id) if superseded_id else None,
    }


async def memory_store_rule(
    pool: Pool,
    embedding_engine,
    content: str,
    *,
    scope: str = "global",
    tags: list[str] | None = None,
    request_context: dict[str, Any] | None = None,
    enable_shared_catalog: bool = False,
    source_schema: str | None = None,
) -> dict[str, Any]:
    """Store a new behavioral rule as a candidate.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new rule's ID.

    Args:
        pool: asyncpg connection pool.
        embedding_engine: EmbeddingEngine for semantic vectors.
        content: Rule description text.
        scope: Visibility scope (default 'global').
        tags: Optional list of string tags.
        request_context: Optional dict with 'tenant_id' and 'request_id' for
            multi-tenant isolation and request trace correlation.
        enable_shared_catalog: When True, write a catalog entry to
            ``shared.memory_catalog`` after the rule is stored.
        source_schema: Butler schema name for the catalog row (e.g. 'health').
    """
    tenant_id, request_id = _extract_request_context(request_context)
    result = await _storage.store_rule(
        pool,
        content,
        embedding_engine,
        scope=scope,
        tags=tags,
        tenant_id=tenant_id,
        request_id=request_id,
        enable_shared_catalog=enable_shared_catalog,
        source_schema=source_schema,
    )

    # Backward-compatible: older storage variants may return a mapping.
    if isinstance(result, dict):
        return {"id": str(result["id"])}
    return {"id": str(result)}
