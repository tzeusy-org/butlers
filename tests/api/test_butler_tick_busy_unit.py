"""``POST /api/butlers/{name}/tick`` reports a refused (busy) tick as unsuccessful (bu-jlrqju).

The ``tick`` tool answers ``{"status": "busy", ...}`` when a tick is already running on the
daemon. The endpoint must pass that on as ``success: false`` (not the old unconditional
``success: true``), and the audit row for a refused tick keeps ``result="success"`` (an
``error`` row would be grouped into an Issue) with the outcome in its metadata.
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
_BUSY = {"status": "busy", "dispatched": 0, "message": "a scheduler tick is already running"}


@pytest.fixture(scope="module")
def app():
    return create_app(api_key="synthetic-owner-key")


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


def _wire(app, tool_reply: dict) -> None:
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


async def _post_tick(app) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post("/api/butlers/qa/tick", headers=_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_a_busy_tool_reply_is_reported_as_unsuccessful(app) -> None:
    _wire(app, _BUSY)
    with patch("butlers.api.routers.butlers.log_audit_entry", AsyncMock()):
        data = await _post_tick(app)

    assert data["success"] is False
    assert "already running" in data["message"]


async def test_a_ran_tool_reply_is_reported_as_successful(app) -> None:
    _wire(app, {"dispatched": 2})
    with patch("butlers.api.routers.butlers.log_audit_entry", AsyncMock()):
        data = await _post_tick(app)

    assert data["success"] is True


@pytest.mark.parametrize(
    ("reply", "outcome"), [(_BUSY, "busy"), ({"dispatched": 1}, "ran")], ids=["busy", "ran"]
)
async def test_the_audit_row_records_the_outcome_without_an_error_result(
    app, reply: dict, outcome: str
) -> None:
    _wire(app, reply)
    audit = AsyncMock()
    with patch("butlers.api.routers.butlers.log_audit_entry", audit):
        await _post_tick(app)

    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    assert args[1:3] == ("qa", "tick")
    assert args[3]["tick_outcome"] == outcome
    assert kwargs.get("result", "success") == "success"
    assert "error" not in kwargs
