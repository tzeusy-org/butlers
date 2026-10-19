"""``POST /api/butlers/{name}/tick`` exposes busy as an explicit field (bu-0w76ze).

The dashboard must tell "a tick is already running" from a real failure without matching
prose, so the response carries ``busy`` and, when busy, the tick tool's own message (not
its raw JSON reply) in ``message``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.deps import ButlerConnectionInfo, get_butler_configs, get_mcp_manager
from butlers.api.routers.butlers import _get_db_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_HEADERS = {"X-API-Key": "synthetic-owner-key"}
_BUSY_TEXT = "a scheduler tick is already running; try again when it finishes"


@pytest.fixture(scope="module")
def app():
    return create_app(api_key="synthetic-owner-key")


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


async def _post_tick(app, tool_reply: dict) -> dict:
    client = MagicMock()
    client.call_tool = AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(text=json.dumps(tool_reply))])
    )
    manager = MagicMock()
    manager.get_client = AsyncMock(return_value=client)
    app.dependency_overrides[get_mcp_manager] = lambda: manager
    app.dependency_overrides[get_butler_configs] = lambda: [
        ButlerConnectionInfo(name="qa", port=8000)
    ]
    app.dependency_overrides[_get_db_manager] = lambda: MagicMock()
    with patch("butlers.api.routers.butlers.log_audit_entry", AsyncMock()):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            resp = await http.post("/api/butlers/qa/tick", headers=_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_a_busy_reply_sets_busy_and_carries_the_tool_message_not_raw_json(app) -> None:
    data = await _post_tick(app, {"status": "busy", "dispatched": 0, "message": _BUSY_TEXT})

    assert data["busy"] is True
    assert data["success"] is False
    assert data["message"] == _BUSY_TEXT


async def test_a_busy_reply_without_a_message_falls_back_to_the_api_wording(app) -> None:
    data = await _post_tick(app, {"status": "busy", "dispatched": 0})

    assert data["busy"] is True
    assert data["success"] is False
    assert data["message"] == (
        "scheduled work is already running on this butler; try again when it finishes"
    )


async def test_a_ran_reply_is_not_busy_and_reads_in_plain_language(app) -> None:
    data = await _post_tick(app, {"dispatched": 2})

    assert data["busy"] is False
    assert data["success"] is True
    assert data["message"] == "tick ran, 2 jobs dispatched"
