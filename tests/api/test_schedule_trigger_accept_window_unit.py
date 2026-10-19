"""A schedule trigger that outlasts the accept window answers 202 "running" (bu-iw3s5w).

``schedule_trigger`` runs the dispatch to completion, which for a prompt-mode schedule is a whole
LLM session, while the browser gives up on a request after 15 s and would toast the trigger as
failed. The route therefore waits only ``_TRIGGER_ACCEPT_WINDOW_S``; past that it answers 202
``running`` and the trigger finishes in the background, which audits the outcome exactly once.
A reply inside the window keeps its status mapping.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from butlers.api.deps import get_mcp_manager
from butlers.api.routers import schedules as schedules_module
from butlers.api.routers.schedules import _get_db_manager
from tests.api.auth_helpers import create_authenticated_domain_app as create_app

pytestmark = pytest.mark.unit

_HEADERS = {"X-API-Key": "synthetic-owner-key"}
_SCHEDULE_ID = uuid.uuid4()
_TRIGGERED = {"id": str(_SCHEDULE_ID), "status": "triggered", "name": "daily"}


@pytest.fixture(scope="module")
def app():
    return create_app(api_key="synthetic-owner-key")


@pytest.fixture(autouse=True)
def clear_overrides(app):
    yield
    app.dependency_overrides.clear()


async def _post_trigger(app, tool, *, window: float = 0.05, release: asyncio.Event | None = None):
    app.dependency_overrides[get_mcp_manager] = lambda: MagicMock()
    app.dependency_overrides[_get_db_manager] = lambda: MagicMock()
    audit = AsyncMock()
    with (
        patch("butlers.api.routers.schedules._call_mcp_tool", tool),
        patch("butlers.api.routers.schedules.log_audit_entry", audit),
        patch("butlers.api.routers.schedules._TRIGGER_ACCEPT_WINDOW_S", window),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            resp = await http.post(
                f"/api/butlers/qa/schedules/{_SCHEDULE_ID}/trigger", headers=_HEADERS
            )
        audit.awaits_at_response = audit.await_count
        if release is not None:
            # Let the background trigger finish while the audit patch is still active.
            release.set()
            await _drain()
        return resp, audit


async def _drain() -> None:
    await asyncio.gather(*schedules_module._trigger_tasks, return_exceptions=True)


async def test_a_trigger_past_the_window_answers_202_running_without_failing(app) -> None:
    release = asyncio.Event()

    async def slow_tool(*_args, **_kwargs):
        await release.wait()
        return _TRIGGERED

    resp, audit = await _post_trigger(app, slow_tool, release=release)

    assert resp.status_code == 202
    assert resp.json()["data"] == {"id": str(_SCHEDULE_ID), "status": "running"}
    assert audit.awaits_at_response == 0


async def test_the_background_finish_audits_the_outcome_exactly_once(app) -> None:
    release = asyncio.Event()

    async def slow_tool(*_args, **_kwargs):
        await release.wait()
        return _TRIGGERED

    _resp, audit = await _post_trigger(app, slow_tool, release=release)

    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    assert args[2] == "schedule.trigger"
    assert args[3]["observed_status"] == "triggered"
    assert kwargs["result"] == "success"


async def test_a_failure_after_the_window_is_audited_as_an_error_and_not_raised(app) -> None:
    release = asyncio.Event()

    async def slow_failure(*_args, **_kwargs):
        await release.wait()
        raise HTTPException(status_code=503, detail="butler unreachable")

    resp, audit = await _post_trigger(app, slow_failure, release=release)
    assert resp.status_code == 202

    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    assert args[3]["observed_status"] == "unavailable"
    assert kwargs["result"] == "error"
    assert not schedules_module._trigger_tasks


async def test_a_reply_inside_the_window_keeps_its_status_mapping(app) -> None:
    resp, audit = await _post_trigger(app, AsyncMock(return_value=_TRIGGERED), window=5.0)

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "triggered"
    audit.assert_awaited_once()


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        ({"status": "busy", "message": "m"}, 409),
        ({"status": "error", "code": "SCHEDULE_NOT_FOUND"}, 404),
        ({"status": "error", "error": "boom"}, 502),
    ],
)
async def test_fast_refusals_inside_the_window_are_not_turned_into_202(app, reply, status) -> None:
    resp, _audit = await _post_trigger(app, AsyncMock(return_value=reply), window=5.0)

    assert resp.status_code == status


async def test_a_fast_unreachable_error_inside_the_window_is_still_a_503(app) -> None:
    tool = AsyncMock(side_effect=HTTPException(status_code=503, detail="butler unreachable"))

    resp, audit = await _post_trigger(app, tool, window=5.0)

    assert resp.status_code == 503
    audit.assert_awaited_once()
