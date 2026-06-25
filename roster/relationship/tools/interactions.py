"""Interactions — log and list interactions with contacts backed by SPO facts.

Each interaction is a temporal fact in the facts table:
  subject   = entity:{entity_id}
  predicate = 'interaction_{type}'  (e.g. 'interaction_call', 'interaction_meeting')
  content   = summary
  metadata  = {type, direction, duration_minutes, extra_metadata}
  valid_at  = occurred_at   (temporal — multiple coexist per entity)
  scope     = 'relationship'
  entity_id = entity UUID (same value as subject's id)

The response shape is backward compatible with the legacy interactions table.

Subject key format changed from ``contact:{contact_id}`` to ``entity:{entity_id}``
in migration rel_018.  All callers that previously passed contact_id must resolve
to entity_id before calling interaction_log/interaction_list.  The MCP tool
wrappers in roster/relationship/modules/tools.py handle this resolution so that
LLM-facing tool signatures still accept contact_id.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

_VALID_DIRECTIONS = ("incoming", "outgoing", "mutual")
_DIRECTION_ALIASES = {
    "inbound": "incoming",
    "outbound": "outgoing",
}

# interaction_log() writes predicate = f"interaction_{type}" at permanence='stable'.
# These type strings are RESERVED because they conflict with episodic predicates that
# must remain at volatile/ephemeral permanence (managed separately, not via interaction_log).
# Passing a reserved type raises ValueError to prevent false-positive curation flags.
#   'note'  → would write interaction_note at stable, which episodic-predicate curation
#             flags as misplaced. interaction_note is an ephemeral annotation, not a
#             structured interaction record.
_RESERVED_INTERACTION_TYPES: frozenset[str] = frozenset({"note"})

_embedding_engine: Any = None


def _normalize_direction(direction: str | None) -> str | None:
    """Normalize common channel-direction aliases to interaction directions."""
    if direction is None:
        return None

    normalized = _DIRECTION_ALIASES.get(direction, direction)
    if normalized not in _VALID_DIRECTIONS:
        accepted = (*_VALID_DIRECTIONS, *_DIRECTION_ALIASES)
        raise ValueError(f"Invalid direction '{direction}'. Must be one of {accepted}")
    return normalized


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


async def _resolve_interaction_target(
    pool: asyncpg.Pool,
    target_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """Return ``(entity_id, contact_id)`` for an entity-or-contact target UUID.

    ``interaction_log`` and ``interaction_list`` now store/read canonical
    ``entity:{entity_id}`` subjects, but direct legacy callers may still pass a
    contact UUID. Resolve contact UUIDs via ``resolve_contact_entity_id``
    (contact_entity_map → contacts_source_links → public.entities direct pass-through)
    without reading public.contacts.

    Returns ``(resolved_entity_id, target_id)`` when target_id was a contact UUID.
    Returns ``(target_id, None)`` when target_id is already an entity UUID or
    cannot be resolved to a distinct entity_id.
    """
    from ._entity_resolve import resolve_contact_entity_id

    resolved = await resolve_contact_entity_id(pool, target_id)
    if resolved is None or resolved == target_id:
        # target_id is already an entity UUID (Step 3 pass-through), or could not
        # be resolved — treat it as entity_id directly, no contact_id.
        return target_id, None
    # target_id was a contact UUID; resolved is the canonical entity_id.
    return resolved, target_id


def _fact_to_interaction(
    row: dict[str, Any],
    entity_id: uuid.UUID,
    contact_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Convert a facts row to the interactions API shape.

    ``type`` is derived from the predicate suffix (e.g. 'interaction_call' → 'call'),
    falling back to the metadata 'type' field for backward compatibility with any
    legacy 'interaction' rows that pre-date the predicate migration.
    """
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    predicate = row.get("predicate", "")
    if predicate.startswith("interaction_"):
        interaction_type = predicate[len("interaction_") :]
    else:
        interaction_type = meta.get("type", "")
    return {
        "id": row["id"],
        "entity_id": entity_id,
        "contact_id": contact_id,
        "type": interaction_type,
        "summary": row.get("content") or None,
        "occurred_at": row.get("valid_at"),
        "created_at": row.get("created_at"),
        "direction": meta.get("direction"),
        "duration_minutes": meta.get("duration_minutes"),
        "metadata": meta.get("extra_metadata"),
    }


async def interaction_log(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    type: str,
    summary: str | None = None,
    occurred_at: datetime | None = None,
    direction: str | None = None,
    duration_minutes: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Log an interaction with an entity.

    Args:
        pool: Database connection pool.
        entity_id: The entity UUID to log the interaction for.  For backward
            compatibility, a contact UUID is also accepted and resolved to its
            linked entity UUID before writing.
        type: Interaction type string (e.g. 'call', 'email', 'meeting').
        summary: Optional free-text summary of the interaction.
        occurred_at: When the interaction occurred. If None, defaults to now.
        direction: One of 'incoming', 'outgoing', 'mutual'. The common channel
            aliases 'inbound' and 'outbound' are accepted and stored as their
            canonical interaction values.
        duration_minutes: Duration of the interaction in minutes.
        metadata: Arbitrary extra metadata dict.
    """
    direction = _normalize_direction(direction)
    if type in _RESERVED_INTERACTION_TYPES:
        raise ValueError(
            f"interaction_log type '{type}' is reserved: 'interaction_{type}' is an episodic "
            "predicate managed outside interaction_log (it must stay at volatile/ephemeral "
            "permanence, but interaction_log always writes stable). "
            f"Reserved types: {sorted(_RESERVED_INTERACTION_TYPES)}"
        )

    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)
    effective_occurred_at = occurred_at if occurred_at is not None else now
    entity_id, contact_id = await _resolve_interaction_target(pool, entity_id)

    # Idempotency guard: only explicit timestamps are treated as deterministic backfills.
    # Direction is included in the dedup key so that incoming and outgoing facts for the
    # same entity on the same day can coexist (RFC 0013 D4).  Two facts with direction=None
    # still collide with each other to preserve backward compatibility.
    if occurred_at is not None:
        predicate = f"interaction_{type}"
        if direction is not None:
            existing = await pool.fetchrow(
                """
                SELECT id FROM facts
                WHERE subject = $1
                  AND predicate = $2
                  AND scope = 'relationship'
                  AND validity = 'active'
                  AND valid_at::date = $3::date
                  AND metadata->>'direction' = $4
                LIMIT 1
                """,
                f"entity:{entity_id}",
                predicate,
                occurred_at,
                direction,
            )
        else:
            existing = await pool.fetchrow(
                """
                SELECT id FROM facts
                WHERE subject = $1
                  AND predicate = $2
                  AND scope = 'relationship'
                  AND validity = 'active'
                  AND valid_at::date = $3::date
                  AND metadata->>'direction' IS NULL
                LIMIT 1
                """,
                f"entity:{entity_id}",
                predicate,
                occurred_at,
            )
        if existing is not None:
            return {
                "skipped": "duplicate",
                "existing_id": str(existing["id"]),
            }

    embedding_engine = _get_embedding_engine()

    fact_metadata: dict[str, Any] = {"type": type}
    if direction is not None:
        fact_metadata["direction"] = direction
    if duration_minutes is not None:
        fact_metadata["duration_minutes"] = duration_minutes
    if metadata is not None:
        fact_metadata["extra_metadata"] = metadata

    fact_id = (
        await store_fact(
            pool,
            subject=f"entity:{entity_id}",
            predicate=f"interaction_{type}",
            content=summary or "",
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=entity_id,
            valid_at=effective_occurred_at,
            metadata=fact_metadata,
        )
    )["id"]

    result = {
        "id": fact_id,
        "entity_id": entity_id,
        "contact_id": contact_id,
        "type": type,
        "summary": summary,
        "occurred_at": effective_occurred_at,
        "created_at": now,
        "direction": direction,
        "duration_minutes": duration_minutes,
        "metadata": metadata,
    }

    return result


async def interaction_log_group(
    pool: asyncpg.Pool,
    group_id: uuid.UUID,
    type: str = "group_interaction",
    direction: str = "mutual",
    occurred_at: datetime | None = None,
    summary: str | None = None,
    duration_minutes: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Log an interaction with all members of a contact group in a single call.

    Resolves group membership from the group_members table, maps each member's
    contact_id to their entity_id via contact_entity_map, and fans out
    interaction_log() calls for each member with group_size injected into
    the fact metadata.

    Members whose contact has no linked entity_id (data integrity issue) are
    skipped with a warning; they are not counted in either logged or skipped.

    Returns:
        {"logged": N, "skipped": M, "group_size": G, "status": "ok"} on success.
        {"logged": 0, "skipped": 0, "group_size": G, "status": "group_too_large"} if >20 members.
        {"logged": 0, "skipped": 0, "group_size": 0, "status": "ok"} if the group is empty.
    """
    direction = _normalize_direction(direction)

    # Fetch up to 21 rows so we can detect oversized groups without reading unbounded rows.
    # LEFT JOIN contact_entity_map to resolve entity_id without reading public.contacts.
    # Members not yet in the map yield entity_id=NULL and are skipped in the fan-out loop.
    rows = await pool.fetch(
        """
        SELECT gm.contact_id, cem.entity_id
        FROM group_members gm
        LEFT JOIN contact_entity_map cem ON cem.contact_id = gm.contact_id
        WHERE gm.group_id = $1
        LIMIT 21
        """,
        group_id,
    )

    if not rows:
        return {"logged": 0, "skipped": 0, "group_size": 0, "status": "ok"}

    if len(rows) > 20:
        # Fetch the exact count only when the limit was hit.
        group_size = await pool.fetchval(
            "SELECT COUNT(*) FROM group_members WHERE group_id = $1",
            group_id,
        )
        return {"logged": 0, "skipped": 0, "group_size": group_size, "status": "group_too_large"}

    group_size = len(rows)

    logged = 0
    skipped = 0
    group_metadata: dict[str, Any] = {
        "group_size": group_size,
        "group_id": str(group_id),
        **(metadata or {}),
    }

    for row in rows:
        contact_id = row["contact_id"]
        entity_id = row["entity_id"]
        if entity_id is None:
            logger.warning(
                "interaction_log_group: contact %s has no linked entity_id — skipping",
                contact_id,
            )
            continue
        result = await interaction_log(
            pool,
            entity_id,
            type=type,
            summary=summary,
            occurred_at=occurred_at,
            direction=direction,
            duration_minutes=duration_minutes,
            metadata=group_metadata,
        )
        if result.get("skipped") == "duplicate":
            skipped += 1
        else:
            logged += 1

    return {"logged": logged, "skipped": skipped, "group_size": group_size, "status": "ok"}


async def interaction_list(
    pool: asyncpg.Pool,
    entity_id: uuid.UUID,
    limit: int = 20,
    direction: str | None = None,
    type: str | None = None,
) -> list[dict[str, Any]]:
    """List interactions for an entity, most recent first.

    Optionally filter by direction and/or type.

    Args:
        pool: Database connection pool.
        entity_id: The entity UUID to list interactions for.  For backward
            compatibility, a contact UUID is also accepted and resolved to its
            linked entity UUID before querying.
    """
    entity_id, contact_id = await _resolve_interaction_target(pool, entity_id)
    conditions = [
        "subject = $1",
        "predicate LIKE 'interaction_%'",
        "scope = 'relationship'",
        "validity = 'active'",
    ]
    params: list[Any] = [f"entity:{entity_id}"]
    idx = 2

    direction = _normalize_direction(direction)
    if direction is not None:
        conditions.append(f"metadata->>'direction' = ${idx}")
        params.append(direction)
        idx += 1

    if type is not None:
        conditions.append(f"predicate = ${idx}")
        params.append(f"interaction_{type}")
        idx += 1

    params.append(limit)
    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT id, predicate, content, valid_at, created_at, metadata
        FROM facts
        WHERE {where}
        ORDER BY valid_at DESC
        LIMIT ${idx}
        """,
        *params,
    )
    return [_fact_to_interaction(dict(r), entity_id, contact_id) for r in rows]
