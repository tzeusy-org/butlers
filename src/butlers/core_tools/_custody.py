"""Fixed Switchboard custody definitions in the canonical core dispatcher."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from butlers.core.custody_admission import CustodyMcpService
from butlers.core.custody_source import CustodyError
from butlers.core_tools._base import ToolContext


def register_custody_tools(ctx: ToolContext, mcp: Any, _core_tool: Callable) -> None:
    if not ctx.is_switchboard:
        return

    def service() -> CustodyMcpService:
        # This is the actual core constructor member, not a routing ContextVar
        # or tool argument. Definition/catalog-only callers can have no runtime;
        # their tool remains unavailable and cannot create a principal.
        value = getattr(ctx.daemon, "_custody_mcp_service", None)
        if type(value) is not CustodyMcpService:
            raise CustodyError("unavailable")
        return value

    @mcp.tool(name="custody.challenge", task=False)
    async def custody_challenge(call_ref: str, operation_digest: str) -> dict:
        """Bounded registered source challenge; locators alone grant no authority."""
        return await service().challenge(call_ref, operation_digest)

    @mcp.tool(name="custody.apply", task=False)
    async def custody_apply(wire: str) -> dict:
        """Apply the exact source-bound UTF-8 custody wire after online admission."""
        return await service().apply(wire)
