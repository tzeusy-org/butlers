"""``POST /api/butlers/{name}/schedules/{id}/trigger`` is truthful when a tick is running (bu-kcgfqb).

The ``schedule_trigger`` tool answers ``{"status": "busy"}`` and dispatches nothing while a
scheduler tick is running. The endpoint must not present that as a successful trigger (the
UI toasts "triggered" on any 2xx): it answers 409 with the tool's message. The audit row
for the refused trigger keeps ``result="success"`` (an ``error`` row would be grouped into
an Issue) and records the outcome as ``observed_status="busy"``.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from butlers.api.deps import get_mcp_manager
from butlers.api.routers.schedules import _get_db_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_HEADERS = {"X-API-Key": "synthetic-owner-key"}
_SCHEDULE_ID = uuid.uuid4()
_BUSY = {"id": str(_SCHEDULE_ID), "status": "busy", "message": "a scheduler tick is running"}
_TRIGGERED = {"id": str(_SCHEDULE_ID), "status": "triggered", "name": "insight-urgent"}


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
    audit = AsyncMock()
    with (
        patch("butlers.api.routers.schedules._call_mcp_tool", AsyncMock(return_value=tool_reply)),
        patch("butlers.api.routers.schedules.log_audit_entry", audit),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            resp = await http.post(
                f"/api/butlers/qa/schedules/{_SCHEDULE_ID}/trigger", headers=_HEADERS
            )
    return resp, audit


async def test_a_busy_reply_is_a_409_not_a_successful_trigger(app) -> None:
    resp, _audit = await _post_trigger(app, _BUSY)

    assert resp.status_code == 409
    assert "tick is running" in resp.json()["detail"]


async def test_a_triggered_reply_is_still_a_200(app) -> None:
    resp, _audit = await _post_trigger(app, _TRIGGERED)

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "triggered"


async def test_a_refused_trigger_is_audited_once_as_success_with_a_busy_outcome(app) -> None:
    _resp, audit = await _post_trigger(app, _BUSY)

    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    assert args[2] == "schedule.trigger"
    assert args[3]["observed_status"] == "busy"
    assert kwargs["result"] == "success"
    assert kwargs.get("error") is None
