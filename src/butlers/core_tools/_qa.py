"""Always-available, logged core QA relay registration."""

from __future__ import annotations

from typing import Any

from butlers.core.qa.relay import QaRelay
from butlers.core_tools._base import ToolContext


def register_qa_tools(ctx: ToolContext, mcp: Any) -> None:
    """Bind trusted daemon context; neither module nor core-group opt-in is required."""
    relay = QaRelay(ctx.butler_name, ctx.pool, ctx.daemon._qa_switchboard_client)
    relay.register_tools(mcp)
