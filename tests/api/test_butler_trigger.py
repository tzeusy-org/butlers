"""Tests for POST /api/butlers/{name}/trigger — trigger runtime session.

Verifies trigger endpoint sends prompt via MCP call_tool("trigger", ...),
returns structured TriggerResponse, and handles error cases (404, 503).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    get_butler_configs,
    get_mcp_manager,
)
from butlers.api.models import TriggerResponse
from butlers.api.routers.butlers import _get_db_manager

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_trigger_result(
    session_id: str = "sess-001",
    success: bool = True,
    output: str = "Task completed.",
) -> MagicMock:
    """Create a mock CallToolResult from the trigger() MCP tool."""
    data = {
        "session_id": session_id,
        "success": success,
        "output": output,
    }
    content_block = MagicMock()
    content_block.text = json.dumps(data)
    result = MagicMock()
    result.content = [content_block]
    result.is_error = False
    return result


def _mock_mcp_manager(
    trigger_result: MagicMock | None = None,
    *,
    unreachable: bool = False,
    timeout: bool = False,
) -> MCPClientManager:
    """Create a mock MCPClientManager for trigger tests."""
    mgr = MagicMock(spec=MCPClientManager)
    if unreachable:
        mgr.get_client = AsyncMock(
            side_effect=ButlerUnreachableError("test", cause=ConnectionRefusedError("refused"))
        )
    elif timeout:
        mgr.get_client = AsyncMock(side_effect=TimeoutError("timed out"))
    else:
        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=trigger_result)
        mgr.get_client = AsyncMock(return_value=mock_client)
    return mgr


def _create_test_app(
    configs: list[ButlerConnectionInfo],
    mcp_manager: MCPClientManager,
):
    """Create a FastAPI test app with dependency overrides."""
    # Mock DatabaseManager for audit logging
    mock_audit_pool = AsyncMock()
    mock_audit_pool.execute = AsyncMock()
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = mock_audit_pool

    app = create_app()
    app.dependency_overrides[get_butler_configs] = lambda: configs
    app.dependency_overrides[get_mcp_manager] = lambda: mcp_manager
    app.dependency_overrides[_get_db_manager] = lambda: mock_db
    return app


# ---------------------------------------------------------------------------
# POST /api/butlers/{name}/trigger endpoint tests
# ---------------------------------------------------------------------------


class TestTriggerButlerEndpoint:
    async def test_returns_404_for_unknown_butler(self):
        """Unknown butler name returns 404."""
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/nonexistent/trigger",
                json={"prompt": "hello"},
            )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_successful_trigger(self):
        """Successful trigger returns session result."""
        configs = [ButlerConnectionInfo("general", 8101)]
        trigger_result = _mock_trigger_result(
            session_id="sess-abc",
            success=True,
            output="Completed successfully.",
        )
        mgr = _mock_mcp_manager(trigger_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={"prompt": "do something"},
            )

        assert response.status_code == 200
        body = response.json()
        assert "data" in body
        assert "meta" in body

        data = body["data"]
        assert data["session_id"] == "sess-abc"
        assert data["success"] is True
        assert data["output"] == "Completed successfully."

        # Verify the mock was called correctly
        mock_client = await mgr.get_client("general")
        mock_client.call_tool.assert_called_once_with("trigger", {"prompt": "do something"})

    async def test_returns_503_when_butler_unreachable(self):
        """Returns 503 when butler MCP server is unreachable."""
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(unreachable=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={"prompt": "hello"},
            )

        assert response.status_code == 503
        assert "unreachable" in response.json()["detail"].lower()

    async def test_returns_503_on_timeout(self):
        """Returns 503 when trigger request times out."""
        configs = [ButlerConnectionInfo("general", 8101)]
        mgr = _mock_mcp_manager(timeout=True)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={"prompt": "hello"},
            )

        assert response.status_code == 503
        assert "timed out" in response.json()["detail"].lower()

    async def test_response_shape_matches_model(self):
        """Verify response data can be parsed as TriggerResponse."""
        configs = [ButlerConnectionInfo("general", 8101)]
        trigger_result = _mock_trigger_result()
        mgr = _mock_mcp_manager(trigger_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={"prompt": "test"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        trigger = TriggerResponse.model_validate(data)
        assert trigger.success is True
        assert trigger.session_id is not None

    async def test_trigger_with_failed_session(self):
        """Trigger that returns success=False is reflected in response."""
        configs = [ButlerConnectionInfo("general", 8101)]
        trigger_result = _mock_trigger_result(
            session_id="sess-fail",
            success=False,
            output="Error: something went wrong",
        )
        mgr = _mock_mcp_manager(trigger_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={"prompt": "do failing thing"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is False
        assert data["output"] == "Error: something went wrong"

    async def test_trigger_requires_prompt_field(self):
        """Request without prompt field returns 422 validation error."""
        configs = [ButlerConnectionInfo("general", 8101)]
        trigger_result = _mock_trigger_result()
        mgr = _mock_mcp_manager(trigger_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={},
            )

        assert response.status_code == 422

    async def test_trigger_with_is_error_flag(self):
        """When MCP result has is_error=True, success should be False."""
        configs = [ButlerConnectionInfo("general", 8101)]
        trigger_result = _mock_trigger_result(
            session_id="sess-err",
            success=True,
            output="Some output",
        )
        trigger_result.is_error = True
        mgr = _mock_mcp_manager(trigger_result)
        app = _create_test_app(configs, mgr)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/butlers/general/trigger",
                json={"prompt": "test"},
            )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["success"] is False
