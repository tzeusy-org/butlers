"""Notes — create, list, and search notes about contacts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import contact_name_expr, table_columns
from butlers.tools.relationship.feed import _log_activity


async def note_create(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    content: str | None = None,
    body: str | None = None,
    title: str | None = None,
    emotion: str | None = None,
) -> dict[str, Any]:
    """Create a note about a contact."""
    note_text = body if body is not None else content
    if not note_text:
        raise ValueError("Note content/body is required")

    cols = await table_columns(pool, "notes")
    text_col = "body" if "body" in cols else "content"

    # Idempotency guard: same note text for same contact within 1 hour.
    one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    existing = await pool.fetchrow(
        f"""
        SELECT id FROM notes
        WHERE contact_id = $1 AND {text_col} = $2 AND created_at >= $3
        """,
        contact_id,
        note_text,
        one_hour_ago,
    )
    if existing is not None:
        return {"skipped": "duplicate", "existing_id": str(existing["id"])}

    insert_cols = ["contact_id"]
    values: list[Any] = [contact_id]
    placeholders = ["$1"]

    if "title" in cols:
        insert_cols.append("title")
        placeholders.append(f"${len(values) + 1}")
        values.append(title)
    if "body" in cols:
        insert_cols.append("body")
        placeholders.append(f"${len(values) + 1}")
        values.append(note_text)
    if "content" in cols:
        insert_cols.append("content")
        placeholders.append(f"${len(values) + 1}")
        values.append(note_text)
    if "emotion" in cols:
        insert_cols.append("emotion")
        placeholders.append(f"${len(values) + 1}")
        values.append(emotion)

    row = await pool.fetchrow(
        f"""
        INSERT INTO notes ({", ".join(insert_cols)})
        VALUES ({", ".join(placeholders)})
        RETURNING *
        """,
        *values,
    )
    result = dict(row)
    if "content" not in result and "body" in result:
        result["content"] = result["body"]
    if "body" not in result and "content" in result:
        result["body"] = result["content"]

    snippet = note_text[:50] + "..." if len(note_text) > 50 else note_text
    await _log_activity(
        pool,
        contact_id,
        "note_created",
        f"Added note: '{snippet}'",
        entity_type="note",
        entity_id=result["id"],
    )
    return result


async def note_list(
    pool: asyncpg.Pool, contact_id: uuid.UUID, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """List all notes for a contact."""
    cols = await table_columns(pool, "notes")
    order_col = "created_at" if "created_at" in cols else "id"
    rows = await pool.fetch(
        f"SELECT * FROM notes WHERE contact_id = $1 ORDER BY {order_col} DESC LIMIT $2 OFFSET $3",
        contact_id,
        limit,
        offset,
    )
    results = [dict(row) for row in rows]
    for result in results:
        if "content" not in result and "body" in result:
            result["content"] = result["body"]
        if "body" not in result and "content" in result:
            result["body"] = result["content"]
    return results


async def note_search(
    pool: asyncpg.Pool, query: str, contact_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """Search notes by body/title content (ILIKE), optionally scoped to a contact."""
    note_cols = await table_columns(pool, "notes")
    contact_cols = await table_columns(pool, "contacts")
    name_sql = contact_name_expr(contact_cols, alias="c")

    predicates = []
    if "body" in note_cols:
        predicates.append("n.body ILIKE '%' || $1 || '%'")
    if "content" in note_cols:
        predicates.append("n.content ILIKE '%' || $1 || '%'")
    if "title" in note_cols:
        predicates.append("n.title ILIKE '%' || $1 || '%'")
    where_text = " OR ".join(predicates)

    if contact_id is not None:
        rows = await pool.fetch(
            f"""
            SELECT n.*, {name_sql} AS contact_name
            FROM notes n
            JOIN contacts c ON n.contact_id = c.id
            WHERE n.contact_id = $2 AND ({where_text})
            ORDER BY n.created_at DESC
            """,
            query,
            contact_id,
        )
    else:
        rows = await pool.fetch(
            f"""
            SELECT n.*, {name_sql} AS contact_name
            FROM notes n
            JOIN contacts c ON n.contact_id = c.id
            WHERE ({where_text})
            ORDER BY n.created_at DESC
            """,
            query,
        )
    results = [dict(row) for row in rows]
    for result in results:
        if "content" not in result and "body" in result:
            result["content"] = result["body"]
        if "body" not in result and "content" in result:
            result["body"] = result["content"]
    return results
