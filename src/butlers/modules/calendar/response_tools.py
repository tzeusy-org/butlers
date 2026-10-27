"""Calendar response tools implementation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from butlers.modules.base import group_enabled
from butlers.modules.calendar_response_fence import RESPONSE_FENCE_OWNERS

if TYPE_CHECKING:
    from . import CalendarModule


def register_response_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:
    if butler_name in RESPONSE_FENCE_OWNERS:

        @mcp.tool()
        async def calendar_response_fence_status(
            request: dict[str, Any], trace_context: dict[str, str] | None = None
        ) -> dict[str, Any]:
            """Read this owner's unresolved response fence under a live same-DB claim.

            Returns clear/unresolved/unavailable with no provider or approval effect.
            Missing protocol, inventory or live lock proof is unavailable, never clear.
            Switchboard trace_context is transport metadata, never authority.
            """
            return await module._response_fence.status(request)

    if module._config.response_enabled:
        if not group_enabled(module._config, "response"):
            raise RuntimeError("calendar response requires its response registration group")

        @mcp.tool(exclude_args=["_command_id", "_command_digest"])
        async def calendar_respond(
            entry_id: str,
            response_status: Literal["accepted", "declined", "tentative"],
            request_id: str,
            send_updates: Literal["none", "all", "externalOnly"] = "none",
            _command_id: str | None = None,
            _command_digest: str | None = None,
        ) -> dict[str, Any]:
            """Respond only to the verified self attendee after exact owner approval.

            The gate prepares the immutable occurrence command. A direct
            call cannot authorize an effect or pass private command bindings.
            """
            arguments = dict(
                entry_id=entry_id,
                response_status=response_status,
                request_id=request_id,
                send_updates=send_updates,
                _command_id=_command_id,
                _command_digest=_command_digest,
            )
            result = await module._response_coordinator.execute(arguments)
            if result.get("status") in {"failed", "uncertain"}:
                # A recorded closed outcome is not a successful approved
                # execution. Preserve the durable Calendar receipt while
                # keeping this approval retryable behind its attempt fence.
                return {**result, "success": False}
            return result
