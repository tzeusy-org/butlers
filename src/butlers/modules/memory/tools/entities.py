"""Memory entity tools — create, retrieve, and update named entities."""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from asyncpg import Pool

from butlers.modules.memory.tools._helpers import _serialize_row

logger = logging.getLogger(__name__)

VALID_ENTITY_TYPES = frozenset({"person", "organization", "place", "other"})


async def entity_create(
    pool: Pool,
    canonical_name: str,
    entity_type: Literal["person", "organization", "place", "other"],
    *,
    tenant_id: str,
    aliases: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert a new entity and return its UUID.

    Args:
        pool: asyncpg connection pool.
        canonical_name: The canonical name of the entity.
        entity_type: One of 'person', 'organization', 'place', 'other'.
        tenant_id: Tenant scope for isolation.
        aliases: Optional list of alternative names for the entity.
        metadata: Optional JSONB metadata dict.

    Returns:
        Dict with key ``entity_id`` (UUID string).

    Raises:
        ValueError: If the (tenant_id, canonical_name, entity_type) already exists
                    or if entity_type is invalid.
    """
    if entity_type not in VALID_ENTITY_TYPES:
        raise ValueError(
            f"Invalid entity_type '{entity_type}'. Must be one of: {sorted(VALID_ENTITY_TYPES)}"
        )

    aliases_list = aliases or []
    metadata_json = json.dumps(metadata or {})

    try:
        entity_id = await pool.fetchval(
            """
            INSERT INTO entities (tenant_id, canonical_name, entity_type, aliases, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id
            """,
            tenant_id,
            canonical_name,
            entity_type,
            aliases_list,
            metadata_json,
        )
    except Exception as exc:
        # Unique constraint violation: asyncpg raises UniqueViolationError
        exc_str = str(exc)
        if "uq_entities_tenant_canonical_type" in exc_str or "unique" in exc_str.lower():
            raise ValueError(
                f"Entity with canonical_name='{canonical_name}' and "
                f"entity_type='{entity_type}' already exists for this tenant."
            ) from exc
        raise

    return {"entity_id": str(entity_id)}


async def entity_get(
    pool: Pool,
    entity_id: str,
    *,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Return the full entity record including aliases and metadata.

    Args:
        pool: asyncpg connection pool.
        entity_id: UUID string of the entity.
        tenant_id: Tenant scope for isolation.

    Returns:
        Serialized entity dict or None if not found.
    """
    row = await pool.fetchrow(
        """
        SELECT id, tenant_id, canonical_name, entity_type, aliases, metadata,
               created_at, updated_at
        FROM entities
        WHERE id = $1 AND tenant_id = $2
        """,
        uuid.UUID(entity_id),
        tenant_id,
    )

    if row is None:
        return None

    return _serialize_row(dict(row))


async def entity_update(
    pool: Pool,
    entity_id: str,
    *,
    tenant_id: str,
    canonical_name: str | None = None,
    aliases: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Update entity fields.

    - ``canonical_name``: replaces the current value when provided.
    - ``aliases``: replace-all semantics — pass the full desired list.
    - ``metadata``: merge semantics — keys are merged into existing metadata.

    Args:
        pool: asyncpg connection pool.
        entity_id: UUID string of the entity to update.
        tenant_id: Tenant scope for isolation.
        canonical_name: New canonical name (optional).
        aliases: Full replacement aliases list (optional).
        metadata: Metadata keys to merge in (optional).

    Returns:
        Updated serialized entity dict or None if not found.
    """
    eid = uuid.UUID(entity_id)

    # Fetch the current row to verify ownership and get current metadata
    current = await pool.fetchrow(
        "SELECT id, metadata FROM entities WHERE id = $1 AND tenant_id = $2",
        eid,
        tenant_id,
    )
    if current is None:
        return None

    # Build SET clauses dynamically based on what was provided
    set_clauses: list[str] = ["updated_at = now()"]
    params: list[Any] = []
    param_idx = 1

    if canonical_name is not None:
        params.append(canonical_name)
        set_clauses.append(f"canonical_name = ${param_idx}")
        param_idx += 1

    if aliases is not None:
        params.append(aliases)
        set_clauses.append(f"aliases = ${param_idx}")
        param_idx += 1

    if metadata is not None:
        # Merge: combine existing metadata with new keys (new keys win on conflict)
        existing_metadata: dict[str, Any] = dict(current["metadata"]) if current["metadata"] else {}
        merged = {**existing_metadata, **metadata}
        params.append(json.dumps(merged))
        set_clauses.append(f"metadata = ${param_idx}::jsonb")
        param_idx += 1

    # WHERE clause params
    params.append(eid)
    params.append(tenant_id)
    where_id_idx = param_idx
    where_tenant_idx = param_idx + 1

    row = await pool.fetchrow(
        f"""
        UPDATE entities
        SET {", ".join(set_clauses)}
        WHERE id = ${where_id_idx} AND tenant_id = ${where_tenant_idx}
        RETURNING id, tenant_id, canonical_name, entity_type, aliases, metadata,
                  created_at, updated_at
        """,
        *params,
    )

    if row is None:
        return None

    return _serialize_row(dict(row))
