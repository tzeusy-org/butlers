"""``POST /api/butlers/{name}/schedules/{id}/trigger`` never reports a missed trigger as success (bu-ukf1u0).

The dashboard toasts "triggered" on any 2xx, so a trigger that did not happen must not be a
200. Not found is 404 with detail, a dispatch failure (or any reply other than
``triggered``) is 502 with detail, success stays 200. Each is audited exactly once: a
nonexistent schedule is a refusal (audited as success with ``observed_status=not_found``,
like busy), a real failure is audited as an error.
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


def _only_audit(audit: AsyncMock) -> tuple[dict, dict]:
    audit.assert_awaited_once()
    args, kwargs = audit.await_args
    return args[3], kwargs


async def test_a_missing_schedule_is_404_with_detail_audited_once_as_a_refusal(app) -> None:
    reply = {
        "id": str(_SCHEDULE_ID),
        "status": "error",
        "code": "SCHEDULE_NOT_FOUND",
        "error": "Schedule not found",
    }

    resp, audit = await _post_trigger(app, reply)

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Schedule not found"
    summary, kwargs = _only_audit(audit)
    assert summary["observed_status"] == "not_found"
    assert kwargs["result"] == "success"


async def test_a_dispatch_failure_is_502_with_detail_audited_once_as_an_error(app) -> None:
    reply = {"id": str(_SCHEDULE_ID), "status": "error", "error": "spawner exploded"}

    resp, audit = await _post_trigger(app, reply)

    assert resp.status_code == 502
    assert "spawner exploded" in resp.json()["detail"]
    summary, kwargs = _only_audit(audit)
    assert summary["observed_status"] == "error"
    assert kwargs["result"] == "error"


async def test_a_refusal_from_the_runtime_slot_check_is_502_with_its_reason(app) -> None:
    reply = {
        "id": str(_SCHEDULE_ID),
        "status": "error",
        "code": "SPAWNER_SLOTS_FULL",
        "error": "Every session slot is in use",
    }

    resp, audit = await _post_trigger(app, reply)

    assert resp.status_code == 502
    assert "Every session slot is in use" in resp.json()["detail"]
    _only_audit(audit)


async def test_an_unrecognised_tool_reply_is_502_audited_once_as_an_error(app) -> None:
    resp, audit = await _post_trigger(app, {"result": "not json from the butler"})

    assert resp.status_code == 502
    assert "did not confirm" in resp.json()["detail"]
    summary, kwargs = _only_audit(audit)
    assert summary["observed_status"] == "unexpected"
    assert kwargs["result"] == "error"


async def test_a_long_failure_detail_is_bounded(app) -> None:
    reply = {"id": str(_SCHEDULE_ID), "status": "error", "error": "x" * 5000}

    resp, _audit = await _post_trigger(app, reply)

    assert resp.status_code == 502
    assert len(resp.json()["detail"]) < 600


async def test_a_triggered_reply_is_200_audited_once_as_success(app) -> None:
    reply = {"id": str(_SCHEDULE_ID), "status": "triggered", "name": "insight-urgent"}

    resp, audit = await _post_trigger(app, reply)

    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "triggered"
    summary, kwargs = _only_audit(audit)
    assert summary["observed_status"] == "triggered"
    assert kwargs["result"] == "success"
