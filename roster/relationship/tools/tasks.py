"""Tasks — create, list, complete, and delete tasks scoped to contacts backed by SPO facts.

Each task is a property fact in the facts table (supersession by subject key):
  subject   = contact:{contact_id}:task:{task_uuid}
  predicate = 'contact_task'
  content   = title
  metadata  = {description, completed, completed_at}
  valid_at  = NULL (property fact — complete/delete updates supersede)
  scope     = 'relationship'
  entity_id = contact's entity UUID (resolved via contacts.entity_id)

The response shape is backward compatible with the legacy tasks table.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg

from butlers.tools.relationship.feed import _log_activity

logger = logging.getLogger(__name__)

_embedding_engine: Any = None


def _get_embedding_engine() -> Any:
    """Lazy-load and return the shared EmbeddingEngine singleton."""
    global _embedding_engine
    if _embedding_engine is None:
        from butlers.modules.memory.tools import get_embedding_engine

        _embedding_engine = get_embedding_engine()
    return _embedding_engine


def _extract_contact_id(subject: str) -> uuid.UUID | None:
    """Extract contact_id from subject string 'contact:{uuid}:task:{uuid}'."""
    parts = subject.split(":")
    if len(parts) >= 2:
        try:
            return uuid.UUID(parts[1])
        except ValueError:
            pass
    return None


def _fact_to_task(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a facts row to the tasks API shape."""
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)
    contact_id = _extract_contact_id(row.get("subject", ""))
    completed_at_str = meta.get("completed_at")
    completed_at = None
    if completed_at_str:
        try:
            completed_at = datetime.fromisoformat(completed_at_str)
        except (ValueError, TypeError):
            pass
    return {
        "id": row["id"],
        "contact_id": contact_id,
        "title": row.get("content", ""),
        "description": meta.get("description"),
        "completed": meta.get("completed", False),
        "completed_at": completed_at,
        "created_at": row.get("created_at"),
    }


async def task_create(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID,
    title: str,
    description: str | None = None,
) -> dict[str, Any]:
    """Create a task/to-do scoped to a contact."""
    from butlers.modules.memory.storage import store_fact

    now = datetime.now(UTC)
    embedding_engine = _get_embedding_engine()

    # Unique task subject per creation — tasks don't supersede each other
    task_uuid = uuid.uuid4()
    subject = f"contact:{contact_id}:task:{task_uuid}"

    fact_metadata: dict[str, Any] = {"completed": False}
    if description is not None:
        fact_metadata["description"] = description

    fact_id = (
        await store_fact(
            pool,
            subject=subject,
            predicate="contact_task",
            content=title,
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=None,  # None so supersession uses subject key (per-task)
            valid_at=None,  # property fact — complete/delete updates will supersede
            metadata=fact_metadata,
        )
    )["id"]

    result: dict[str, Any] = {
        "id": fact_id,
        "contact_id": contact_id,
        "title": title,
        "description": description,
        "completed": False,
        "completed_at": None,
        "created_at": now,
    }
    await _log_activity(pool, contact_id, "task_created", f"Created task: '{title}'")
    return result


async def task_list(
    pool: asyncpg.Pool,
    contact_id: uuid.UUID | None = None,
    include_completed: bool = False,
) -> list[dict[str, Any]]:
    """List tasks, optionally filtered by contact and completion status."""
    conditions = [
        "f.predicate = 'contact_task'",
        "f.scope = 'relationship'",
        "f.validity = 'active'",
        "f.valid_at IS NULL",
    ]
    params: list[Any] = []
    idx = 1

    if contact_id is not None:
        conditions.append(f"f.subject LIKE ${idx}")
        params.append(f"contact:{contact_id}:task:%")
        idx += 1

    if not include_completed:
        conditions.append(
            "((f.metadata->>'completed')::boolean = false OR f.metadata->>'completed' IS NULL)"
        )  # noqa: E501

    where = " AND ".join(conditions)

    # Join contacts to add contact_name for task_list without contact_id filter
    if contact_id is not None:
        # contact_id is passed as the last param; its index is len(params)+1
        cid_idx = len(params) + 1
        rows = await pool.fetch(
            f"""
            SELECT f.id, f.subject, f.content, f.created_at, f.metadata,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ',
                           COALESCE(c.first_name, ''),
                           COALESCE(c.last_name, '')
                       )), ''),
                       c.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM facts f
            JOIN contacts c ON c.id = ${cid_idx}
            WHERE {where}
            ORDER BY f.created_at DESC
            """,
            *params,
            contact_id,
        )
    else:
        params_for_join: list[Any] = list(params)
        rows = await pool.fetch(
            f"""
            SELECT f.id, f.subject, f.content, f.created_at, f.metadata,
                   COALESCE(
                       NULLIF(TRIM(CONCAT_WS(' ',
                           COALESCE(c.first_name, ''),
                           COALESCE(c.last_name, '')
                       )), ''),
                       c.nickname,
                       'Unknown'
                   ) AS contact_name
            FROM facts f
            JOIN contacts c ON f.subject LIKE 'contact:' || c.id::text || ':task:%'
            WHERE {where}
            ORDER BY f.created_at DESC
            """,
            *params_for_join,
        )

    results = []
    for row in rows:
        d = _fact_to_task(dict(row))
        d["contact_name"] = row["contact_name"]
        results.append(d)
    return results


async def task_complete(pool: asyncpg.Pool, task_id: uuid.UUID) -> dict[str, Any]:
    """Mark a task as completed."""
    from butlers.modules.memory.storage import store_fact

    row = await pool.fetchrow(
        "SELECT id, subject, content, metadata, entity_id FROM facts WHERE id = $1",
        task_id,
    )
    if row is None:
        raise ValueError(f"Task {task_id} not found")

    meta = row["metadata"] or {}
    if isinstance(meta, str):
        meta = json.loads(meta)

    now = datetime.now(UTC)
    new_metadata = dict(meta)
    new_metadata["completed"] = True
    new_metadata["completed_at"] = now.isoformat()

    embedding_engine = _get_embedding_engine()

    new_fact_id = (
        await store_fact(
            pool,
            subject=row["subject"],
            predicate="contact_task",
            content=row["content"],
            embedding_engine=embedding_engine,
            permanence="stable",
            scope="relationship",
            entity_id=None,  # None so supersession uses subject key (per-task)
            valid_at=None,  # property fact — supersedes previous
            metadata=new_metadata,
        )
    )["id"]

    contact_id = _extract_contact_id(row["subject"])
    result: dict[str, Any] = {
        "id": new_fact_id,
        "contact_id": contact_id,
        "title": row["content"],
        "description": new_metadata.get("description"),
        "completed": True,
        "completed_at": now,
        "created_at": now,
        "updated_at": now,
    }
    if contact_id is not None:
        await _log_activity(
            pool, contact_id, "task_completed", f"Completed task: '{row['content']}'"
        )
    return result


async def task_delete(pool: asyncpg.Pool, task_id: uuid.UUID) -> None:
    """Delete a task (mark as retracted)."""
    row = await pool.fetchrow(
        "SELECT id, subject, content FROM facts WHERE id = $1 AND predicate = 'contact_task'",
        task_id,
    )
    if row is None:
        raise ValueError(f"Task {task_id} not found")

    await pool.execute(
        "UPDATE facts SET validity = 'retracted' WHERE id = $1",
        task_id,
    )

    contact_id = _extract_contact_id(row["subject"])
    if contact_id is not None:
        await _log_activity(pool, contact_id, "task_deleted", f"Deleted task: '{row['content']}'")
