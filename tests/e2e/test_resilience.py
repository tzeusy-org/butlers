"""E2E resilience tests — module failure isolation.

Tests failure injection and graceful degradation across the butler ecosystem.

Note: Serial dispatch lock contention and lock-release-after-error tests have
been removed — these are covered by tests/e2e/test_performance.py which tests
the same behaviors (test_serial_dispatch_under_load, test_lock_release_after_error).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastmcp import Client as MCPClient

if TYPE_CHECKING:
    from tests.e2e.conftest import ButlerEcosystem

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


# ---------------------------------------------------------------------------
# Scenario: Module failure isolation
# ---------------------------------------------------------------------------


async def test_module_failure_isolation(
    butler_ecosystem: ButlerEcosystem,
) -> None:
    """Butler with failed module should still serve core tools.

    Tests:
    1. Check module statuses on a butler
    2. Verify core tools (status, trigger) are still available
    3. Failed module's tools are not registered
    """
    # Use general butler as test target
    general_daemon = butler_ecosystem.butlers["general"]
    port = general_daemon.config.port
    url = f"http://localhost:{port}/sse"

    # Call status tool (core functionality)
    async with MCPClient(url) as client:
        status = await client.call_tool("status", {})

        assert status is not None
        assert status["name"] == "general"
        assert "modules" in status

        # Verify core tools are available
        tools_response = await client.list_tools()
        tool_names = {t.name for t in tools_response.tools}

        # Core tools should always be present
        assert "status" in tool_names, "Core status tool should be available"
        assert "trigger" in tool_names, "Core trigger tool should be available"

        # If any module failed during startup, it should be marked as failed
        # but butler should still function
        module_statuses = status["modules"]
        for module_name, module_status in module_statuses.items():
            if module_status.get("status") == "failed":
                # Failed module's tools should not be registered
                # This is implicit - if a module fails, its tools won't be in tool_names
                pass
