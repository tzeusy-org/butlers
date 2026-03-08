"""Tests for switchboard ingestion rule API endpoints.

Covers:
- GET /api/switchboard/ingestion-rules (list with optional filters)
- POST /api/switchboard/ingestion-rules (create with scope-aware validation)
- GET /api/switchboard/ingestion-rules/{id} (get single rule)
- PATCH /api/switchboard/ingestion-rules/{id} (partial update with scope validation)
- DELETE /api/switchboard/ingestion-rules/{id} (soft-delete)
- POST /api/switchboard/ingestion-rules/test (dry-run evaluation)
- Scope-aware validation: connector scope -> action must be block
- Rule type compatibility with connector type

Uses mocked DatabaseManager -- no real database required.

Issue: bu-r55.5
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

_GLOBAL_RULE_ROW = {
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

_CONNECTOR_RULE_ROW = {
    "id": "22222222-2222-2222-2222-222222222222",
    "scope": "connector:gmail:gmail:user:dev",
    "rule_type": "sender_domain",
    "condition": {"domain": "spam.com", "match": "suffix"},
    "action": "block",
    "priority": 5,
    "enabled": True,
    "name": "Block spam.com",
    "description": None,
    "created_by": "dashboard",
    "created_at": "2026-03-08T00:00:00+00:00",
    "updated_at": "2026-03-08T00:00:00+00:00",
    "deleted_at": None,
}

_CHAT_ID_RULE_ROW = {
    "id": "33333333-3333-3333-3333-333333333333",
    "scope": "connector:telegram-bot:my-bot",
    "rule_type": "chat_id",
    "condition": {"chat_id": "987654321"},
    "action": "block",
    "priority": 10,
    "enabled": True,
    "name": None,
    "description": None,
    "created_by": "dashboard",
    "created_at": "2026-03-08T00:00:00+00:00",
    "updated_at": "2026-03-08T00:00:00+00:00",
    "deleted_at": None,
}


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _get_current_db_dep():
    """Return _get_db_manager from the currently loaded switchboard module."""
    if _MODULE_NAME in sys.modules:
        return sys.modules[_MODULE_NAME]._get_db_manager
    import importlib.util

    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {_router_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module._get_db_manager


def _make_row(data: dict):
    """Create a dict-like mock row from a dict."""
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
    fetchrow_result: dict | None = None,
    fetchval_result: int = 0,
    execute_result: str = "UPDATE 1",
    pool_available: bool = True,
):
    """Build a FastAPI test app with a mocked DatabaseManager."""
    mock_pool = AsyncMock()
    mock_pool.fetch = AsyncMock(return_value=[_make_row(r) for r in (fetch_rows or [])])
    if fetchrow_result is not None:
        mock_pool.fetchrow = AsyncMock(return_value=_make_row(fetchrow_result))
    else:
        mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.fetchval = AsyncMock(return_value=fetchval_result)
    mock_pool.execute = AsyncMock(return_value=execute_result)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool")

    get_dep = _get_current_db_dep()
    app.dependency_overrides[get_dep] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# GET /api/switchboard/ingestion-rules
# ---------------------------------------------------------------------------


class TestListIngestionRules:
    async def test_returns_api_response_structure(self, app):
        """Response must have 'data' list and 'meta' with total."""
        app, _ = _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]

    async def test_empty_results(self, app):
        """When no rules exist, data is empty list with total=0."""
        app, _ = _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_returns_rule_fields(self, app):
        """Each rule must have all required fields."""
        app, _ = _app_with_mock(app, fetch_rows=[_GLOBAL_RULE_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")

        body = resp.json()
        assert len(body["data"]) == 1
        rule = body["data"][0]
        assert rule["id"] == _GLOBAL_RULE_ROW["id"]
        assert rule["scope"] == "global"
        assert rule["rule_type"] == "sender_domain"
        assert rule["action"] == "route_to:finance"
        assert rule["priority"] == 10
        assert rule["enabled"] is True
        assert rule["name"] == "Chase routing"
        assert rule["description"] == "Route Chase emails to finance"
        assert rule["created_by"] == "dashboard"
        assert "condition" in rule
        assert "created_at" in rule
        assert "updated_at" in rule

    async def test_scope_filter_accepted(self, app):
        """?scope= query parameter must be accepted."""
        app, mock_pool = _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules", params={"scope": "global"})

        assert resp.status_code == 200
        call_args = mock_pool.fetch.call_args
        assert call_args is not None
        query = call_args[0][0]
        assert "scope" in query

    async def test_rule_type_filter_accepted(self, app):
        """?rule_type= query parameter must be accepted."""
        app, mock_pool = _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/ingestion-rules", params={"rule_type": "sender_domain"}
            )

        assert resp.status_code == 200
        call_args = mock_pool.fetch.call_args
        assert call_args is not None
        query = call_args[0][0]
        assert "rule_type" in query

    async def test_action_filter_accepted(self, app):
        """?action= query parameter must be accepted."""
        app, mock_pool = _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules", params={"action": "block"})

        assert resp.status_code == 200
        call_args = mock_pool.fetch.call_args
        assert call_args is not None
        query = call_args[0][0]
        assert "action" in query

    async def test_enabled_filter_accepted(self, app):
        """?enabled= query parameter must be accepted."""
        app, _ = _app_with_mock(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules", params={"enabled": "true"})

        assert resp.status_code == 200

    async def test_meta_total_matches_row_count(self, app):
        """meta.total must equal the number of returned rows."""
        app, _ = _app_with_mock(app, fetch_rows=[_GLOBAL_RULE_ROW, _CONNECTOR_RULE_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")

        body = resp.json()
        assert body["meta"]["total"] == 2
        assert len(body["data"]) == 2

    async def test_pool_unavailable_returns_503(self, app):
        """When DB pool is unavailable, must return 503."""
        app, _ = _app_with_mock(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")

        assert resp.status_code == 503

    async def test_condition_jsonb_decoded(self, app):
        """condition field must be a dict (not raw JSON string)."""
        row_with_str_condition = dict(_GLOBAL_RULE_ROW)
        row_with_str_condition["condition"] = json.dumps({"domain": "chase.com", "match": "exact"})
        app, _ = _app_with_mock(app, fetch_rows=[row_with_str_condition])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules")

        body = resp.json()
        assert isinstance(body["data"][0]["condition"], dict)


# ---------------------------------------------------------------------------
# POST /api/switchboard/ingestion-rules
# ---------------------------------------------------------------------------


class TestCreateIngestionRule:
    async def test_create_global_rule_returns_201(self, app):
        """Successful create of a global rule returns 201 with the rule."""
        app, mock_pool = _app_with_mock(app)
        registry_row = _make_row({"name": "finance"})
        created_row = _make_row(_GLOBAL_RULE_ROW)
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
                    "name": "Chase routing",
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert "data" in body
        assert body["data"]["scope"] == "global"
        assert body["data"]["rule_type"] == "sender_domain"

    async def test_create_connector_block_rule_returns_201(self, app):
        """Connector-scoped rule with action=block is accepted."""
        app, mock_pool = _app_with_mock(app)
        created_row = _make_row(_CONNECTOR_RULE_ROW)
        mock_pool.fetchrow = AsyncMock(return_value=created_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:gmail:gmail:user:dev",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "spam.com", "match": "suffix"},
                    "action": "block",
                    "priority": 5,
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["data"]["scope"] == "connector:gmail:gmail:user:dev"
        assert body["data"]["action"] == "block"

    async def test_connector_scope_rejects_non_block_action(self, app):
        """Connector scope with non-block action must be rejected with 422."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:gmail:gmail:user:dev",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_connector_scope_rejects_route_to_action(self, app):
        """Connector scope with route_to action must be rejected."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:gmail:gmail:user:dev",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "route_to:finance",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_invalid_scope_format_returns_422(self, app):
        """Invalid scope format must be rejected."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "invalid_scope",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_invalid_rule_type_returns_422(self, app):
        """Invalid rule_type must be rejected."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "global",
                    "rule_type": "invalid_type",
                    "condition": {"domain": "example.com"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_negative_priority_returns_422(self, app):
        """Negative priority must be rejected."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "global",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "skip",
                    "priority": -1,
                },
            )

        assert resp.status_code == 422

    async def test_condition_schema_mismatch_returns_422(self, app):
        """Condition schema mismatch for rule_type must be rejected."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "global",
                    "rule_type": "sender_domain",
                    "condition": {"address": "bad@example.com"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_create_chat_id_rule_for_telegram(self, app):
        """chat_id rule type is valid for telegram-bot connector scope."""
        app, mock_pool = _app_with_mock(app)
        created_row = _make_row(_CHAT_ID_RULE_ROW)
        mock_pool.fetchrow = AsyncMock(return_value=created_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:telegram-bot:my-bot",
                    "rule_type": "chat_id",
                    "condition": {"chat_id": "987654321"},
                    "action": "block",
                    "priority": 10,
                },
            )

        assert resp.status_code == 201

    async def test_chat_id_rule_type_rejected_for_gmail_scope(self, app):
        """chat_id rule type must be rejected for gmail connector scope."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:gmail:gmail:user:dev",
                    "rule_type": "chat_id",
                    "condition": {"chat_id": "123456"},
                    "action": "block",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_channel_id_rule_type_rejected_for_telegram_scope(self, app):
        """channel_id rule type must be rejected for telegram-bot connector scope."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:telegram-bot:my-bot",
                    "rule_type": "channel_id",
                    "condition": {"channel_id": "123456789"},
                    "action": "block",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_create_substring_rule_for_gmail(self, app):
        """substring rule type is valid for gmail connector scope."""
        app, mock_pool = _app_with_mock(app)
        substring_row = {
            **_CONNECTOR_RULE_ROW,
            "id": "44444444-4444-4444-4444-444444444444",
            "rule_type": "substring",
            "condition": {"pattern": "newsletter"},
        }
        mock_pool.fetchrow = AsyncMock(return_value=_make_row(substring_row))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:gmail:gmail:user:dev",
                    "rule_type": "substring",
                    "condition": {"pattern": "newsletter"},
                    "action": "block",
                    "priority": 20,
                },
            )

        assert resp.status_code == 201

    async def test_create_global_simple_action_no_registry_check(self, app):
        """For non-route_to global actions, no registry check is performed."""
        app, mock_pool = _app_with_mock(app)
        created_row = _make_row(
            {
                **_GLOBAL_RULE_ROW,
                "action": "skip",
            }
        )
        mock_pool.fetchrow = AsyncMock(return_value=created_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "global",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 201
        assert mock_pool.fetchrow.call_count == 1
        insert_call = mock_pool.fetchrow.call_args_list[0]
        assert "INSERT" in insert_call[0][0]

    async def test_route_to_unregistered_butler_returns_422(self, app):
        """route_to action with unregistered target must be rejected."""
        app, mock_pool = _app_with_mock(app)
        mock_pool.fetchrow = AsyncMock(return_value=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "global",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "route_to:unknown_butler",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422
        assert "unknown_butler" in resp.json()["detail"]

    async def test_connector_scope_missing_identity_returns_422(self, app):
        """Connector scope with missing identity part is rejected."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules",
                json={
                    "scope": "connector:gmail",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "block",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/switchboard/ingestion-rules/{id}
# ---------------------------------------------------------------------------


class TestGetIngestionRule:
    async def test_get_existing_rule(self, app):
        """Getting an existing rule returns 200 with the rule data."""
        app, mock_pool = _app_with_mock(app, fetchrow_result=_GLOBAL_RULE_ROW)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["id"] == _GLOBAL_RULE_ROW["id"]
        assert body["data"]["scope"] == "global"

    async def test_get_nonexistent_rule_returns_404(self, app):
        """Getting a non-existent rule returns 404."""
        app, _ = _app_with_mock(app, fetchrow_result=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/ingestion-rules/11111111-1111-1111-1111-111111111111"
            )

        assert resp.status_code == 404

    async def test_get_invalid_uuid_returns_422(self, app):
        """Getting a rule with invalid UUID format returns 422."""
        app, _ = _app_with_mock(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/ingestion-rules/not-a-uuid")

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# PATCH /api/switchboard/ingestion-rules/{id}
# ---------------------------------------------------------------------------


class TestUpdateIngestionRule:
    async def test_update_priority(self, app):
        """Updating priority returns the updated rule."""
        app, mock_pool = _app_with_mock(app)
        existing_row = _make_row(_GLOBAL_RULE_ROW)
        # fetchrow calls: 1) existing rule, 2) registry check for route_to, 3) UPDATE RETURNING
        registry_row = _make_row({"name": "finance"})
        updated_row = _make_row({**_GLOBAL_RULE_ROW, "priority": 20})
        mock_pool.fetchrow = AsyncMock(side_effect=[existing_row, registry_row, updated_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}",
                json={"priority": 20},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["priority"] == 20

    async def test_update_nonexistent_returns_404(self, app):
        """Updating a non-existent rule returns 404."""
        app, mock_pool = _app_with_mock(app)
        mock_pool.fetchrow = AsyncMock(return_value=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/switchboard/ingestion-rules/11111111-1111-1111-1111-111111111111",
                json={"priority": 20},
            )

        assert resp.status_code == 404

    async def test_update_invalid_uuid_returns_422(self, app):
        """Updating with invalid UUID format returns 422."""
        app, _ = _app_with_mock(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/switchboard/ingestion-rules/not-a-uuid",
                json={"priority": 20},
            )

        assert resp.status_code == 422

    async def test_update_empty_body_returns_existing(self, app):
        """Updating with empty body returns the existing rule unchanged."""
        app, mock_pool = _app_with_mock(app, fetchrow_result=_GLOBAL_RULE_ROW)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}",
                json={},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["id"] == _GLOBAL_RULE_ROW["id"]

    async def test_update_scope_to_connector_validates_action(self, app):
        """Changing scope to connector rejects non-block action."""
        app, mock_pool = _app_with_mock(app)
        # Existing rule is global with action=route_to:finance
        existing_row = _make_row(_GLOBAL_RULE_ROW)
        mock_pool.fetchrow = AsyncMock(side_effect=[existing_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}",
                json={"scope": "connector:gmail:gmail:user:dev"},
            )

        # The existing action is route_to:finance, which is invalid for connector scope
        assert resp.status_code == 422

    async def test_update_action_validates_against_scope(self, app):
        """Changing action validates against the existing scope."""
        app, mock_pool = _app_with_mock(app)
        existing_row = _make_row(_CONNECTOR_RULE_ROW)
        mock_pool.fetchrow = AsyncMock(side_effect=[existing_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/ingestion-rules/{_CONNECTOR_RULE_ROW['id']}",
                json={"action": "skip"},
            )

        # connector scope only allows block
        assert resp.status_code == 422

    async def test_update_condition_validated_against_rule_type(self, app):
        """Updating condition validates against the existing rule_type."""
        app, mock_pool = _app_with_mock(app)
        existing_row = _make_row(_GLOBAL_RULE_ROW)
        registry_row = _make_row({"name": "finance"})
        # fetchrow calls: 1) existing rule, 2) registry check for route_to
        # then validate_condition should fail before UPDATE
        mock_pool.fetchrow = AsyncMock(side_effect=[existing_row, registry_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}",
                json={"condition": {"chat_id": "123"}},  # wrong for sender_domain
            )

        assert resp.status_code == 422

    async def test_update_negative_priority_returns_422(self, app):
        """Negative priority in update must be rejected."""
        app, _ = _app_with_mock(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}",
                json={"priority": -1},
            )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/switchboard/ingestion-rules/{id}
# ---------------------------------------------------------------------------


class TestDeleteIngestionRule:
    async def test_delete_returns_204(self, app):
        """Successful soft-delete returns 204 No Content."""
        app, _ = _app_with_mock(app, execute_result="UPDATE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}")

        assert resp.status_code == 204

    async def test_delete_nonexistent_returns_404(self, app):
        """Deleting a non-existent rule returns 404."""
        app, _ = _app_with_mock(app, execute_result="UPDATE 0")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(
                "/api/switchboard/ingestion-rules/11111111-1111-1111-1111-111111111111"
            )

        assert resp.status_code == 404

    async def test_delete_invalid_uuid_returns_422(self, app):
        """Deleting with invalid UUID format returns 422."""
        app, _ = _app_with_mock(app)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/switchboard/ingestion-rules/not-a-uuid")

        assert resp.status_code == 422

    async def test_delete_calls_soft_delete_sql(self, app):
        """Delete must set deleted_at and enabled=FALSE, not actually delete the row."""
        app, mock_pool = _app_with_mock(app, execute_result="UPDATE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.delete(f"/api/switchboard/ingestion-rules/{_GLOBAL_RULE_ROW['id']}")

        call_args = mock_pool.execute.call_args
        assert call_args is not None
        query = call_args[0][0]
        assert "deleted_at" in query
        assert "enabled = FALSE" in query
        assert "DELETE" not in query.upper().split("SET")[0]  # no actual DELETE


# ---------------------------------------------------------------------------
# POST /api/switchboard/ingestion-rules/test — dry-run evaluation
# ---------------------------------------------------------------------------


class TestIngestionRulesDryRun:
    async def test_dry_run_with_matching_rule(self, app):
        """Dry-run returns matched=True when a rule matches."""
        app, mock_pool = _app_with_mock(app)
        # Simulate evaluator loading rules from DB
        rule_rows = [
            {
                "id": _GLOBAL_RULE_ROW["id"],
                "rule_type": "sender_domain",
                "condition": {"domain": "chase.com", "match": "exact"},
                "action": "route_to:finance",
                "priority": 10,
                "name": "Chase routing",
                "created_at": "2026-03-08T00:00:00+00:00",
            }
        ]
        mock_pool.fetch = AsyncMock(return_value=[_make_row(r) for r in rule_rows])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules/test",
                json={
                    "envelope": {
                        "sender_address": "alerts@chase.com",
                        "source_channel": "email",
                    },
                    "scope": "global",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["matched"] is True
        assert body["data"]["decision"] == "route_to"
        assert body["data"]["matched_rule_type"] == "sender_domain"

    async def test_dry_run_no_match(self, app):
        """Dry-run returns matched=False when no rule matches."""
        app, mock_pool = _app_with_mock(app)
        mock_pool.fetch = AsyncMock(return_value=[])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules/test",
                json={
                    "envelope": {
                        "sender_address": "hello@example.com",
                        "source_channel": "email",
                    },
                    "scope": "global",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["matched"] is False
        assert body["data"]["decision"] is None

    async def test_dry_run_connector_scope(self, app):
        """Dry-run works with connector scope."""
        app, mock_pool = _app_with_mock(app)
        rule_rows = [
            {
                "id": _CONNECTOR_RULE_ROW["id"],
                "rule_type": "sender_domain",
                "condition": {"domain": "spam.com", "match": "suffix"},
                "action": "block",
                "priority": 5,
                "name": "Block spam",
                "created_at": "2026-03-08T00:00:00+00:00",
            }
        ]
        mock_pool.fetch = AsyncMock(return_value=[_make_row(r) for r in rule_rows])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/ingestion-rules/test",
                json={
                    "envelope": {
                        "sender_address": "promo@spam.com",
                        "source_channel": "email",
                    },
                    "scope": "connector:gmail:gmail:user:dev",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["matched"] is True
        assert body["data"]["decision"] == "block"


# ---------------------------------------------------------------------------
# Model validation tests (unit tests for Pydantic models)
# ---------------------------------------------------------------------------


class TestIngestionRuleModels:
    """Tests for Pydantic model validation logic."""

    def test_validate_scope_global(self):
        """Global scope passes validation."""
        from switchboard_api_models import validate_scope

        assert validate_scope("global") == "global"

    def test_validate_scope_connector(self):
        """Connector scope passes validation."""
        from switchboard_api_models import validate_scope

        assert validate_scope("connector:gmail:gmail:user:dev") == "connector:gmail:gmail:user:dev"

    def test_validate_scope_invalid(self):
        """Invalid scope raises ValueError."""
        from switchboard_api_models import validate_scope

        with pytest.raises(ValueError):
            validate_scope("invalid")

    def test_validate_scope_connector_missing_parts(self):
        """Connector scope missing identity raises ValueError."""
        from switchboard_api_models import validate_scope

        with pytest.raises(ValueError):
            validate_scope("connector:gmail")

    def test_validate_ingestion_action_global_skip(self):
        """skip is valid for global scope."""
        from switchboard_api_models import validate_ingestion_action

        assert validate_ingestion_action("skip", "global") == "skip"

    def test_validate_ingestion_action_global_route_to(self):
        """route_to:<butler> is valid for global scope."""
        from switchboard_api_models import validate_ingestion_action

        assert validate_ingestion_action("route_to:finance", "global") == "route_to:finance"

    def test_validate_ingestion_action_connector_block(self):
        """block is valid for connector scope."""
        from switchboard_api_models import validate_ingestion_action

        assert validate_ingestion_action("block", "connector:gmail:dev") == "block"

    def test_validate_ingestion_action_connector_skip_fails(self):
        """skip is invalid for connector scope."""
        from switchboard_api_models import validate_ingestion_action

        with pytest.raises(ValueError):
            validate_ingestion_action("skip", "connector:gmail:dev")

    def test_validate_rule_type_for_scope_global_any(self):
        """Any rule type is valid for global scope."""
        from switchboard_api_models import validate_rule_type_for_scope

        assert validate_rule_type_for_scope("chat_id", "global") == "chat_id"

    def test_validate_rule_type_for_scope_gmail_sender_domain(self):
        """sender_domain is valid for gmail connector scope."""
        from switchboard_api_models import validate_rule_type_for_scope

        assert (
            validate_rule_type_for_scope("sender_domain", "connector:gmail:dev") == "sender_domain"
        )

    def test_validate_rule_type_for_scope_gmail_chat_id_fails(self):
        """chat_id is invalid for gmail connector scope."""
        from switchboard_api_models import validate_rule_type_for_scope

        with pytest.raises(ValueError):
            validate_rule_type_for_scope("chat_id", "connector:gmail:dev")

    def test_validate_rule_type_for_scope_telegram_chat_id(self):
        """chat_id is valid for telegram-bot connector scope."""
        from switchboard_api_models import validate_rule_type_for_scope

        assert validate_rule_type_for_scope("chat_id", "connector:telegram-bot:my-bot") == "chat_id"

    def test_validate_rule_type_for_scope_discord_channel_id(self):
        """channel_id is valid for discord connector scope."""
        from switchboard_api_models import validate_rule_type_for_scope

        assert (
            validate_rule_type_for_scope("channel_id", "connector:discord:my-server")
            == "channel_id"
        )

    def test_validate_rule_type_for_scope_unknown_connector_allows_all(self):
        """Unknown connector type allows all rule types (forward-compat)."""
        from switchboard_api_models import validate_rule_type_for_scope

        assert validate_rule_type_for_scope("chat_id", "connector:whatsapp:my-phone") == "chat_id"

    def test_validate_condition_substring(self):
        """substring condition validates correctly."""
        from switchboard_api_models import validate_condition

        result = validate_condition("substring", {"pattern": "newsletter"})
        assert result == {"pattern": "newsletter"}

    def test_validate_condition_substring_empty_fails(self):
        """Empty substring pattern fails validation."""
        from switchboard_api_models import validate_condition

        with pytest.raises(ValueError):
            validate_condition("substring", {"pattern": ""})

    def test_validate_condition_chat_id(self):
        """chat_id condition validates correctly."""
        from switchboard_api_models import validate_condition

        result = validate_condition("chat_id", {"chat_id": "987654321"})
        assert result == {"chat_id": "987654321"}

    def test_validate_condition_channel_id(self):
        """channel_id condition validates correctly."""
        from switchboard_api_models import validate_condition

        result = validate_condition("channel_id", {"channel_id": "123456789"})
        assert result == {"channel_id": "123456789"}

    def test_ingestion_rule_create_cross_validation(self):
        """IngestionRuleCreate cross-validates scope, action, rule_type, and condition."""
        from switchboard_api_models import IngestionRuleCreate

        # Valid: global scope with skip action
        rule = IngestionRuleCreate(
            scope="global",
            rule_type="sender_domain",
            condition={"domain": "example.com", "match": "exact"},
            action="skip",
            priority=10,
        )
        assert rule.scope == "global"

    def test_ingestion_rule_create_connector_non_block_fails(self):
        """IngestionRuleCreate rejects connector scope with non-block action."""
        from switchboard_api_models import IngestionRuleCreate

        with pytest.raises(ValueError, match="connector scope"):
            IngestionRuleCreate(
                scope="connector:gmail:dev",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="skip",
                priority=10,
            )
