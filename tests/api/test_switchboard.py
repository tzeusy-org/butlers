"""Tests for switchboard API endpoints.

Condensed: 43 → ~20 tests [bu-gg4y1].
Keeps: CRUD contracts, error fallbacks, eligibility history transitions,
validation boundary, backfill lifecycle (create/pause/conflict), connectors.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
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


def _get_router_module():
    _get_db_dep()
    return sys.modules[_MODULE_NAME]


def _make_row(data: dict):
    row = MagicMock()
    row.__getitem__ = lambda self, k: data[k]
    row.get = lambda k, default=None: data.get(k, default)
    row.keys = lambda: data.keys()
    row.__iter__ = lambda self: iter(data)
    return row


def _app_with_mock(
    app, *, fetch_rows=None, fetchrow_result=None, fetchval_result=0,
    execute_return="UPDATE 1", pool_available=True, fetchrow_side_effects=None,
):
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_return)
    if fetchrow_side_effects is not None:
        mock_pool.fetchrow = AsyncMock(side_effect=fetchrow_side_effects)
    else:
        mock_pool.fetchrow = AsyncMock(return_value=fetchrow_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool")

    app.dependency_overrides[_get_db_dep()] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# Routing log / Registry views
# ---------------------------------------------------------------------------


async def test_routing_log_returns_paginated_structure(app):
    _app_with_mock(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/routing-log")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body and "meta" in body


async def test_routing_log_503_when_pool_unavailable(app):
    _app_with_mock(app, pool_available=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/routing-log")
    assert resp.status_code == 503


async def test_register_missing_butler_uses_mcp_url(tmp_path):
    module = _get_router_module()
    config_dir = tmp_path / "demo"
    config_dir.mkdir()
    (config_dir / "butler.toml").write_text(
        '[butler]\nname = "demo"\nport = 41234\ndescription = "Demo butler"\n'
    )
    registry_module = MagicMock()
    registry_module.register_butler = AsyncMock()
    with patch.dict(sys.modules, {module._REGISTRY_MODULE_NAME: registry_module}):
        old = module._ROSTER_DIR
        module._ROSTER_DIR = tmp_path
        try:
            ok = await module._register_missing_butler_from_roster(AsyncMock(), "demo")
        finally:
            module._ROSTER_DIR = old
    assert ok is True
    args = registry_module.register_butler.await_args.args
    assert args[2] == "http://localhost:41234/mcp"


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_active_returns_200(app):
    _app_with_mock(app, fetchrow_result={"eligibility_state": "active", "last_seen_at": None})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})
    assert resp.status_code == 200
    assert resp.json()["eligibility_state"] == "active"


async def test_heartbeat_stale_transitions_to_active_and_logs(app):
    app, mock_pool = _app_with_mock(
        app, fetchrow_result={"eligibility_state": "stale", "last_seen_at": None}, execute_return="UPDATE 1"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})
    assert resp.status_code == 200
    assert resp.json()["eligibility_state"] == "active"
    sql_calls = [c[0][0] for c in mock_pool.execute.call_args_list]
    assert any("butler_registry_eligibility_log" in s for s in sql_calls)


async def test_heartbeat_unknown_butler_404(app):
    _app_with_mock(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "nonexistent"})
    assert resp.status_code == 404


async def test_heartbeat_missing_name_422(app):
    _app_with_mock(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/switchboard/heartbeat", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Eligibility history
# ---------------------------------------------------------------------------


async def test_eligibility_no_transitions_single_segment(app):
    _app_with_mock(app, fetchrow_result={"eligibility_state": "active"})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/registry/health/eligibility-history")
    data = resp.json()["data"]
    assert data["butler_name"] == "health"
    assert len(data["segments"]) == 1
    assert data["segments"][0]["state"] == "active"


async def test_eligibility_transitions_correct_segments(app):
    now = datetime.datetime.now(datetime.UTC)
    app, mock_pool = _app_with_mock(app, fetchrow_result={"eligibility_state": "active"})
    mock_pool.fetch = AsyncMock(return_value=[
        {"previous_state": "active", "new_state": "stale", "observed_at": now - datetime.timedelta(hours=12)},
        {"previous_state": "stale", "new_state": "active", "observed_at": now - datetime.timedelta(hours=6)},
    ])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/registry/health/eligibility-history")
    states = [s["state"] for s in resp.json()["data"]["segments"]]
    assert states == ["active", "stale", "active"]


async def test_eligibility_unknown_butler_404(app):
    _app_with_mock(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/registry/unknown/eligibility-history")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Ingestion rules
# ---------------------------------------------------------------------------

_GLOBAL_RULE = {
    "id": "11111111-1111-1111-1111-111111111111", "scope": "global",
    "rule_type": "sender_domain", "condition": {"domain": "chase.com", "match": "exact"},
    "action": "route_to:finance", "priority": 10, "enabled": True, "name": "Chase routing",
    "description": None, "created_by": "dashboard",
    "created_at": "2026-03-08T00:00:00+00:00", "updated_at": "2026-03-08T00:00:00+00:00",
    "deleted_at": None,
}


async def test_ingestion_rules_list_paginated(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/ingestion-rules")
    assert resp.status_code == 200
    assert "data" in resp.json() and "meta" in resp.json()


async def test_ingestion_rules_condition_jsonb_decoded(app):
    row = dict(_GLOBAL_RULE)
    row["condition"] = json.dumps({"domain": "chase.com", "match": "exact"})
    _app_with_mock(app, fetch_rows=[row])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/ingestion-rules")
    assert isinstance(resp.json()["data"][0]["condition"], dict)


async def test_ingestion_rules_create_global_201(app):
    app, mock_pool = _app_with_mock(app)
    mock_pool.fetchrow = AsyncMock(side_effect=[_make_row({"name": "finance"}), _make_row(_GLOBAL_RULE)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/switchboard/ingestion-rules",
            json={"scope": "global", "rule_type": "sender_domain",
                  "condition": {"domain": "chase.com", "match": "exact"},
                  "action": "route_to:finance", "priority": 10},
        )
    assert resp.status_code == 201


@pytest.mark.parametrize("bad_payload,exp_status", [
    ({"scope": "connector:gmail:gmail:user:dev", "rule_type": "sender_domain",
      "condition": {"domain": "x.com", "match": "exact"}, "action": "skip", "priority": 10}, 422),
    ({"scope": "invalid_scope", "rule_type": "sender_domain",
      "condition": {}, "action": "skip", "priority": 10}, 422),
    ({"scope": "global", "rule_type": "sender_domain",
      "condition": {}, "action": "skip", "priority": -1}, 422),
])
async def test_ingestion_rules_validation_errors(app, bad_payload, exp_status):
    _app_with_mock(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/switchboard/ingestion-rules", json=bad_payload)
    assert resp.status_code == exp_status


async def test_ingestion_rules_delete_nonexistent_404(app):
    _app_with_mock(app, execute_return="UPDATE 0")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete("/api/switchboard/ingestion-rules/11111111-1111-1111-1111-111111111112")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Backfill jobs
# ---------------------------------------------------------------------------

_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SAMPLE_JOB = {
    "id": _JOB_ID, "connector_type": "gmail", "endpoint_identity": "user@example.com",
    "target_categories": ["finance"], "date_from": "2020-01-01", "date_to": "2026-01-01",
    "rate_limit_per_hour": 100, "daily_cost_cap_cents": 500, "status": "pending",
    "cursor": None, "rows_processed": 0, "rows_skipped": 0, "cost_spent_cents": 0,
    "error": None, "created_at": "2026-02-23T10:00:00+00:00", "started_at": None,
    "completed_at": None, "updated_at": "2026-02-23T10:00:00+00:00",
}


async def test_backfill_create_201(app):
    app, mock_pool = _app_with_mock(app)
    mock_pool.fetchrow = AsyncMock(return_value=_make_row(_SAMPLE_JOB))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/switchboard/backfill",
            json={"connector_type": "gmail", "endpoint_identity": "user@example.com",
                  "date_from": "2020-01-01", "date_to": "2026-01-01"},
        )
    assert resp.status_code == 201
    assert "id" in resp.json()["data"]


async def test_backfill_get_nonexistent_404(app):
    _app_with_mock(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}")
    assert resp.status_code == 404


async def test_backfill_pause_completed_409(app):
    completed_job = {**_SAMPLE_JOB, "status": "completed"}
    _app_with_mock(app, fetchrow_result=_make_row(completed_job))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

_SAMPLE_CONNECTOR = {
    "connector_type": "telegram_bot", "endpoint_identity": "bot-123", "instance_id": None,
    "version": "1.0.0", "state": "healthy", "error_message": None, "uptime_s": 3600,
    "last_heartbeat_at": "2026-02-23T10:00:00+00:00", "first_seen_at": "2026-02-01T00:00:00+00:00",
    "registered_via": "self", "counter_messages_ingested": 42, "counter_messages_failed": 1,
    "counter_source_api_calls": 150, "counter_checkpoint_saves": 10, "counter_dedupe_accepted": 0,
    "today_messages_ingested": 7, "today_messages_failed": 0,
    "checkpoint_cursor": "update-12345", "checkpoint_updated_at": "2026-02-23T09:55:00+00:00",
}


async def test_connectors_list_200(app):
    _app_with_mock(app, fetch_rows=[])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/connectors")
    assert resp.status_code == 200


async def test_connector_detail_200(app):
    _app_with_mock(app, fetchrow_result=_make_row(_SAMPLE_CONNECTOR))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/connectors/telegram_bot/bot-123")
    assert resp.status_code == 200


async def test_connector_detail_404(app):
    _app_with_mock(app, fetchrow_result=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/switchboard/connectors/telegram_bot/nonexistent")
    assert resp.status_code == 404


async def test_update_connector_cursor_validates_empty_string(app):
    _app_with_mock(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.patch(
            "/api/switchboard/connectors/telegram_bot/bot-123/cursor", json={"cursor": ""}
        )
    assert resp.status_code == 422
