"""Tests for switchboard API endpoints.

Condensed from 6 files (177 tests total):
  test_switchboard_views.py (10)
  test_switchboard_heartbeat.py (16)
  test_switchboard_eligibility_history.py (5)
  test_switchboard_ingestion_rules.py (63)
  test_switchboard_backfill.py (40)
  test_switchboard_connectors.py (43)
→ ~30 tests (bu-egmz6).

Keeps: status codes per operation, scope-aware validation, behavioral transitions,
CRUD contract, graceful fallbacks.
Removes: trivial filter-accepted tests, duplicate 503/503 tests per endpoint.
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

# ---------------------------------------------------------------------------
# Module loading — shared across all switchboard test helpers
# ---------------------------------------------------------------------------

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"


def _get_db_dep():
    """Return _get_db_manager from the live module (lazy-load if needed)."""
    if _MODULE_NAME not in sys.modules:
        spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Could not load spec from {_router_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[_MODULE_NAME] = module
        spec.loader.exec_module(module)
    return sys.modules[_MODULE_NAME]._get_db_manager


def _get_router_module():
    """Return the live switchboard API router module."""
    _get_db_dep()
    return sys.modules[_MODULE_NAME]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    fetch_rows: list | None = None,
    fetchrow_result=None,
    fetchval_result=0,
    execute_return: str | None = "UPDATE 1",
    pool_available: bool = True,
    fetchrow_side_effects: list | None = None,
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


class TestSwitchboardViews:
    async def test_routing_log_returns_paginated_structure(self, app):
        _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/routing-log")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body
        assert "total" in body["meta"]

    async def test_routing_log_empty_state(self, app):
        _app_with_mock(app, fetchval_result=0)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/routing-log")
        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_routing_log_503_when_pool_unavailable(self, app):
        _app_with_mock(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/routing-log")
        assert resp.status_code == 503

    async def test_registry_returns_paginated_structure(self, app):
        _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body

    async def test_registry_503_when_pool_unavailable(self, app):
        _app_with_mock(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry")
        assert resp.status_code == 503

    async def test_register_missing_butler_from_roster_uses_mcp_url(self, tmp_path):
        module = _get_router_module()

        config_dir = tmp_path / "demo"
        config_dir.mkdir()
        (config_dir / "butler.toml").write_text(
            '[butler]\nname = "demo"\nport = 41234\ndescription = "Demo butler"\n'
        )

        registry_module = MagicMock()
        registry_module.register_butler = AsyncMock()

        with patch.dict(sys.modules, {module._REGISTRY_MODULE_NAME: registry_module}):
            old_roster_dir = module._ROSTER_DIR
            module._ROSTER_DIR = tmp_path
            try:
                ok = await module._register_missing_butler_from_roster(AsyncMock(), "demo")
            finally:
                module._ROSTER_DIR = old_roster_dir

        assert ok is True
        registry_module.register_butler.assert_awaited_once()
        args = registry_module.register_butler.await_args.args
        assert args[2] == "http://localhost:41234/mcp"


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


class TestHeartbeat:
    async def test_active_butler_returns_200(self, app):
        _app_with_mock(app, fetchrow_result={"eligibility_state": "active", "last_seen_at": None})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["eligibility_state"] == "active"

    async def test_stale_butler_transitions_to_active(self, app):
        _app_with_mock(
            app,
            fetchrow_result={"eligibility_state": "stale", "last_seen_at": None},
            execute_return="UPDATE 1",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})
        assert resp.status_code == 200
        assert resp.json()["eligibility_state"] == "active"

    async def test_stale_transition_logs_eligibility_change(self, app):
        _, mock_pool = _app_with_mock(
            app,
            fetchrow_result={"eligibility_state": "stale", "last_seen_at": None},
            execute_return="UPDATE 1",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})
        # Should have 2 execute calls: UPDATE + INSERT into eligibility_log
        assert mock_pool.execute.call_count == 2
        sql_calls = [c[0][0] for c in mock_pool.execute.call_args_list]
        assert any("butler_registry_eligibility_log" in s for s in sql_calls)

    async def test_unknown_butler_returns_404(self, app):
        _app_with_mock(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/heartbeat", json={"butler_name": "nonexistent"}
            )
        assert resp.status_code == 404

    async def test_missing_butler_name_returns_422(self, app):
        _app_with_mock(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={})
        assert resp.status_code == 422

    async def test_pool_unavailable_returns_503(self, app):
        _app_with_mock(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/heartbeat", json={"butler_name": "health"})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Eligibility history
# ---------------------------------------------------------------------------


class TestEligibilityHistory:
    async def test_no_transitions_returns_single_segment(self, app):
        _app_with_mock(app, fetchrow_result={"eligibility_state": "active"})
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry/health/eligibility-history")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["butler_name"] == "health"
        assert len(data["segments"]) == 1
        assert data["segments"][0]["state"] == "active"

    async def test_transitions_produce_correct_segments(self, app):
        now = datetime.datetime.now(datetime.UTC)
        t1 = now - datetime.timedelta(hours=12)
        t2 = now - datetime.timedelta(hours=6)
        app, mock_pool = _app_with_mock(app, fetchrow_result={"eligibility_state": "active"})
        mock_pool.fetch = AsyncMock(
            return_value=[
                {"previous_state": "active", "new_state": "stale", "observed_at": t1},
                {"previous_state": "stale", "new_state": "active", "observed_at": t2},
            ]
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/registry/health/eligibility-history")
        segments = resp.json()["data"]["segments"]
        assert len(segments) == 3
        states = [s["state"] for s in segments]
        assert states == ["active", "stale", "active"]

    async def test_unknown_butler_returns_404(self, app):
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
    "id": "11111111-1111-1111-1111-111111111111",
    "scope": "global",
    "rule_type": "sender_domain",
    "condition": {"domain": "chase.com", "match": "exact"},
    "action": "route_to:finance",
    "priority": 10,
    "enabled": True,
    "name": "Chase routing",
    "description": "Route Chase emails to finance",
    "created_by": "dashboard",
    "created_at": "2026-03-08T00:00:00+00:00",
    "updated_at": "2026-03-08T00:00:00+00:00",
    "deleted_at": None,
}

_CONNECTOR_RULE = {
    **_GLOBAL_RULE,
    "id": "22222222-2222-2222-2222-222222222222",
    "scope": "connector:gmail:gmail:user:dev",
    "action": "block",
    "name": "Block spam",
    "description": None,
}


class TestIngestionRules:
    async def test_list_returns_paginated_structure(self, app):
        _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body

    async def test_list_returns_rule_fields(self, app):
        _app_with_mock(app, fetch_rows=[_GLOBAL_RULE])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")
        rule = resp.json()["data"][0]
        assert rule["scope"] == "global"
        assert rule["rule_type"] == "sender_domain"
        assert rule["action"] == "route_to:finance"

    async def test_condition_jsonb_decoded(self, app):
        """condition field returned as dict, not raw JSON string."""
        row = dict(_GLOBAL_RULE)
        row["condition"] = json.dumps({"domain": "chase.com", "match": "exact"})
        _app_with_mock(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")
        assert isinstance(resp.json()["data"][0]["condition"], dict)

    async def test_list_503_when_pool_unavailable(self, app):
        _app_with_mock(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")
        assert resp.status_code == 503

    async def test_create_global_rule_returns_201(self, app):
        app, mock_pool = _app_with_mock(app)
        registry_row = _make_row({"name": "finance"})
        created_row = _make_row(_GLOBAL_RULE)
        mock_pool.fetchrow = AsyncMock(side_effect=[registry_row, created_row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "global",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "chase.com", "match": "exact"},
                    "action": "route_to:finance",
                    "priority": 10,
                },
            )
        assert resp.status_code == 201
        assert resp.json()["data"]["scope"] == "global"

    @pytest.mark.parametrize(
        "bad_payload,expected_status",
        [
            # connector scope with non-block action
            (
                {
                    "scope": "connector:gmail:gmail:user:dev",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "x.com", "match": "exact"},
                    "action": "skip",
                    "priority": 10,
                },
                422,
            ),
            # invalid scope format
            (
                {
                    "scope": "invalid_scope",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "x.com", "match": "exact"},
                    "action": "skip",
                    "priority": 10,
                },
                422,
            ),
            # invalid rule_type
            (
                {
                    "scope": "global",
                    "rule_type": "invalid_type",
                    "condition": {"domain": "x.com"},
                    "action": "skip",
                    "priority": 10,
                },
                422,
            ),
            # negative priority
            (
                {
                    "scope": "global",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "x.com", "match": "exact"},
                    "action": "skip",
                    "priority": -1,
                },
                422,
            ),
            # chat_id rule for gmail (wrong connector type)
            (
                {
                    "scope": "connector:gmail:gmail:user:dev",
                    "rule_type": "chat_id",
                    "condition": {"chat_id": "123"},
                    "action": "block",
                    "priority": 10,
                },
                422,
            ),
        ],
    )
    async def test_create_validation_errors(self, app, bad_payload, expected_status):
        _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/switchboard/ingestion-rules", json=bad_payload)
        assert resp.status_code == expected_status

    async def test_get_existing_rule(self, app):
        app, mock_pool = _app_with_mock(app, fetchrow_result=_make_row(_GLOBAL_RULE))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE['id']}")
        assert resp.status_code == 200

    async def test_get_nonexistent_rule_returns_404(self, app):
        _app_with_mock(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/ingestion-rules/11111111-1111-1111-1111-111111111112"
            )
        assert resp.status_code == 404

    async def test_update_priority(self, app):
        updated = _make_row({**_GLOBAL_RULE, "priority": 20})
        _app_with_mock(app, fetchrow_result=updated)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE['id']}",
                json={"priority": 20},
            )
        assert resp.status_code == 200

    async def test_delete_returns_204(self, app):
        _app_with_mock(app, fetchrow_result=_make_row(_GLOBAL_RULE))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE['id']}")
        assert resp.status_code == 204

    async def test_delete_nonexistent_returns_404(self, app):
        _app_with_mock(app, execute_return="UPDATE 0")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                "/api/switchboard/ingestion-rules/11111111-1111-1111-1111-111111111112"
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Backfill jobs
# ---------------------------------------------------------------------------

_JOB_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
_SAMPLE_JOB = {
    "id": _JOB_ID,
    "connector_type": "gmail",
    "endpoint_identity": "user@example.com",
    "target_categories": ["finance"],
    "date_from": "2020-01-01",
    "date_to": "2026-01-01",
    "rate_limit_per_hour": 100,
    "daily_cost_cap_cents": 500,
    "status": "pending",
    "cursor": None,
    "rows_processed": 0,
    "rows_skipped": 0,
    "cost_spent_cents": 0,
    "error": None,
    "created_at": "2026-02-23T10:00:00+00:00",
    "started_at": None,
    "completed_at": None,
    "updated_at": "2026-02-23T10:00:00+00:00",
}


class TestBackfillJobs:
    async def test_list_returns_paginated_structure(self, app):
        _app_with_mock(app, fetchval_result=0, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill")
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body and "meta" in body

    async def test_create_returns_201(self, app):
        app, mock_pool = _app_with_mock(app)
        mock_pool.fetchrow = AsyncMock(return_value=_make_row(_SAMPLE_JOB))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/backfill",
                json={
                    "connector_type": "gmail",
                    "endpoint_identity": "user@example.com",
                    "date_from": "2020-01-01",
                    "date_to": "2026-01-01",
                },
            )
        assert resp.status_code == 201
        assert "id" in resp.json()["data"]

    async def test_get_returns_full_detail(self, app):
        _app_with_mock(app, fetchrow_result=_make_row(_SAMPLE_JOB))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}")
        assert resp.status_code == 200

    async def test_get_nonexistent_returns_404(self, app):
        _app_with_mock(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/backfill/{_JOB_ID}")
        assert resp.status_code == 404

    async def test_pause_active_job(self, app):
        active_job = {**_SAMPLE_JOB, "status": "active"}
        app, mock_pool = _app_with_mock(app)
        mock_pool.fetchrow = AsyncMock(
            side_effect=[_make_row(active_job), _make_row({**active_job, "status": "paused"})]
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")
        assert resp.status_code == 200

    async def test_pause_completed_job_returns_409(self, app):
        completed_job = {**_SAMPLE_JOB, "status": "completed"}
        _app_with_mock(app, fetchrow_result=_make_row(completed_job))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(f"/api/switchboard/backfill/{_JOB_ID}/pause")
        assert resp.status_code == 409

    async def test_pool_unavailable_returns_503(self, app):
        _app_with_mock(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/backfill")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Connectors
# ---------------------------------------------------------------------------

_SAMPLE_CONNECTOR = {
    "connector_type": "telegram_bot",
    "endpoint_identity": "bot-123",
    "instance_id": None,
    "version": "1.0.0",
    "state": "healthy",
    "error_message": None,
    "uptime_s": 3600,
    "last_heartbeat_at": "2026-02-23T10:00:00+00:00",
    "first_seen_at": "2026-02-01T00:00:00+00:00",
    "registered_via": "self",
    "counter_messages_ingested": 42,
    "counter_messages_failed": 1,
    "counter_source_api_calls": 150,
    "counter_checkpoint_saves": 10,
    "counter_dedupe_accepted": 0,
    "today_messages_ingested": 7,
    "today_messages_failed": 0,
    "checkpoint_cursor": "update-12345",
    "checkpoint_updated_at": "2026-02-23T09:55:00+00:00",
}


class TestConnectors:
    async def test_list_returns_empty(self, app):
        _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")
        assert resp.status_code == 200

    async def test_list_db_unavailable_returns_503(self, app):
        _app_with_mock(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors")
        assert resp.status_code == 503

    async def test_connector_detail_returns_200(self, app):
        _app_with_mock(app, fetchrow_result=_make_row(_SAMPLE_CONNECTOR))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram_bot/bot-123")
        assert resp.status_code == 200

    async def test_connector_detail_404_when_not_found(self, app):
        _app_with_mock(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram_bot/nonexistent")
        assert resp.status_code == 404

    async def test_ingestion_overview_returns_overview_structure(self, app):
        app, mock_pool = _app_with_mock(app, fetchval_result=5, fetch_rows=[])
        overview_row = _make_row(
            {
                "tier1_count": 100,
                "tier2_count": 50,
                "tier3_count": 25,
                "connector_count": 5,
                "llm_calls_saved": 75,
            }
        )
        mock_pool.fetchrow = AsyncMock(return_value=overview_row)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion/overview")
        assert resp.status_code == 200

    async def test_update_connector_cursor_validates_empty_string(self, app):
        _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/switchboard/connectors/telegram_bot/bot-123/cursor",
                json={"cursor": ""},
            )
        assert resp.status_code == 422
