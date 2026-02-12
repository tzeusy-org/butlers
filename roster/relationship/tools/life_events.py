"""Life events — log and list significant life events for contacts."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import contact_name_expr, has_table, table_columns
from butlers.tools.relationship.feed import _log_activity


async def life_event_types_list(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    """List all available life event types with their categories."""
    if not (await has_table(pool, "life_event_types")):
        return []
    if not (await has_table(pool, "life_event_categories")):
        return []

    rows = await pool.fetch(
        """
        SELECT t.id, t.name, c.name as category
        FROM life_event_types t
        JOIN life_event_categories c ON t.category_id = c.id
        ORDER BY c.name, t.name
        """
    )
    return [dict(row) for row in rows]


def _coerce_happened_date(
    happened_at: str | None,
    occurred_at: datetime | None,
) -> date | None:
    if occurred_at is not None:
        return occurred_at.date()
    if happened_at is None:
        return None
    return date.fromisoformat(happened_at)


def _coerce_occurred_at(
    happened_at: str | None,
    occurred_at: datetime | None,
) -> datetime | None:
    if occurred_at is not None:
        return occurred_at
    if happened_at is None:
        return None
    return datetime.fromisoformat(happened_at).replace(tzinfo=UTC)


async def life_event_log(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    type_name: str,
    summary: str | None = None,
    description: str | None = None,
    happened_at: str | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    """Log a life event, supporting both new and legacy schemas."""
    cols = await table_columns(pool, "life_events")
    uses_type_table = "life_event_type_id" in cols and await has_table(pool, "life_event_types")

    if uses_type_table:
        type_row = await pool.fetchrow(
            "SELECT id FROM life_event_types WHERE name = $1",
            type_name,
        )
        if type_row is None:
            raise ValueError(f"Unknown life event type '{type_name}'.")

        happened_at_date = _coerce_happened_date(happened_at, occurred_at)
        if happened_at_date is not None:
            existing = await pool.fetchrow(
                """
                SELECT id FROM life_events
                WHERE contact_id = $1
                  AND life_event_type_id = $2
                  AND happened_at = $3
                """,
                contact_id,
                type_row["id"],
                happened_at_date,
            )
            if existing is not None:
                return {"skipped": "duplicate", "existing_id": str(existing["id"])}

        event_summary = summary or description or type_name
        row = await pool.fetchrow(
            """
            INSERT INTO life_events (
                contact_id, life_event_type_id, summary, description, happened_at
            )
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            contact_id,
            type_row["id"],
            event_summary,
            description,
            happened_at_date,
        )
        result = dict(row)
        activity_text = event_summary
    else:
        event_occurred_at = _coerce_occurred_at(happened_at, occurred_at)
        if event_occurred_at is not None and "occurred_at" in cols:
            existing = await pool.fetchrow(
                """
                SELECT id FROM life_events
                WHERE contact_id = $1 AND type = $2 AND DATE(occurred_at) = DATE($3)
                """,
                contact_id,
                type_name,
                event_occurred_at,
            )
            if existing is not None:
                return {"skipped": "duplicate", "existing_id": str(existing["id"])}

        event_description = description if description is not None else summary
        insert_cols: list[str] = ["contact_id"]
        values: list[Any] = [contact_id]

        def add(col: str, value: Any) -> None:
            if col in cols:
                insert_cols.append(col)
                values.append(value)

        add("type", type_name)
        add("summary", summary or description or type_name)
        add("description", event_description)
        if event_occurred_at is not None:
            add("occurred_at", event_occurred_at)

        placeholders = [f"${idx}" for idx in range(1, len(values) + 1)]
        row = await pool.fetchrow(
            f"""
            INSERT INTO life_events ({", ".join(insert_cols)})
            VALUES ({", ".join(placeholders)})
            RETURNING *
            """,
            *values,
        )
        result = dict(row)
        activity_text = summary or event_description or type_name

    await _log_activity(
        pool,
        contact_id,
        "life_event_logged",
        f"Life event: {type_name} - {activity_text}",
    )
    return result


async def life_event_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    type_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List life events, optionally filtered by contact and/or type."""
    event_cols = await table_columns(pool, "life_events")
    contact_cols = await table_columns(pool, "contacts")
    name_sql = contact_name_expr(contact_cols, alias="con")

    uses_type_table = "life_event_type_id" in event_cols and await has_table(
        pool, "life_event_types"
    )
    if uses_type_table:
        if contact_id is not None and type_name is not None:
            rows = await pool.fetch(
                f"""
                SELECT e.*, t.name as type_name, c.name as category, {name_sql} as contact_name
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
            rows = await pool.fetch(
                f"""
                SELECT e.*, t.name as type_name, c.name as category, {name_sql} as contact_name
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
            rows = await pool.fetch(
                f"""
                SELECT e.*, t.name as type_name, c.name as category, {name_sql} as contact_name
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
            rows = await pool.fetch(
                f"""
                SELECT e.*, t.name as type_name, c.name as category, {name_sql} as contact_name
                FROM life_events e
                JOIN life_event_types t ON e.life_event_type_id = t.id
                JOIN life_event_categories c ON t.category_id = c.id
                JOIN contacts con ON e.contact_id = con.id
                ORDER BY e.happened_at DESC NULLS LAST, e.created_at DESC
                LIMIT $1
                """,
                limit,
            )
    else:
        where: list[str] = []
        params: list[Any] = []
        if contact_id is not None:
            where.append(f"e.contact_id = ${len(params) + 1}")
            params.append(contact_id)
        if type_name is not None:
            where.append(f"e.type = ${len(params) + 1}")
            params.append(type_name)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        order_col = "occurred_at" if "occurred_at" in event_cols else "created_at"
        params.append(limit)
        rows = await pool.fetch(
            f"""
            SELECT e.*, e.type as type_name, NULL::text as category, {name_sql} as contact_name
            FROM life_events e
            JOIN contacts con ON e.contact_id = con.id
            {where_sql}
            ORDER BY e.{order_col} DESC
            LIMIT ${len(params)}
            """,
            *params,
        )

    return [dict(row) for row in rows]
