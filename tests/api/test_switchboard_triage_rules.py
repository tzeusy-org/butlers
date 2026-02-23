"""Tests for switchboard triage rule API endpoints.

Covers:
- GET /api/switchboard/triage-rules (list with optional filters)
- POST /api/switchboard/triage-rules (create with validation)
- PATCH /api/switchboard/triage-rules/{id} (partial update)
- DELETE /api/switchboard/triage-rules/{id} (soft-delete)
- POST /api/switchboard/triage-rules/test (dry-run evaluation)

Uses mocked DatabaseManager — no real database required.

Issue: butlers-dsa4.1.2
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager

_MODULE_NAME = "switchboard_api_router"
_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------

_RULE_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "rule_type": "sender_domain",
    "condition": {"domain": "chase.com", "match": "exact"},
    "action": "route_to:finance",
    "priority": 10,
    "enabled": True,
    "created_by": "dashboard",
    "created_at": "2026-02-22T00:00:00+00:00",
    "updated_at": "2026-02-22T00:00:00+00:00",
}

_HEADER_RULE_ROW = {
    "id": "22222222-2222-2222-2222-222222222222",
    "rule_type": "header_condition",
    "condition": {"header": "List-Unsubscribe", "op": "present", "value": None},
    "action": "metadata_only",
    "priority": 40,
    "enabled": True,
    "created_by": "seed",
    "created_at": "2026-02-22T00:00:00+00:00",
    "updated_at": "2026-02-22T00:00:00+00:00",
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
    row.keys = lambda: data.keys()
    row.__iter__ = lambda self: iter(data)
    return row


def _app_with_mock(
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
    app = create_app(cors_origins=["*"])
    app.dependency_overrides[get_dep] = lambda: mock_db
    return app, mock_pool


# ---------------------------------------------------------------------------
# GET /api/switchboard/triage-rules
# ---------------------------------------------------------------------------


class TestListTriageRules:
    async def test_returns_api_response_structure(self):
        """Response must have 'data' list and 'meta' with total."""
        app, _ = _app_with_mock(fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/triage-rules")

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert isinstance(body["data"], list)
        assert "total" in body["meta"]

    async def test_empty_results(self):
        """When no rules exist, data is empty list with total=0."""
        app, _ = _app_with_mock(fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/triage-rules")

        body = resp.json()
        assert body["data"] == []
        assert body["meta"]["total"] == 0

    async def test_returns_rule_fields(self):
        """Each rule must have all required fields."""
        app, _ = _app_with_mock(fetch_rows=[_RULE_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/triage-rules")

        body = resp.json()
        assert len(body["data"]) == 1
        rule = body["data"][0]
        assert rule["id"] == _RULE_ROW["id"]
        assert rule["rule_type"] == "sender_domain"
        assert rule["action"] == "route_to:finance"
        assert rule["priority"] == 10
        assert rule["enabled"] is True
        assert rule["created_by"] == "dashboard"
        assert "condition" in rule
        assert "created_at" in rule
        assert "updated_at" in rule

    async def test_rule_type_filter_accepted(self):
        """?rule_type= query parameter must be accepted."""
        app, mock_pool = _app_with_mock(fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/triage-rules", params={"rule_type": "sender_domain"}
            )

        assert resp.status_code == 200
        # Verify the SQL query contained the filter
        call_args = mock_pool.fetch.call_args
        assert call_args is not None
        query = call_args[0][0]
        assert "rule_type" in query

    async def test_enabled_filter_accepted(self):
        """?enabled= query parameter must be accepted."""
        app, _ = _app_with_mock(fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/triage-rules", params={"enabled": "true"})

        assert resp.status_code == 200

    async def test_meta_total_matches_row_count(self):
        """meta.total must equal the number of returned rows."""
        app, _ = _app_with_mock(fetch_rows=[_RULE_ROW, _HEADER_RULE_ROW])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/triage-rules")

        body = resp.json()
        assert body["meta"]["total"] == 2
        assert len(body["data"]) == 2

    async def test_pool_unavailable_returns_503(self):
        """When DB pool is unavailable, must return 503."""
        app, _ = _app_with_mock(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/triage-rules")

        assert resp.status_code == 503

    async def test_condition_jsonb_decoded(self):
        """condition field must be a dict (not raw JSON string)."""
        # Simulate asyncpg returning condition as a JSON string (edge case)
        row_with_str_condition = dict(_RULE_ROW)
        row_with_str_condition["condition"] = json.dumps({"domain": "chase.com", "match": "exact"})
        app, _ = _app_with_mock(fetch_rows=[row_with_str_condition])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/triage-rules")

        body = resp.json()
        assert isinstance(body["data"][0]["condition"], dict)


# ---------------------------------------------------------------------------
# POST /api/switchboard/triage-rules
# ---------------------------------------------------------------------------


class TestCreateTriageRule:
    async def test_create_returns_201_with_rule(self):
        """Successful create returns 201 with the created rule."""
        app, _ = _app_with_mock(
            fetchrow_result={  # registry lookup for route_to target
                "name": "finance",
            },
        )
        # fetchrow is called twice: registry check + INSERT RETURNING
        # We need to handle this differently — let's set up two sequential fetchrow calls
        app, mock_pool = _app_with_mock()
        registry_row = _make_row({"name": "finance"})
        created_row = _make_row(
            {
                **_RULE_ROW,
                "id": "33333333-3333-3333-3333-333333333333",
            }
        )
        mock_pool.fetchrow = AsyncMock(side_effect=[registry_row, created_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "sender_domain",
                    "condition": {"domain": "chase.com", "match": "exact"},
                    "action": "route_to:finance",
                    "priority": 10,
                    "enabled": True,
                },
            )

        assert resp.status_code == 201
        body = resp.json()
        assert "data" in body
        assert body["data"]["rule_type"] == "sender_domain"

    async def test_create_simple_action_no_registry_check(self):
        """For non-route_to actions, no registry check is performed."""
        app, mock_pool = _app_with_mock()
        created_row = _make_row(
            {
                **_RULE_ROW,
                "action": "metadata_only",
                "rule_type": "header_condition",
                "condition": {"header": "List-Unsubscribe", "op": "present", "value": None},
            }
        )
        mock_pool.fetchrow = AsyncMock(return_value=created_row)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "header_condition",
                    "condition": {"header": "List-Unsubscribe", "op": "present"},
                    "action": "metadata_only",
                    "priority": 40,
                    "enabled": True,
                },
            )

        assert resp.status_code == 201
        # Registry fetchrow should NOT have been called
        # (the only fetchrow call is the INSERT RETURNING)
        assert mock_pool.fetchrow.call_count == 1
        insert_call = mock_pool.fetchrow.call_args_list[0]
        assert "INSERT" in insert_call[0][0]

    async def test_create_invalid_rule_type_returns_422(self):
        """Invalid rule_type must be rejected with 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "invalid_type",
                    "condition": {"domain": "example.com"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_create_invalid_action_returns_422(self):
        """Invalid action must be rejected with 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "invalid_action",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_create_negative_priority_returns_422(self):
        """Negative priority must be rejected with 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "skip",
                    "priority": -1,
                },
            )

        assert resp.status_code == 422

    async def test_create_condition_schema_mismatch_returns_422(self):
        """Condition schema mismatch for rule_type must be rejected with 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            # sender_domain requires 'domain' and 'match', not 'address'
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "sender_domain",
                    "condition": {"address": "bad@example.com"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_create_route_to_unregistered_butler_returns_422(self):
        """route_to action with unregistered target must be rejected with 422."""
        app, mock_pool = _app_with_mock()
        # Registry lookup returns None → butler not found
        mock_pool.fetchrow = AsyncMock(return_value=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "route_to:unknown_butler",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422
        assert "unknown_butler" in resp.json()["detail"]

    async def test_create_header_condition_op_equals_requires_value(self):
        """header_condition with op=equals must require non-empty value."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "header_condition",
                    "condition": {"header": "Precedence", "op": "equals"},  # missing value
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_create_header_condition_op_present_rejects_value(self):
        """header_condition with op=present must reject non-null value."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "header_condition",
                    "condition": {
                        "header": "List-Unsubscribe",
                        "op": "present",
                        "value": "should-not-be-here",
                    },
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 422

    async def test_create_pool_unavailable_returns_503(self):
        """When DB pool is unavailable, must return 503."""
        app, _ = _app_with_mock(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "sender_domain",
                    "condition": {"domain": "example.com", "match": "exact"},
                    "action": "skip",
                    "priority": 10,
                },
            )

        assert resp.status_code == 503

    async def test_create_all_simple_actions_accepted(self):
        """All four simple actions must be valid."""
        for action in ("skip", "metadata_only", "low_priority_queue", "pass_through"):
            app, mock_pool = _app_with_mock()
            created_row = _make_row({**_RULE_ROW, "action": action})
            mock_pool.fetchrow = AsyncMock(return_value=created_row)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/switchboard/triage-rules",
                    json={
                        "rule_type": "sender_domain",
                        "condition": {"domain": "example.com", "match": "exact"},
                        "action": action,
                        "priority": 10,
                    },
                )

            assert resp.status_code == 201, f"action={action!r} failed with {resp.status_code}"

    async def test_create_mime_type_rule(self):
        """mime_type rule_type with valid condition must be accepted."""
        app, mock_pool = _app_with_mock()
        created_row = _make_row(
            {
                **_RULE_ROW,
                "rule_type": "mime_type",
                "condition": {"type": "text/calendar"},
                "action": "route_to:relationship",
            }
        )
        # First fetchrow = registry check for relationship
        registry_row = _make_row({"name": "relationship"})
        mock_pool.fetchrow = AsyncMock(side_effect=[registry_row, created_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules",
                json={
                    "rule_type": "mime_type",
                    "condition": {"type": "text/calendar"},
                    "action": "route_to:relationship",
                    "priority": 50,
                },
            )

        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# PATCH /api/switchboard/triage-rules/{id}
# ---------------------------------------------------------------------------


class TestUpdateTriageRule:
    _RULE_ID = "11111111-1111-1111-1111-111111111111"

    async def test_update_priority_returns_200(self):
        """Partial update of priority must return 200 with updated rule."""
        app, mock_pool = _app_with_mock()
        updated_row = _make_row({**_RULE_ROW, "priority": 99})
        # fetchrow calls: 1) existing rule SELECT, 2) UPDATE RETURNING
        mock_pool.fetchrow = AsyncMock(side_effect=[_make_row(_RULE_ROW), updated_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                json={"priority": 99},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["priority"] == 99

    async def test_update_enabled_toggle(self):
        """Toggling enabled must be accepted."""
        app, mock_pool = _app_with_mock()
        updated_row = _make_row({**_RULE_ROW, "enabled": False})
        mock_pool.fetchrow = AsyncMock(side_effect=[_make_row(_RULE_ROW), updated_row])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                json={"enabled": False},
            )

        assert resp.status_code == 200
        assert resp.json()["data"]["enabled"] is False

    async def test_update_action_validates_route_to(self):
        """Updating action to route_to:<unregistered> must return 422."""
        app, mock_pool = _app_with_mock()
        # Existing rule found, then registry lookup returns None
        mock_pool.fetchrow = AsyncMock(side_effect=[_make_row(_RULE_ROW), None])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                json={"action": "route_to:nonexistent_butler"},
            )

        assert resp.status_code == 422

    async def test_update_condition_validated_against_rule_type(self):
        """Updating condition must be validated against existing rule_type."""
        app, mock_pool = _app_with_mock()
        # Existing rule is sender_domain; condition update with wrong schema
        mock_pool.fetchrow = AsyncMock(return_value=_make_row(_RULE_ROW))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                # address field is for sender_address, not sender_domain
                json={"condition": {"address": "bad@example.com"}},
            )

        assert resp.status_code == 422

    async def test_update_not_found_returns_404(self):
        """When rule doesn't exist (soft-deleted or never existed), return 404."""
        app, mock_pool = _app_with_mock()
        mock_pool.fetchrow = AsyncMock(return_value=None)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                json={"priority": 5},
            )

        assert resp.status_code == 404

    async def test_update_invalid_uuid_returns_422(self):
        """Non-UUID rule_id must return 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                "/api/switchboard/triage-rules/not-a-uuid",
                json={"priority": 5},
            )

        assert resp.status_code == 422

    async def test_update_no_fields_returns_existing_rule(self):
        """Empty patch body must return existing rule unchanged with 200."""
        app, mock_pool = _app_with_mock()
        mock_pool.fetchrow = AsyncMock(return_value=_make_row(_RULE_ROW))

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                json={},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["id"] == self._RULE_ID

    async def test_update_negative_priority_returns_422(self):
        """Negative priority in update must be rejected with 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                json={"priority": -5},
            )

        assert resp.status_code == 422

    async def test_update_pool_unavailable_returns_503(self):
        """When DB pool is unavailable, must return 503."""
        app, _ = _app_with_mock(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.patch(
                f"/api/switchboard/triage-rules/{self._RULE_ID}",
                json={"priority": 5},
            )

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# DELETE /api/switchboard/triage-rules/{id}
# ---------------------------------------------------------------------------


class TestDeleteTriageRule:
    _RULE_ID = "11111111-1111-1111-1111-111111111111"

    async def test_delete_returns_204(self):
        """Successful soft-delete must return 204 No Content."""
        app, mock_pool = _app_with_mock(execute_result="UPDATE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/switchboard/triage-rules/{self._RULE_ID}")

        assert resp.status_code == 204
        assert resp.content == b""

    async def test_delete_not_found_returns_404(self):
        """When rule not found (already deleted or never existed), return 404."""
        app, mock_pool = _app_with_mock(execute_result="UPDATE 0")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/switchboard/triage-rules/{self._RULE_ID}")

        assert resp.status_code == 404

    async def test_delete_sets_soft_delete_columns(self):
        """DELETE must set deleted_at and enabled=FALSE in the UPDATE query."""
        app, mock_pool = _app_with_mock(execute_result="UPDATE 1")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.delete(f"/api/switchboard/triage-rules/{self._RULE_ID}")

        call = mock_pool.execute.call_args
        assert call is not None
        query = call[0][0]
        assert "deleted_at" in query
        assert "enabled" in query

    async def test_delete_invalid_uuid_returns_422(self):
        """Non-UUID rule_id must return 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/switchboard/triage-rules/not-a-uuid")

        assert resp.status_code == 422

    async def test_delete_pool_unavailable_returns_503(self):
        """When DB pool is unavailable, must return 503."""
        app, _ = _app_with_mock(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete(f"/api/switchboard/triage-rules/{self._RULE_ID}")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/switchboard/triage-rules/test (dry-run evaluation)
# ---------------------------------------------------------------------------


class TestTriageRuleTest:
    _SENDER_DOMAIN_RULE = {
        "rule_type": "sender_domain",
        "condition": {"domain": "chase.com", "match": "exact"},
        "action": "route_to:finance",
        "priority": 10,
        "enabled": True,
    }

    _CHASE_ENVELOPE = {
        "sender": {"identity": "alerts@chase.com"},
        "payload": {"headers": {}, "mime_parts": []},
    }

    async def test_matched_sender_domain_exact(self):
        """Exact sender_domain match must return matched=True."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": self._CHASE_ENVELOPE,
                    "rule": self._SENDER_DOMAIN_RULE,
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["matched"] is True
        assert data["decision"] == "route_to"
        assert data["target_butler"] == "finance"
        assert data["matched_rule_type"] == "sender_domain"
        assert "reason" in data

    async def test_no_match_returns_matched_false(self):
        """Non-matching envelope must return matched=False."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "user@otherdomain.com"},
                        "payload": {"headers": {}, "mime_parts": []},
                    },
                    "rule": self._SENDER_DOMAIN_RULE,
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["matched"] is False
        assert data["decision"] is None
        assert data["target_butler"] is None

    async def test_sender_domain_suffix_match(self):
        """Suffix match must match subdomains."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "alerts@mail.delta.com"},
                        "payload": {"headers": {}, "mime_parts": []},
                    },
                    "rule": {
                        "rule_type": "sender_domain",
                        "condition": {"domain": "delta.com", "match": "suffix"},
                        "action": "route_to:travel",
                        "priority": 20,
                        "enabled": True,
                    },
                },
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["matched"] is True
        assert data["target_butler"] == "travel"

    async def test_sender_domain_exact_does_not_match_subdomain(self):
        """Exact match must NOT match subdomains."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "alerts@mail.delta.com"},
                        "payload": {"headers": {}, "mime_parts": []},
                    },
                    "rule": {
                        "rule_type": "sender_domain",
                        "condition": {"domain": "delta.com", "match": "exact"},
                        "action": "skip",
                        "priority": 20,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is False

    async def test_sender_address_exact_match(self):
        """sender_address rule must match on exact address."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "alerts@chase.com"},
                        "payload": {"headers": {}, "mime_parts": []},
                    },
                    "rule": {
                        "rule_type": "sender_address",
                        "condition": {"address": "alerts@chase.com"},
                        "action": "route_to:finance",
                        "priority": 10,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is True
        assert data["matched_rule_type"] == "sender_address"

    async def test_header_condition_present_matched(self):
        """header_condition op=present must match when header exists."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "news@example.com"},
                        "payload": {
                            "headers": {"List-Unsubscribe": "<mailto:unsubscribe@example.com>"},
                            "mime_parts": [],
                        },
                    },
                    "rule": {
                        "rule_type": "header_condition",
                        "condition": {"header": "List-Unsubscribe", "op": "present"},
                        "action": "metadata_only",
                        "priority": 40,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is True
        assert data["decision"] == "metadata_only"

    async def test_header_condition_present_not_matched(self):
        """header_condition op=present must not match when header absent."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "news@example.com"},
                        "payload": {"headers": {}, "mime_parts": []},
                    },
                    "rule": {
                        "rule_type": "header_condition",
                        "condition": {"header": "List-Unsubscribe", "op": "present"},
                        "action": "metadata_only",
                        "priority": 40,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is False

    async def test_header_condition_equals_matched(self):
        """header_condition op=equals must match on exact header value."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "bulk@example.com"},
                        "payload": {
                            "headers": {"Precedence": "bulk"},
                            "mime_parts": [],
                        },
                    },
                    "rule": {
                        "rule_type": "header_condition",
                        "condition": {"header": "Precedence", "op": "equals", "value": "bulk"},
                        "action": "low_priority_queue",
                        "priority": 41,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is True
        assert data["decision"] == "low_priority_queue"

    async def test_header_condition_contains_matched(self):
        """header_condition op=contains must match when value is substring."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "alerts@example.com"},
                        "payload": {
                            "headers": {"Subject": "Important: Your account alert"},
                            "mime_parts": [],
                        },
                    },
                    "rule": {
                        "rule_type": "header_condition",
                        "condition": {
                            "header": "Subject",
                            "op": "contains",
                            "value": "account alert",
                        },
                        "action": "skip",
                        "priority": 10,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is True

    async def test_mime_type_exact_match(self):
        """mime_type rule must match when MIME part type equals target."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "cal@example.com"},
                        "payload": {
                            "headers": {},
                            "mime_parts": [
                                {"type": "text/plain"},
                                {"type": "text/calendar"},
                            ],
                        },
                    },
                    "rule": {
                        "rule_type": "mime_type",
                        "condition": {"type": "text/calendar"},
                        "action": "route_to:relationship",
                        "priority": 50,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is True
        assert data["target_butler"] == "relationship"

    async def test_mime_type_wildcard_match(self):
        """mime_type with wildcard (image/*) must match any image/* subtype."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "photos@example.com"},
                        "payload": {
                            "headers": {},
                            "mime_parts": [{"type": "image/jpeg"}],
                        },
                    },
                    "rule": {
                        "rule_type": "mime_type",
                        "condition": {"type": "image/*"},
                        "action": "skip",
                        "priority": 60,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is True

    async def test_mime_type_no_match(self):
        """mime_type rule must not match when no part has the target type."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "msg@example.com"},
                        "payload": {
                            "headers": {},
                            "mime_parts": [{"type": "text/plain"}],
                        },
                    },
                    "rule": {
                        "rule_type": "mime_type",
                        "condition": {"type": "text/calendar"},
                        "action": "route_to:relationship",
                        "priority": 50,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is False

    async def test_test_endpoint_is_dry_run(self):
        """Test endpoint must not call pool.execute or pool.fetchrow for inserts."""
        app, mock_pool = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": self._CHASE_ENVELOPE,
                    "rule": self._SENDER_DOMAIN_RULE,
                },
            )

        # execute() must not have been called (no writes)
        mock_pool.execute.assert_not_called()
        # fetchrow must not have been called (no reads — evaluator is pure)
        mock_pool.fetchrow.assert_not_called()

    async def test_test_invalid_rule_returns_422(self):
        """Test endpoint must reject invalid rule with 422."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": self._CHASE_ENVELOPE,
                    "rule": {
                        "rule_type": "invalid_type",
                        "condition": {},
                        "action": "skip",
                        "priority": 10,
                    },
                },
            )

        assert resp.status_code == 422

    async def test_test_response_structure(self):
        """Test response must have 'data' wrapper with required fields."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": self._CHASE_ENVELOPE,
                    "rule": self._SENDER_DOMAIN_RULE,
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        data = body["data"]
        assert "matched" in data
        assert "reason" in data

    async def test_test_pool_unavailable_returns_503(self):
        """When DB pool is unavailable, must return 503."""
        app, _ = _app_with_mock(pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": self._CHASE_ENVELOPE,
                    "rule": self._SENDER_DOMAIN_RULE,
                },
            )

        assert resp.status_code == 503

    async def test_sender_address_no_match(self):
        """sender_address rule must not match on different address."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "other@chase.com"},
                        "payload": {"headers": {}, "mime_parts": []},
                    },
                    "rule": {
                        "rule_type": "sender_address",
                        "condition": {"address": "alerts@chase.com"},
                        "action": "skip",
                        "priority": 10,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is False

    async def test_simple_action_decision_set_correctly(self):
        """For simple (non-route_to) actions, decision must equal the action."""
        app, _ = _app_with_mock()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/switchboard/triage-rules/test",
                json={
                    "envelope": {
                        "sender": {"identity": "news@example.com"},
                        "payload": {
                            "headers": {"List-Unsubscribe": "<mailto:unsub@example.com>"},
                            "mime_parts": [],
                        },
                    },
                    "rule": {
                        "rule_type": "header_condition",
                        "condition": {"header": "List-Unsubscribe", "op": "present"},
                        "action": "pass_through",
                        "priority": 5,
                        "enabled": True,
                    },
                },
            )

        data = resp.json()["data"]
        assert data["matched"] is True
        assert data["decision"] == "pass_through"
        assert data["target_butler"] is None
