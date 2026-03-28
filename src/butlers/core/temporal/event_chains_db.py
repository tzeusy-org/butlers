"""Database operations for event chains (CRUD + trigger detection helpers).

Provides async functions for:
  - event_chain_create   — Insert a new event chain row
  - event_chain_update   — Update fields on an existing chain (status reset on action change)
  - event_chain_list     — List chains with optional trigger_type filter
  - event_chain_delete   — Delete a chain by ID
  - get_chain_by_id      — Fetch a single chain row by UUID

These functions require an asyncpg pool and operate within the current butler
schema (via search_path set on the connection pool).

See: openspec/changes/temporal-intelligence/tasks.md §5 (tasks 5.1–5.4)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from butlers.core.temporal.event_chains import validate_chain_actions

_VALID_TRIGGER_TYPES = frozenset(
    {
        "calendar_event_end",
        "deadline_passed",
        "deadline_threshold",
    }
)
_VALID_STATUSES = frozenset({"active", "paused", "fired", "failed", "disabled"})


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a plain dict with JSON-normalised fields."""
    result = dict(row)
    # Normalise UUID to string
    if result.get("id") is not None:
        result["id"] = str(result["id"])
    # Normalise JSONB actions — asyncpg may return a list or a JSON string
    actions = result.get("actions")
    if isinstance(actions, str):
        result["actions"] = json.loads(actions)
    # Normalise timestamps to ISO strings
    for col in ("created_at", "updated_at"):
        val = result.get(col)
        if val is not None and isinstance(val, datetime):
            result[col] = val.isoformat()
    return result


async def event_chain_create(
    pool: asyncpg.Pool,
    *,
    name: str,
    trigger_type: str,
    actions: list[dict[str, Any]],
    butler_name: str,
    trigger_reference: str | None = None,
) -> dict[str, Any]:
    """Create a new event chain.

    Validates the trigger_type, actions schema (via validate_chain_actions),
    and enforces unique name per butler.

    Args:
        pool: asyncpg connection pool.
        name: Human-readable chain name (unique per butler).
        trigger_type: One of 'calendar_event_end', 'deadline_passed',
            'deadline_threshold'.
        actions: Ordered list of action dicts. Each must have action_type,
            delay_minutes, and type-specific required fields.
        butler_name: The owning butler's name.
        trigger_reference: Optional event_id or task_id this chain fires on.
            When None, the chain fires for all events of this trigger_type.

    Returns:
        The newly created chain row as a dict.

    Raises:
        ValueError: On invalid trigger_type, invalid actions, or duplicate name.
        asyncpg.UniqueViolationError: If name already exists for this butler
            (wrapped as ValueError).
    """
    if not name or not name.strip():
        raise ValueError("Event chain name must be a non-empty string")
    name = name.strip()

    if trigger_type not in _VALID_TRIGGER_TYPES:
        raise ValueError(
            f"Invalid trigger_type: {trigger_type!r}. "
            f"Must be one of {sorted(_VALID_TRIGGER_TYPES)!r}"
        )

    # Validate the actions array (raises ValueError on failure)
    validate_chain_actions(actions)

    try:
        row = await pool.fetchrow(
            """
            INSERT INTO event_chains
                (name, trigger_type, trigger_reference, actions, butler_name)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            RETURNING id, name, trigger_type, trigger_reference, actions,
                      status, butler_name, created_at, updated_at
            """,
            name,
            trigger_type,
            trigger_reference,
            json.dumps(actions),
            butler_name,
        )
    except asyncpg.UniqueViolationError:
        raise ValueError(f"An event chain named {name!r} already exists for butler {butler_name!r}")
    assert row is not None
    return _row_to_dict(row)


async def event_chain_update(
    pool: asyncpg.Pool,
    chain_id: str | uuid.UUID,
    *,
    butler_name: str,
    name: str | None = None,
    trigger_type: str | None = None,
    trigger_reference: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Update fields on an existing event chain.

    When *actions* is updated, status is reset to 'active' (re-arm the chain).

    Args:
        pool: asyncpg connection pool.
        chain_id: UUID of the chain to update.
        butler_name: Butler name for ownership check.
        name: New name (optional).
        trigger_type: New trigger_type (optional).
        trigger_reference: New trigger_reference (optional; pass empty string to clear).
        actions: New actions array (optional; triggers status reset to 'active').
        status: Explicit status override ('active' | 'paused' | 'fired' | 'failed' | 'disabled').

    Returns:
        The updated chain row as a dict.

    Raises:
        ValueError: If chain not found, invalid field values, or duplicate name.
    """
    chain_uuid = uuid.UUID(str(chain_id))

    # Fetch existing row (ownership check)
    existing = await pool.fetchrow(
        """
        SELECT id, name, trigger_type, trigger_reference, actions,
               status, butler_name, created_at, updated_at
        FROM event_chains
        WHERE id = $1 AND butler_name = $2
        """,
        chain_uuid,
        butler_name,
    )
    if existing is None:
        raise ValueError(f"Event chain {chain_id!r} not found for butler {butler_name!r}")

    # Validate new values
    if trigger_type is not None and trigger_type not in _VALID_TRIGGER_TYPES:
        raise ValueError(
            f"Invalid trigger_type: {trigger_type!r}. "
            f"Must be one of {sorted(_VALID_TRIGGER_TYPES)!r}"
        )
    if status is not None and status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}. Must be one of {sorted(_VALID_STATUSES)!r}")
    if actions is not None:
        validate_chain_actions(actions)

    # Build SET clause
    set_clauses = ["updated_at = now()"]
    params: list[Any] = [chain_uuid, butler_name]
    idx = 3

    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("Event chain name must be a non-empty string")
        set_clauses.append(f"name = ${idx}")
        params.append(name)
        idx += 1

    if trigger_type is not None:
        set_clauses.append(f"trigger_type = ${idx}")
        params.append(trigger_type)
        idx += 1

    if trigger_reference is not None:
        set_clauses.append(f"trigger_reference = ${idx}")
        # Empty string means clear the reference
        params.append(trigger_reference if trigger_reference else None)
        idx += 1

    if actions is not None:
        set_clauses.append(f"actions = ${idx}::jsonb")
        params.append(json.dumps(actions))
        idx += 1
        # Reset status to 'active' when actions change (unless explicitly overridden)
        if status is None:
            set_clauses.append("status = 'active'")

    if status is not None:
        set_clauses.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    try:
        row = await pool.fetchrow(
            f"""
            UPDATE event_chains
            SET {", ".join(set_clauses)}
            WHERE id = $1 AND butler_name = $2
            RETURNING id, name, trigger_type, trigger_reference, actions,
                      status, butler_name, created_at, updated_at
            """,
            *params,
        )
    except asyncpg.UniqueViolationError:
        raise ValueError(f"An event chain named {name!r} already exists for butler {butler_name!r}")
    assert row is not None
    return _row_to_dict(row)


async def event_chain_list(
    pool: asyncpg.Pool,
    butler_name: str,
    *,
    trigger_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List event chains for a butler with optional filters.

    Args:
        pool: asyncpg connection pool.
        butler_name: Butler name (scopes the query).
        trigger_type: Optional filter on trigger_type.
        status: Optional filter on status.
        limit: Maximum number of rows to return (default 100).

    Returns:
        List of chain rows as dicts, ordered by created_at ascending.

    Raises:
        ValueError: If trigger_type or status filter is invalid.
    """
    if trigger_type is not None and trigger_type not in _VALID_TRIGGER_TYPES:
        raise ValueError(
            f"Invalid trigger_type filter: {trigger_type!r}. "
            f"Must be one of {sorted(_VALID_TRIGGER_TYPES)!r}"
        )
    if status is not None and status not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status filter: {status!r}. Must be one of {sorted(_VALID_STATUSES)!r}"
        )

    where_clauses = ["butler_name = $1"]
    params: list[Any] = [butler_name]
    idx = 2

    if trigger_type is not None:
        where_clauses.append(f"trigger_type = ${idx}")
        params.append(trigger_type)
        idx += 1

    if status is not None:
        where_clauses.append(f"status = ${idx}")
        params.append(status)
        idx += 1

    params.append(limit)
    rows = await pool.fetch(
        f"""
        SELECT id, name, trigger_type, trigger_reference, actions,
               status, butler_name, created_at, updated_at
        FROM event_chains
        WHERE {" AND ".join(where_clauses)}
        ORDER BY created_at ASC
        LIMIT ${idx}
        """,
        *params,
    )
    return [_row_to_dict(row) for row in rows]


async def event_chain_delete(
    pool: asyncpg.Pool,
    chain_id: str | uuid.UUID,
    *,
    butler_name: str,
) -> bool:
    """Delete an event chain by ID.

    Args:
        pool: asyncpg connection pool.
        chain_id: UUID of the chain to delete.
        butler_name: Butler name for ownership check.

    Returns:
        True if the chain was deleted, False if not found.
    """
    chain_uuid = uuid.UUID(str(chain_id))
    result = await pool.execute(
        """
        DELETE FROM event_chains
        WHERE id = $1 AND butler_name = $2
        """,
        chain_uuid,
        butler_name,
    )
    # asyncpg returns "DELETE N" where N is the row count
    deleted_count = int(result.split()[-1])
    return deleted_count > 0


async def get_chain_by_id(
    pool: asyncpg.Pool,
    chain_id: str | uuid.UUID,
    *,
    butler_name: str,
) -> dict[str, Any] | None:
    """Fetch a single event chain by UUID.

    Args:
        pool: asyncpg connection pool.
        chain_id: UUID of the chain.
        butler_name: Butler name for ownership check.

    Returns:
        Chain row as a dict, or None if not found.
    """
    chain_uuid = uuid.UUID(str(chain_id))
    row = await pool.fetchrow(
        """
        SELECT id, name, trigger_type, trigger_reference, actions,
               status, butler_name, created_at, updated_at
        FROM event_chains
        WHERE id = $1 AND butler_name = $2
        """,
        chain_uuid,
        butler_name,
    )
    if row is None:
        return None
    return _row_to_dict(row)
