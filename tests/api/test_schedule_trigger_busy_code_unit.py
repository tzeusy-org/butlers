"""The busy 409 from ``POST /api/butlers/{name}/schedules/{id}/trigger`` carries an error code (bu-3fle50).

The dashboard branches on an error code, never on the status or the message text. The busy body
carries ``error.code == "scheduler_busy"`` (the ErrorResponse envelope, as the chronicler refresh
does) and keeps the FastAPI ``detail`` string for clients that read it. Other non-2xx replies carry no
such code.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.deps import get_mcp_manager
from butlers.api.routers.schedules import _get_db_manager
from butlers.core.tick_guard import BUSY_MESSAGE
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_HEADERS = {"X-API-Key": "synthetic-owner-key"}
_SCHEDULE_ID = uuid.uuid4()
_MESSAGE = BUSY_MESSAGE


@pytest.fixture(scope="module")
def app():
    return create_app(api_key="synthetic-owner-key")


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


async def _post_trigger(app, tool_reply: dict):
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[_get_db_manager] = lambda: MagicMock()
    with (
        patch("butlers.api.routers.schedules._call_mcp_tool", AsyncMock(return_value=tool_reply)),
        patch("butlers.api.routers.schedules.log_audit_entry", AsyncMock()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            return await http.post(
                f"/api/butlers/qa/schedules/{_SCHEDULE_ID}/trigger", headers=_HEADERS
            )


async def test_a_busy_reply_is_a_409_with_the_scheduler_busy_code_and_message(app) -> None:
    resp = await _post_trigger(app, {"status": "busy", "message": _MESSAGE})

    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "scheduler_busy"
    assert error["message"] == _MESSAGE
    assert error["butler"] == "qa"


async def test_a_busy_reply_passes_the_tools_own_message_through(app) -> None:
    tool_message = "a distinct message from the tool, not the fallback"
    resp = await _post_trigger(app, {"status": "busy", "message": tool_message})

    assert tool_message != BUSY_MESSAGE
    assert resp.json()["error"]["message"] == tool_message
    assert resp.json()["detail"] == tool_message


async def test_a_busy_reply_keeps_the_detail_string(app) -> None:
    resp = await _post_trigger(app, {"status": "busy", "message": _MESSAGE})

    assert resp.json()["detail"] == _MESSAGE


async def test_a_busy_reply_without_a_message_still_carries_the_code(app) -> None:
    resp = await _post_trigger(app, {"status": "busy"})

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "scheduler_busy"
    assert "already running" in resp.json()["error"]["message"]


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ({"status": "error", "code": "SCHEDULE_NOT_FOUND"}, 404),
        ({"status": "error", "error": "spawner exploded"}, 502),
        ({"status": "weird"}, 502),
    ],
)
async def test_other_refusals_carry_no_busy_code(app, reply, status) -> None:
    resp = await _post_trigger(app, reply)

    assert resp.status_code == status
    assert "error" not in resp.json()
    assert isinstance(resp.json()["detail"], str)


async def test_a_triggered_reply_is_a_200_without_an_error_body(app) -> None:
    resp = await _post_trigger(app, {"id": str(_SCHEDULE_ID), "status": "triggered"})

    assert resp.status_code == 200
    assert "error" not in resp.json()
