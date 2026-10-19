"""``POST /api/butlers/{name}/tick`` exposes the dispatched count as a typed field (bu-5s42ls).

A ran tick's ``dispatched`` is an int the dashboard can render without parsing prose.
``message`` is unchanged: the tick tool's reply text for a ran reply.
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


@pytest.fixture(scope="module")
def app():
    return create_app(api_key="synthetic-owner-key")


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


async def _post_tick(app, reply_text: str) -> dict:
    client = MagicMock()
    client.call_tool = AsyncMock(
        return_value=SimpleNamespace(content=[SimpleNamespace(text=reply_text)])
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


@pytest.mark.parametrize("count", [0, 1, 3])
async def test_a_ran_tick_exposes_the_count_and_keeps_the_tool_text(app, count) -> None:
    reply_text = json.dumps({"dispatched": count})
    data = await _post_tick(app, reply_text)

    assert data["dispatched"] == count
    assert data["message"] == reply_text
    assert data["success"] is True
    assert data["busy"] is False


async def test_a_busy_tick_has_no_dispatched_count(app) -> None:
    data = await _post_tick(app, json.dumps({"status": "busy", "dispatched": 0, "message": "wait"}))

    assert data["busy"] is True
    assert data["dispatched"] is None
    assert data["message"] == "wait"


@pytest.mark.parametrize(
    "reply_text",
    [
        "not json at all",
        json.dumps({"ok": True}),
        json.dumps({"dispatched": "2"}),
        json.dumps({"dispatched": True}),
        json.dumps({"dispatched": 2.5}),
        json.dumps([1, 2]),
    ],
)
async def test_a_reply_without_an_integer_count_keeps_its_text_and_has_no_count(
    app, reply_text
) -> None:
    data = await _post_tick(app, reply_text)

    assert data["dispatched"] is None
    assert data["message"] == reply_text
    assert data["success"] is True
