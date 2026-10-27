"""Shared task-continuity ledger primitive — ``public.task_continuity``.

Backs the ``carry_forward`` core tool (``butlers.core_tools._continuity``) and
the scheduler's dispatch-seam injection
(``butlers.core.scheduler._continuity_block_for_task``). See
``core_229_task_continuity_ledger.py`` for the table shape and its two unique
indexes: ``(butler_name, task_name, session_id)`` for same-session dedup and
the partial ``(butler_name, task_name) WHERE is_live`` for the single live
row per task.
"""

from __future__ import annotations

import uuid
from typing import Any


async def record_carry_forward(
    pool: Any,
    *,
    butler_name: str,
    task_name: str,
    session_id: uuid.UUID,
    content: str,
) -> uuid.UUID:
    """Upsert the live carry-forward row for *(butler_name, task_name)*.

    Calling this twice with the same ``session_id`` updates the same row
    (dedup, keyed by the ``(butler_name, task_name, session_id)`` unique
    index). Calling it with a new ``session_id`` archives the previous live
    row and installs this one as live -- both steps run in one transaction so
    a true concurrent writer serializes on Postgres's row-level locking
    rather than racing two live rows into existence; the partial unique index
    is the hard backstop if that ever fails.
    """
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            UPDATE public.task_continuity
            SET is_live = FALSE
            WHERE butler_name = $1 AND task_name = $2
              AND is_live AND session_id IS DISTINCT FROM $3
            """,
            butler_name,
            task_name,
            session_id,
        )
        row_id = await conn.fetchval(
            """
            INSERT INTO public.task_continuity
                (butler_name, task_name, session_id, carry_forward, is_live, recorded_at)
            VALUES ($1, $2, $3, $4, TRUE, now())
            ON CONFLICT (butler_name, task_name, session_id) DO UPDATE
            SET carry_forward = EXCLUDED.carry_forward,
                is_live = TRUE,
                recorded_at = now()
            RETURNING id
            """,
            butler_name,
            task_name,
            session_id,
            content,
        )
    return row_id


async def fetch_live_carry_forward(
    pool: Any,
    *,
    butler_name: str,
    task_name: str,
) -> Any | None:
    """Return the live ``public.task_continuity`` row for *(butler_name, task_name)*.

    Returns ``None`` when no such row exists.
    """
    return await pool.fetchrow(
        """
        SELECT session_id, carry_forward, recorded_at
        FROM public.task_continuity
        WHERE butler_name = $1 AND task_name = $2 AND is_live
        """,
        butler_name,
        task_name,
    )


__all__ = ["fetch_live_carry_forward", "record_carry_forward"]
