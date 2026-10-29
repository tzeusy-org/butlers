"""GET /api/relationship/meeting-debriefs/pending (bu-q7vx1q.63).

The endpoint returns what ``meeting_debrief_pending`` returns, and a failed read is a degraded
envelope rather than an empty "all answered".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.db import DatabaseManager
from butlers.api.deps import get_db
from butlers.tools.relationship import meeting_debrief as debrief_tools
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_ITEM = {
    "debrief_id": "11111111-1111-1111-1111-111111111111",
    "number": 2,
    "event_title": "Roadmap sync",
    "occurrence_start": "2026-10-18T09:00:00+00:00",
    "prompted_at": "2026-10-18T11:00:00+00:00",
    "attendees": [
        {
            "entity_id": "22222222-2222-2222-2222-222222222222",
            "name": "Sam Example",
            "email": "sam@example.test",
        },
        {"entity_id": None, "name": "Unknown Guest", "email": "guest@example.test"},
    ],
}


def _app(monkeypatch: pytest.MonkeyPatch, pending: AsyncMock) -> FastAPI:
    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.pool.return_value = AsyncMock()
    app = create_app()
    for butler_name, _router_module in app.state.butler_routers:
        if butler_name == "relationship":
            app.dependency_overrides[get_db] = lambda: mock_db
            break
    monkeypatch.setattr(debrief_tools, "meeting_debrief_pending", pending)
    return app


async def _get(app: FastAPI) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get("/api/relationship/meeting-debriefs/pending")


async def test_returns_the_tool_items_in_the_tool_order(monkeypatch: pytest.MonkeyPatch):
    second = {**_ITEM, "debrief_id": "33333333-3333-3333-3333-333333333333", "number": 1}
    pending = AsyncMock(return_value={"debriefs": [_ITEM, second]})

    response = await _get(_app(monkeypatch, pending))

    assert response.status_code == 200
    body = response.json()
    assert body["debriefs_available"] is True
    assert body["debriefs"] == [_ITEM, second]
    pending.assert_awaited_once()


async def test_empty_list_is_available_not_degraded(monkeypatch: pytest.MonkeyPatch):
    response = await _get(_app(monkeypatch, AsyncMock(return_value={"debriefs": []})))

    assert response.json() == {"debriefs": [], "debriefs_available": True}


async def test_read_failure_is_a_degraded_envelope(monkeypatch: pytest.MonkeyPatch):
    pending = AsyncMock(side_effect=RuntimeError("password=hunter2 connection lost"))

    response = await _get(_app(monkeypatch, pending))

    assert response.status_code == 200
    assert response.json() == {"debriefs": [], "debriefs_available": False}
    assert "hunter2" not in response.text


async def test_failure_log_carries_the_exception_class_only(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    pending = AsyncMock(side_effect=ValueError("input_value='sam@example.test' is invalid"))

    with caplog.at_level("WARNING"):
        await _get(_app(monkeypatch, pending))

    assert "ValueError" in caplog.text
    assert "sam@example.test" not in caplog.text
    ours = [r for r in caplog.records if "Pending meeting debriefs" in r.getMessage()]
    assert ours and all(r.exc_info is None for r in ours)
