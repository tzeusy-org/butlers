"""Task-continuity core tools: carry_forward (non-STAFFER only).

Backs the general task-continuity ledger (bu-2jtfw.13): a recurring task
opted into ``continuity=true`` calls ``carry_forward`` to record what its run
concluded, and the scheduler's dispatch seam
(:func:`butlers.core.scheduler._continuity_block_for_task`) injects that
record into the next opted-in run's prompt.

``continuity=true`` is restricted to PROMPT-mode schedules (see
``ScheduleConfig`` validation): it exists to carry narrative conclusions
forward for LLM-authored digests and reviews. Every staffer butler
(concierge, messenger, switchboard, qa) currently declares only job-mode
schedules -- deterministic Python handlers, not narrative prompts -- so
``carry_forward`` has no reachable caller there. Gating it out matches the
existing non-STAFFER convention already drawn for the same reason around
other narrative/dispatch-adjacent tools (see ``_notifications.py``'s
``notify``, ``_temporal.py``'s deadline/event_chain/seasonal tools). This is
a registration-time role-fit and reachability decision, independent of RFC
0002 Amendment 1 / RFC 0027's 30-50 target for full definitions initially
loaded into model context; presentation discovery and evidence remain owned
by ``bu-ondtw``.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from butlers.config import ButlerType
from butlers.core.task_continuity import record_carry_forward
from butlers.core.tool_call_capture import get_current_runtime_session_id
from butlers.core_tools._base import ToolContext

logger = logging.getLogger(__name__)


def register_continuity_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    """Register the carry_forward continuity-ledger tool (non-STAFFER only)."""
    if ctx.butler_type == ButlerType.STAFFER:
        return

    pool = ctx.pool
    butler_name = ctx.butler_name

    @_core_tool("continuity")
    async def carry_forward(task_name: str, content: str) -> dict:
        """Record what this recurring task's run concluded, for the next run to read.

        Upserts the single "live" row in ``public.task_continuity`` for
        ``(butler, task_name)``. Calling this more than once in the same
        session updates that same row rather than appending a duplicate
        (keyed by ``(butler_name, task_name, session_id)``); a new session
        calling it supersedes the previous live row. Only a task the
        scheduler dispatched with ``scheduled_tasks.continuity = true`` will
        have this content injected into its next run's prompt.
        """
        if pool is None:
            return {
                "status": "error",
                "code": "no_database",
                "message": "No database pool available",
            }
        if not task_name or not task_name.strip():
            raise ValueError("carry_forward requires a non-empty task_name")
        if not content or not content.strip():
            raise ValueError("carry_forward requires non-empty content")

        session_id_raw = get_current_runtime_session_id()
        if not session_id_raw:
            return {
                "status": "error",
                "code": "no_session_context",
                "message": "carry_forward requires an active runtime session",
            }
        session_id = uuid.UUID(session_id_raw)

        row_id = await record_carry_forward(
            pool,
            butler_name=butler_name,
            task_name=task_name,
            session_id=session_id,
            content=content,
        )

        return {"status": "ok", "id": str(row_id), "task_name": task_name}
