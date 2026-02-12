"""Life events — log and list significant life events for contacts."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)


async def life_event_types_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all available life event types with their categories."""
    rows = await pool.fetch(
        """
        SELECT t.id, t.name, c.name as category
        FROM life_event_types t
        JOIN life_event_categories c ON t.category_id = c.id
        ORDER BY c.name, t.name
        """
    )
    return [dict(row) for row in rows]


async def life_event_log(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type_name: str,
    summary: str,
    description: str | None = None,
    happened_at: str | None = None,
) -> dict[str, Any]:
    """
    Log a life event for a contact.

    Args:
        contact_id: UUID of the contact
        type_name: Name of the life event type (e.g., 'promotion', 'married')
        summary: Short summary of the event
        description: Optional longer description
        happened_at: Optional date string (YYYY-MM-DD format)
    """
    # Look up the life_event_type_id by name
    type_row = await pool.fetchrow(
        """
        SELECT id FROM life_event_types WHERE name = $1
        """,
        type_name,
    )
    if type_row is None:
        raise ValueError(
            f"Unknown life event type '{type_name}'. Use life_event_types_list() to see options."
        )

    # Parse the date string if provided
    from datetime import date

    happened_at_date = None
    if happened_at is not None:
        happened_at_date = date.fromisoformat(happened_at)

    # Insert the life event
    row = await pool.fetchrow(
        """
        INSERT INTO life_events (contact_id, life_event_type_id, summary, description, happened_at)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING *
        """,
        contact_id,
        type_row["id"],
        summary,
        description,
        happened_at_date,
    )
    result = dict(row)

    # Log to activity feed
    await _log_activity(
        pool,
        contact_id,
        "life_event_logged",
        f"Life event: {type_name} - {summary}",
    )

    return result


async def life_event_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    type_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List life events, optionally filtered by contact and/or type.

    Args:
        contact_id: Optional filter by contact UUID
        type_name: Optional filter by life event type name
        limit: Maximum number of events to return
    """
    if contact_id is not None and type_name is not None:
        # Filter by both contact and type
        rows = await pool.fetch(
            """
            SELECT e.*,
                   t.name AS type_name,
                   c.name AS category,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ', con.first_name, con.last_name)), ''),
                       con.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM life_events e
            JOIN life_event_types t ON e.life_event_type_id = t.id
            JOIN life_event_categories c ON t.category_id = c.id
            JOIN contacts con ON e.contact_id = con.id
            WHERE e.contact_id = $1 AND t.name = $2
            ORDER BY e.happened_at DESC NULLS LAST, e.created_at DESC
            LIMIT $3
            """,
            contact_id,
            type_name,
            limit,
        )
    elif contact_id is not None:
        # Filter by contact only
        rows = await pool.fetch(
            """
            SELECT e.*,
                   t.name AS type_name,
                   c.name AS category,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ', con.first_name, con.last_name)), ''),
                       con.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM life_events e
            JOIN life_event_types t ON e.life_event_type_id = t.id
            JOIN life_event_categories c ON t.category_id = c.id
            JOIN contacts con ON e.contact_id = con.id
            WHERE e.contact_id = $1
            ORDER BY e.happened_at DESC NULLS LAST, e.created_at DESC
            LIMIT $2
            """,
            contact_id,
            limit,
        )
    elif type_name is not None:
        # Filter by type only
        rows = await pool.fetch(
            """
            SELECT e.*,
                   t.name AS type_name,
                   c.name AS category,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ', con.first_name, con.last_name)), ''),
                       con.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM life_events e
            JOIN life_event_types t ON e.life_event_type_id = t.id
            JOIN life_event_categories c ON t.category_id = c.id
            JOIN contacts con ON e.contact_id = con.id
            WHERE t.name = $1
            ORDER BY e.happened_at DESC NULLS LAST, e.created_at DESC
            LIMIT $2
            """,
            type_name,
            limit,
        )
    else:
        # No filters
        rows = await pool.fetch(
            """
            SELECT e.*,
                   t.name AS type_name,
                   c.name AS category,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ', con.first_name, con.last_name)), ''),
                       con.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM life_events e
            JOIN life_event_types t ON e.life_event_type_id = t.id
            JOIN life_event_categories c ON t.category_id = c.id
            JOIN contacts con ON e.contact_id = con.id
            ORDER BY e.happened_at DESC NULLS LAST, e.created_at DESC
            LIMIT $1
            """,
            limit,
        )

    return [dict(row) for row in rows]
