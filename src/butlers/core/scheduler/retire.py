"""Delete a scheduled task without leaving a provider calendar ghost (bu-5ii4gj.4).

A task whose calendar mirror exists, or may exist, cannot simply be deleted: the row carries the
only link to the provider event, so removing it strands a Google event nothing can ever clean up.
Such a row is retired instead: disabled, hidden, and kept in ``mirror_state = 'retiring'`` with
``delete_requested_at`` set until the calendar mirror has deleted the provider copy and purged it.
A task that was never mirrored is deleted outright.
"""

from __future__ import annotations

import logging
import uuid

import asyncpg

logger = logging.getLogger(__name__)

# The name is unique per butler; the retiring row gives its name back so the owner can reuse it.
_RETIRE_SQL = """
UPDATE scheduled_tasks
SET enabled = false,
    mirror_state = 'retiring',
    mirror_attempts = 0,
    mirror_error = NULL,
    delete_requested_at = now(),
    name = left(name, 200) || ' [deleted ' || left(id::text, 8) || ']',
    updated_at = now()
WHERE id = $1
  AND delete_requested_at IS NULL
  AND (calendar_event_id IS NOT NULL OR mirror_attempted_at IS NOT NULL)
RETURNING id
"""


async def delete_or_retire_scheduled_task(pool: asyncpg.Pool, task_id: uuid.UUID) -> bool:
    """Delete ``task_id``, or retire it when a provider copy may exist.

    Returns ``True`` when the row was retired (the mirror purges it after deleting the provider
    copy) and ``False`` when it was deleted outright.
    """
    retired = await pool.fetchrow(_RETIRE_SQL, task_id)
    if retired is not None:
        logger.info("Retired scheduled task %s until its calendar mirror is deleted", task_id)
        return True
    await pool.execute(
        "DELETE FROM scheduled_tasks WHERE id = $1 AND delete_requested_at IS NULL", task_id
    )
    return False


async def delete_or_retire_scheduled_task_by_name(pool: asyncpg.Pool, name: str) -> bool:
    """:func:`delete_or_retire_scheduled_task` for a caller that knows the task by name.

    Returns ``False`` when no live task has that name.
    """
    task_id = await pool.fetchval(
        "SELECT id FROM scheduled_tasks WHERE name = $1 AND delete_requested_at IS NULL", name
    )
    if task_id is None:
        return False
    return await delete_or_retire_scheduled_task(pool, task_id)
