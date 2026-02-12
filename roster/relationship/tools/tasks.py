"""Tasks — create, list, complete, and delete tasks scoped to contacts."""

from __future__ import annotations

import uuid
from typing import Any

import asyncpg

from butlers.tools.relationship._schema import contact_name_expr, table_columns
from butlers.tools.relationship.feed import _log_activity


async def task_create(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    title: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a task/to-do scoped to a contact."""
    row = await pool.fetchrow(
        """
        INSERT INTO tasks (contact_id, title, description)
        VALUES ($1, $2, $3)
        RETURNING *
        """,
        contact_id,
        title,
        description,
    )
    result = dict(row)
    await _log_activity(pool, contact_id, "task_created", f"Created task: '{title}'")
    return result


async def task_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    include_completed: bool = False,
) -> list[dict[str, Any]]:
    """List tasks, optionally filtered by contact and completion status."""
    conditions: list[str] = []
    args: list[Any] = []
    idx = 1

    if contact_id is not None:
        conditions.append(f"t.contact_id = ${idx}")
        args.append(contact_id)
        idx += 1

    if not include_completed:
        conditions.append("t.completed = false")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    contact_cols = await table_columns(pool, "contacts")
    name_sql = contact_name_expr(contact_cols, alias="c")

    rows = await pool.fetch(
        f"""
        SELECT t.*, {name_sql} as contact_name
        FROM tasks t
        JOIN contacts c ON t.contact_id = c.id
        {where}
        ORDER BY t.created_at DESC
        """,
        *args,
    )
    return [dict(row) for row in rows]


async def task_complete(pool: asyncpg.Pool, task_id: uuid.UUID) -> dict[str, Any]:
    """Mark a task as completed."""
    row = await pool.fetchrow(
        """
        UPDATE tasks SET completed = true, completed_at = now()
        WHERE id = $1
        RETURNING *
        """,
        task_id,
    )
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    result = dict(row)
    await _log_activity(
        pool, result["contact_id"], "task_completed", f"Completed task: '{row['title']}'"
    )
    return result


async def task_delete(pool: asyncpg.Pool, task_id: uuid.UUID) -> None:
    """Delete a task."""
    row = await pool.fetchrow(
        "DELETE FROM tasks WHERE id = $1 RETURNING contact_id, title",
        task_id,
    )
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    await _log_activity(pool, row["contact_id"], "task_deleted", f"Deleted task: '{row['title']}'")
