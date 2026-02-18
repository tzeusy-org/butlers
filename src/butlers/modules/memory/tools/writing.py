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


async def memory_store_episode(
    pool: Pool,
    content: str,
    butler: str,
    *,
    session_id: str | None = None,
    importance: float = 5.0,
) -> dict[str, Any]:
    """Store a raw episode from a runtime session.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new episode's ID and expiry timestamp.
    """
    parsed_session_id = uuid.UUID(session_id) if session_id is not None else None
    result = await _storage.store_episode(
        pool,
        content,
        butler,
        get_embedding_engine(),
        session_id=parsed_session_id,
        importance=importance,
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
) -> dict[str, Any]:
    """Store a distilled fact, automatically superseding any existing match.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new fact's ID and the superseded fact's ID (if any).
    """
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
) -> dict[str, Any]:
    """Store a new behavioral rule as a candidate.

    Delegates to the storage layer and returns an MCP-friendly dict with the
    new rule's ID.
    """
    result = await _storage.store_rule(
        pool,
        content,
        embedding_engine,
        scope=scope,
        tags=tags,
    )

    # Backward-compatible: older storage variants may return a mapping.
    if isinstance(result, dict):
        return {"id": str(result["id"])}
    return {"id": str(result)}
