"""Tests for GET /api/switchboard/insights — read-only insight-candidate reader.

Bead: bu-sqjc7.3.

The reader is hosted on the Switchboard role because ``butler_switchboard_rw``
is the only butler role with SELECT on ``public.insight_candidates`` (migration
core_010); the others have INSERT-only access.

Coverage:
- Happy path: rows mapped to the response model with all fields
- Default status=pending applied and bound as the first SQL arg
- ``butler`` filter adds an origin_butler condition
- Invalid status → 422
- limit out of range → 422 (FastAPI validation)
- 503 when the switchboard pool is unavailable
- Graceful degrade: table query fails → 200 with empty list
"""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager

pytestmark = pytest.mark.unit

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"


def _get_db_dep():
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load spec from {_router_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]._get_db_manager


def _make_row(data: dict):
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    row.keys = lambda: data.keys()
    row.__iter__ = lambda self: iter(data)
    return row


def _app_with_mock(
    app,
    *,
    fetch_rows=None,
    fetch_side_effect=None,
    pool_available=True,
):
    mock_pool = AsyncMock()
    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool")

    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return app, mock_pool


def _sample_row(**overrides):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "origin_butler": "finance",
        "priority": 80,
        "category": "bill",
        "dedup_key": "bill:water:2026-06",
        "cooldown_days": 7,
        "expires_at": datetime.datetime(2026, 6, 30, tzinfo=datetime.UTC),
        "message": "Water bill due soon",
        "channel": "telegram",
        "metadata": {"amount": "42.00"},
        "created_at": datetime.datetime(2026, 6, 20, tzinfo=datetime.UTC),
        "status": "pending",
        "delivered_at": None,
        "delivery_attempt_count": 0,
        "prepared_action_id": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


async def test_insights_happy_path_maps_all_fields(app):
    app, _ = _app_with_mock(app, fetch_rows=[_make_row(_sample_row())])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    item = data[0]
    assert item["id"] == "11111111-1111-1111-1111-111111111111"
    assert item["origin_butler"] == "finance"
    assert item["priority"] == 80
    assert item["category"] == "bill"
    assert item["dedup_key"] == "bill:water:2026-06"
    assert item["cooldown_days"] == 7
    assert item["message"] == "Water bill due soon"
    assert item["channel"] == "telegram"
    assert item["metadata"] == {"amount": "42.00"}
    assert item["status"] == "pending"
    assert item["delivered_at"] is None
    assert item["prepared_action_id"] is None
    assert item["delivery_attempt_count"] == 0
    assert item["expires_at"] is not None
    assert item["created_at"] is not None


async def test_insights_surfaces_prepared_action_id(app):
    prepared_id = "22222222-2222-2222-2222-222222222222"
    app, _ = _app_with_mock(
        app, fetch_rows=[_make_row(_sample_row(prepared_action_id=prepared_id))]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["prepared_action_id"] == prepared_id


# ---------------------------------------------------------------------------
# Default status + SQL binding
# ---------------------------------------------------------------------------


async def test_insights_defaults_to_pending(app):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")

    assert resp.status_code == 200
    assert resp.json()["data"] == []
    call = mock_pool.fetch.call_args
    sql = call[0][0]
    assert "public.insight_candidates" in sql
    assert "status = $1" in sql
    # First bound arg is the status filter.
    assert call[0][1] == "pending"


async def test_insights_butler_filter_adds_condition(app):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/switchboard/insights", params={"butler": "health", "status": "delivered"}
        )

    assert resp.status_code == 200
    call = mock_pool.fetch.call_args
    sql = call[0][0]
    assert "origin_butler = $2" in sql
    # Bound args: status, butler, then limit.
    assert call[0][1] == "delivered"
    assert call[0][2] == "health"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


async def test_insights_invalid_status_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights", params={"status": "bogus"})
    assert resp.status_code == 422


async def test_insights_limit_out_of_range_422(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights", params={"limit": 99999})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Degraded modes
# ---------------------------------------------------------------------------


async def test_insights_503_when_pool_unavailable(app):
    _app_with_mock(app, pool_available=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")
    assert resp.status_code == 503


async def test_insights_graceful_degrade_when_table_missing(app):
    _app_with_mock(app, fetch_side_effect=RuntimeError("relation does not exist"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/insights")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


@pytest.mark.parametrize(
    ("action", "verdict", "body"),
    [
        ("useful", "useful", None),
        ("snooze", "not_now", {"snooze_until": "2026-09-20T00:00:00Z"}),
        ("mute", "never", None),
    ],
)
async def test_insight_feedback_rest_verbs_share_server_attributed_behavior(
    app, action, verdict, body
):
    app, mock_pool = _app_with_mock(app, fetch_rows=[])
    result = {
        "status": "recorded",
        "verdict": verdict,
        "insight_id": "11111111-1111-1111-1111-111111111111",
        "category": "Health",
        "dedup_family": "health:signal",
        "snooze_until": body["snooze_until"] if body else None,
    }
    with patch(
        "butlers.tools.switchboard.insight.broker.record_insight_feedback",
        new=AsyncMock(return_value=result),
    ) as feedback:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/switchboard/insights/11111111-1111-1111-1111-111111111111/{action}",
                json=body,
            )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "status": "recorded",
        "verdict": verdict,
        "insight_id": "11111111-1111-1111-1111-111111111111",
        "snooze_until": body["snooze_until"] if body else None,
    }
    assert feedback.await_args.args[0] is mock_pool
    assert feedback.await_args.kwargs["actor"] == "owner"
    assert "actor" not in (body or {})


def _callback_app():
    from fastapi import FastAPI

    from butlers.api.owner_auth.config import OwnerAuthConfig
    from butlers.api.owner_auth.http import OwnerAuthMiddleware
    from tests.api.auth_helpers import _DomainOwnerState

    dep = _get_db_dep()
    app = FastAPI()
    app.state.approval_callback_connector_token = "test-only-connector"
    app.state.owner_auth_service = _DomainOwnerState("test-only-owner-key")
    app.add_middleware(OwnerAuthMiddleware, config=OwnerAuthConfig.from_env("test-only-owner-key"))
    app.include_router(sys.modules[_MODULE_NAME].router)
    pool = AsyncMock()
    db = MagicMock()
    db.pool.return_value = pool
    app.dependency_overrides[dep] = lambda: db
    return app, pool


async def test_telegram_feedback_credential_scope_is_exact_and_actor_is_fixed():
    app, pool = _callback_app()
    insight_id = "11111111-1111-1111-1111-111111111111"
    base = f"/api/switchboard/insights/{insight_id}"
    pool.fetchrow.return_value = {
        "id": insight_id,
        "created_at": datetime.datetime(2026, 10, 16, tzinfo=datetime.UTC),
        "status": "delivered",
        "verdict": None,
        "message": "must never be returned",
    }
    scoped = {"X-Butlers-Approval-Callback-Token": "test-only-connector"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for method, suffix in (("GET", "callback-state"), ("POST", "telegram-feedback")):
            for headers in (
                {},
                {"X-Butlers-Approval-Callback-Token": "wrong"},
                {"X-API-Key": "test-only-owner-key"},
            ):
                response = await client.request(
                    method,
                    f"{base}/{suffix}",
                    headers=headers,
                    json={"verdict": "useful", "token": "ifb1:test", "callback_query_id": "query"},
                )
                assert response.status_code in (401, 403)
        allowed = await client.get(f"{base}/callback-state", headers=scoped)
        assert allowed.status_code == 200
        assert set(allowed.json()["data"]) == {"id", "created_at", "status", "verdict"}
        for path in (f"{base}/useful", f"{base}/callback-state/extra", "/api/switchboard/insights"):
            denied = await client.post(path, headers=scoped, json={})
            assert denied.status_code == 401
        body = {"verdict": "useful", "token": "ifb1:test", "callback_query_id": "query"}
        for actor in (None, "owner@dashboard", "forged"):
            response = await client.post(
                f"{base}/telegram-feedback",
                headers={**scoped, **({"X-Butlers-Decision-Actor": actor} if actor else {})},
                json=body,
            )
            assert response.status_code == 403


async def test_telegram_feedback_derives_snooze_and_rejects_body_authority():
    app, _ = _callback_app()
    path = "/api/switchboard/insights/11111111-1111-1111-1111-111111111111/telegram-feedback"
    headers = {
        "X-Butlers-Approval-Callback-Token": "test-only-connector",
        "X-Butlers-Decision-Actor": "owner@telegram",
    }
    result = {
        "status": "recorded",
        "verdict": "not_now",
        "insight_id": path.split("/")[4],
        "snooze_until": None,
    }
    body = {"verdict": "not_now", "token": "ifb1:test", "callback_query_id": "query-1"}
    with (
        patch(
            "butlers.credential_store.CredentialStore.resolve",
            AsyncMock(return_value="test-only-signing-secret"),
        ),
        patch(
            "butlers.tools.switchboard.insight.broker.record_insight_feedback",
            AsyncMock(return_value=result),
        ) as record,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            before = datetime.datetime.now(datetime.UTC)
            response = await client.post(path, headers=headers, json=body)
            assert response.status_code == 200
            kwargs = record.call_args.kwargs
            assert kwargs["actor"] == "owner@telegram"
            assert kwargs["evidence_ref"] == "telegram:query-1"
            assert (
                before + datetime.timedelta(hours=72)
                <= kwargs["snooze_until"]
                <= datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=72)
            )
            assert kwargs["skip_unchanged"] is True
            for extra in (
                {"actor": "forged"},
                {"snooze_until": "2099-01-01"},
                {"callback_query_id": "\nsecret"},
            ):
                response = await client.post(path, headers=headers, json={**body, **extra})
                assert response.status_code == 422
                assert body["token"] not in response.text
            record.assert_awaited_once()


async def test_telegram_feedback_audit_and_errors_omit_bearer_body(caplog):
    from butlers.api.dashboard_audit_middleware import DashboardAuditMiddleware

    app, _ = _callback_app()
    app.add_middleware(DashboardAuditMiddleware)
    path = "/api/switchboard/insights/11111111-1111-1111-1111-111111111111/telegram-feedback"
    headers = {
        "X-Butlers-Approval-Callback-Token": "test-only-connector",
        "X-Butlers-Decision-Actor": "owner@telegram",
        "Content-Type": "application/json",
    }
    private = "synthetic-private-bearer-sentinel"
    with (
        patch("butlers.api.dashboard_audit_middleware.get_db_manager", MagicMock()),
        patch("butlers.api.dashboard_audit_middleware.emit_dashboard_audit", AsyncMock()) as emit,
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for suffix, body in (
                ("", '{"token":"' + private + '"'),
                ("/", '{"private": "' + private + '"}'),
            ):
                response = await client.post(path + suffix, headers=headers, content=body)
                assert response.status_code in (401, 422)
                assert emit.call_args.kwargs["body"] == {"redacted": "insight_callback"}
                assert private not in repr(emit.call_args) + response.text + caplog.text
