"""Separate fixed-purpose owner preparation channel. Replies confer no authority.

Only immutable candidate preparation is exposed, never attachment, approval,
execution or caller-selected tool dispatch. The dashboard independently reads
and attaches the candidate under the owning decision locks.
"""

from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import ToolResult
from mcp.types import TextContent

from butlers.core.capability_json import canonical_bytes

from .calendar_prepare_client import (
    PROTOCOL as PROTOCOL,
)
from .calendar_prepare_client import (
    TOOL as TOOL,
)
from .calendar_prepare_client import (
    CalendarPrepareClient as CalendarPrepareClient,
)
from .calendar_prepare_client import (
    CalendarPrepareHTTPTransport as CalendarPrepareHTTPTransport,
)
from .calendar_prepare_client import (
    prepare_reply as prepare_reply,
)
from .calendar_prepare_client import (
    prepare_request as prepare_request,
)
from .calendar_prepare_client import (
    prepare_rpc as prepare_rpc,
)
from .calendar_prepare_client import (
    prepare_rpc_result as prepare_rpc_result,
)
from .config import OPERATION_SECONDS
from .protocol import ProtocolError
from .server import PrivateApp


class CalendarPrepareApp(PrivateApp):
    required_peer_role = "dashboard-owner"

    def __init__(self, domain, coordinator):
        from butlers.modules.calendar_response import CalendarResponseCoordinator

        if type(coordinator) is not CalendarResponseCoordinator:
            raise ValueError("actual_calendar_coordinator_required")
        self.domain, self.coordinator = domain, coordinator
        self.sentinel = object()
        self.mcp = FastMCP("private-calendar-owner-prepare")

        async def prepare(
            protocol: str,
            domain: str,
            root_id: str,
            parent_id: str,
            parent_digest: str,
            expected_args_digest: str,
            edits: dict,
        ) -> ToolResult:
            binding = get_http_request().scope.get("private.broker.peer")
            if (
                type(binding) is not tuple
                or len(binding) != 2
                or binding[0] is not self.sentinel
                or binding[1].role != self.required_peer_role
                or binding[1].domain != self.domain
            ):
                raise ProtocolError("private_peer_required")
            if get_http_request().headers.get("x-butlers-assertion") is not None:
                raise ProtocolError("unexpected_assertion")
            value = prepare_request(
                dict(
                    protocol=protocol,
                    domain=domain,
                    root_id=root_id,
                    parent_id=parent_id,
                    parent_digest=parent_digest,
                    expected_args_digest=expected_args_digest,
                    edits=edits,
                ),
                self.domain,
            )
            try:
                async with asyncio.timeout(OPERATION_SECONDS - 1):
                    result = prepare_reply(
                        await self.coordinator.prepare_owner_candidate(value), value
                    )
            except Exception:
                raise ProtocolError("calendar_prepare_refused") from None
            return ToolResult(
                content=[TextContent(type="text", text=canonical_bytes(result).decode())],
                structured_content=result,
                is_error=False,
            )

        self.mcp.tool(prepare, name=TOOL, output_schema=None)
        self.app = self.mcp.http_app(path="/mcp", stateless_http=True, json_response=True)

    def _validate_rpc(self, value):
        return prepare_rpc(value, self.domain)
