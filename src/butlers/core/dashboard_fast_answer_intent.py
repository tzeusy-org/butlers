"""Monotonic lane persistence for the current registered Switchboard tool path."""

from __future__ import annotations

from typing import Any
from uuid import UUID


async def persist_dashboard_intent(ctx: Any, message_id: UUID | None, lane: str) -> None:
    """Legacy isolated runtimes retain null; the started current owner must persist."""
    from butlers.core.dashboard_fast_answer_runtime import DashboardFastAnswerRuntime

    runtime = getattr(ctx.daemon, "_dashboard_fast_answer_runtime", None)
    if message_id is not None and isinstance(runtime, DashboardFastAnswerRuntime):
        await runtime.store.intent(message_id, lane)
