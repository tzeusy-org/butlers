"""The dashboard's control-plane carrier (RFC 0038 D7, mechanism A).

``butlers.api.app`` hands :func:`carry_via_control_plane` to
``init_dependencies`` so every ``DashboardButlerClient`` carries its
model-reachable calls through Switchboard. It lives apart from
``butlers.api.deps`` because deps sits inside the relationship Finder's no-LLM
import walk (``roster/relationship/tests/test_finder_no_llm_transitive.py``)
and the signed-call client reaches the runtime graph. Nothing on that walk may
import this module.
"""

from __future__ import annotations

import datetime
from typing import Any

import mcp.types

from butlers.api.deps import ButlerUnreachableError
from butlers.core.service_plane import dashboard_client


async def carry_via_control_plane(
    butler_name: str,
    tool: str,
    arguments: dict[str, Any],
    request_timeout: datetime.timedelta | float | int | None,
) -> mcp.types.CallToolResult:
    """Carry one call through Switchboard; unreachable when it cannot be carried."""
    try:
        return await dashboard_client.call_via_control_plane(
            butler_name, tool, arguments, request_timeout=request_timeout
        )
    except dashboard_client.ServiceCallUnavailable as exc:
        raise ButlerUnreachableError(butler_name, cause=exc) from exc
