"""Tests for connector filter assignment API endpoints.

Covers:
- GET /api/switchboard/connectors/{type}/{identity}/filters
- PUT /api/switchboard/connectors/{type}/{identity}/filters

Issue: bu-qbq.3
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.api.db import DatabaseManager

_roster_root = Path(__file__).resolve().parents[2] / "roster"
_router_path = _roster_root / "switchboard" / "api" / "router.py"
_MODULE_NAME = "switchboard_api_router"

if _MODULE_NAME in sys.modules:
    switchboard_module = sys.modules[_MODULE_NAME]
else:
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {_router_path}")
    switchboard_module = importlib.util.module_from_spec(spec)
    sys.modules[_MODULE_NAME] = switchboard_module
    spec.loader.exec_module(switchboard_module)

pytestmark = pytest.mark.unit

_FILTER_ID_1 = str(uuid.uuid4())
_FILTER_ID_2 = str(uuid.uuid4())
_FILTER_ID_3 = str(uuid.uuid4())


def _current_get_db_manager():
    return sys.modules[_MODULE_NAME]._get_db_manager


def _make_filter_row(
    *,
    filter_id: str = _FILTER_ID_1,
    name: str = "block-spam",
    filter_mode: str = "blacklist",
    source_key_type: str = "domain",
    pattern_count: int = 3,
    enabled: bool = False,
    priority: int = 0,
) -> dict:
    """Build a dict mimicking an asyncpg row from the GET filters query."""
    return {
        "filter_id": uuid.UUID(filter_id),
        "name": name,
        "filter_mode": filter_mode,
        "source_key_type": source_key_type,
        "pattern_count": pattern_count,
        "enabled": enabled,
        "priority": priority,
    }


def _make_source_filter_id_row(filter_id: str) -> dict:
    """Mimics a row returned by the PUT validation query (SELECT id FROM source_filters)."""
    return {"id": uuid.UUID(filter_id)}


def _app_with_mock_pool(
    app,
    *,
    fetch_side_effect=None,
    fetch_rows: list | None = None,
    pool_available: bool = True,
    acquire_conn: object | None = None,
):
    """Wire app with a mocked pool.

    fetch_side_effect: passed as side_effect to pool.fetch (overrides fetch_rows).
    fetch_rows: static return value for pool.fetch.
    acquire_conn: a mock asyncpg connection for pool.acquire() context manager.
    """
    mock_pool = AsyncMock()

    if fetch_side_effect is not None:
        mock_pool.fetch = AsyncMock(side_effect=fetch_side_effect)
    else:
        mock_pool.fetch = AsyncMock(return_value=fetch_rows or [])

    if acquire_conn is not None:
        acquire_cm = AsyncMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=acquire_conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=acquire_cm)

    mock_db = MagicMock(spec=DatabaseManager)
    if pool_available:
        mock_db.pool.return_value = mock_pool
    else:
        mock_db.pool.side_effect = KeyError("No pool for butler: switchboard")

    app.dependency_overrides[_current_get_db_manager()] = lambda: mock_db
    return app


def _make_mock_conn():
    """Build a mock asyncpg connection with transaction support."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value="DELETE 0")
    mock_conn.executemany = AsyncMock(return_value=None)

    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=tx)
    return mock_conn


# ---------------------------------------------------------------------------
# GET /connectors/{type}/{identity}/filters
# ---------------------------------------------------------------------------


class TestListConnectorFilters:
    async def test_empty_returns_empty_list(self, app):
        """No source filters defined → returns empty list."""
        app = _app_with_mock_pool(app, fetch_rows=[])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_unattached_filter_has_enabled_false(self, app):
        """Filters that exist but are not attached to this connector show enabled=False."""
        row = _make_filter_row(enabled=False, priority=0)
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["enabled"] is False
        assert data[0]["name"] == "block-spam"

    async def test_attached_filter_has_enabled_true(self, app):
        """Filter attached to connector is returned with enabled=True and correct priority."""
        row = _make_filter_row(enabled=True, priority=5)
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data[0]["enabled"] is True
        assert data[0]["priority"] == 5

    async def test_incompatible_flag_for_wrong_key_type_on_gmail(self, app):
        """chat_id is incompatible with gmail connector → incompatible=True."""
        row = _make_filter_row(source_key_type="chat_id")
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        assert resp.status_code == 200
        assert resp.json()["data"][0]["incompatible"] is True

    async def test_compatible_key_type_on_gmail(self, app):
        """domain is valid for gmail → incompatible=False."""
        row = _make_filter_row(source_key_type="domain")
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        assert resp.status_code == 200
        assert resp.json()["data"][0]["incompatible"] is False

    async def test_incompatible_domain_on_telegram_bot(self, app):
        """domain is not valid for telegram-bot connector → incompatible=True."""
        row = _make_filter_row(source_key_type="domain")
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram-bot/bot-123/filters")

        assert resp.status_code == 200
        assert resp.json()["data"][0]["incompatible"] is True

    async def test_chat_id_compatible_on_telegram_bot(self, app):
        """chat_id is valid for telegram-bot → incompatible=False."""
        row = _make_filter_row(source_key_type="chat_id")
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/telegram-bot/bot-123/filters")

        assert resp.status_code == 200
        assert resp.json()["data"][0]["incompatible"] is False

    async def test_unknown_connector_type_no_incompatible(self, app):
        """Unknown connector type → all filters pass-through (incompatible=False)."""
        row = _make_filter_row(source_key_type="some_unknown_type")
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/switchboard/connectors/custom-connector/identity-abc/filters"
            )

        assert resp.status_code == 200
        assert resp.json()["data"][0]["incompatible"] is False

    async def test_multiple_filters_all_returned(self, app):
        """GET returns all named filters regardless of attachment state."""
        rows = [
            _make_filter_row(filter_id=_FILTER_ID_1, name="alpha", enabled=True, priority=1),
            _make_filter_row(filter_id=_FILTER_ID_2, name="beta", enabled=False, priority=0),
        ]
        app = _app_with_mock_pool(app, fetch_rows=rows)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2

    async def test_response_includes_all_required_fields(self, app):
        """Each returned item includes all ConnectorFilterAssignment fields."""
        row = _make_filter_row(
            filter_id=_FILTER_ID_1,
            name="my-filter",
            filter_mode="whitelist",
            source_key_type="sender_address",
            pattern_count=2,
            enabled=True,
            priority=10,
        )
        app = _app_with_mock_pool(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        item = resp.json()["data"][0]
        assert item["filter_id"] == _FILTER_ID_1
        assert item["name"] == "my-filter"
        assert item["filter_mode"] == "whitelist"
        assert item["source_key_type"] == "sender_address"
        assert item["pattern_count"] == 2
        assert item["enabled"] is True
        assert item["priority"] == 10
        assert item["incompatible"] is False

    async def test_db_unavailable_returns_503(self, app):
        """When DB pool is missing, returns 503."""
        app = _app_with_mock_pool(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/switchboard/connectors/gmail/user%40example.com/filters")

        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# PUT /connectors/{connector_type}/{endpoint_identity}/filters
# ---------------------------------------------------------------------------


class TestReplaceConnectorFilters:
    def _make_put_app(self, app, *, validation_rows: list, post_update_rows: list):
        """Build an app for PUT tests.

        fetch is called twice:
          1. Validation query (SELECT id FROM source_filters WHERE id = ANY(...))
          2. Subsequent GET query (SELECT ... FROM source_filters LEFT JOIN ...)
        """
        call_count = {"n": 0}
        responses = [validation_rows, post_update_rows]

        async def fetch_side_effect(sql, *args):
            idx = call_count["n"]
            call_count["n"] += 1
            return responses[idx] if idx < len(responses) else []

        mock_conn = _make_mock_conn()
        return _app_with_mock_pool(
            app,
            fetch_side_effect=fetch_side_effect,
            acquire_conn=mock_conn,
        ), mock_conn

    async def test_empty_body_detaches_all_filters(self, app):
        """PUT with empty list calls DELETE but no INSERT, returns empty list."""
        # Empty body: no validation query needed; GET returns empty
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        mock_conn = _make_mock_conn()
        acquire_cm = AsyncMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)
        mock_pool.acquire = MagicMock(return_value=acquire_cm)

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.pool.return_value = mock_pool
        app.dependency_overrides[_current_get_db_manager()] = lambda: mock_db

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/switchboard/connectors/gmail/user%40example.com/filters",
                json=[],
            )

        assert resp.status_code == 200
        assert resp.json()["data"] == []
        mock_conn.execute.assert_called_once()
        mock_conn.executemany.assert_not_called()

    async def test_put_replaces_assignments(self, app):
        """PUT with one item deletes old assignments and inserts new ones."""
        validation_rows = [_make_source_filter_id_row(_FILTER_ID_1)]
        post_update_rows = [_make_filter_row(filter_id=_FILTER_ID_1, enabled=True, priority=0)]

        app, mock_conn = self._make_put_app(
            app,
            validation_rows=validation_rows,
            post_update_rows=post_update_rows,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/switchboard/connectors/gmail/user%40example.com/filters",
                json=[{"filter_id": _FILTER_ID_1, "enabled": True, "priority": 0}],
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 1
        assert data[0]["enabled"] is True
        mock_conn.execute.assert_called_once()
        mock_conn.executemany.assert_called_once()

    async def test_put_returns_422_for_unknown_filter_id(self, app):
        """PUT with unknown filter_id returns 422."""
        unknown_id = str(uuid.uuid4())
        # Validation returns empty (no matching filter found)
        app = _app_with_mock_pool(app, fetch_rows=[])
        # Need acquire too (even though it won't be reached)
        mock_conn = _make_mock_conn()
        acquire_cm = AsyncMock()
        acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        acquire_cm.__aexit__ = AsyncMock(return_value=None)
        # Reach into the already-set mock to add acquire
        mock_db = app.dependency_overrides[_current_get_db_manager()]()
        mock_db.pool.return_value.acquire = MagicMock(return_value=acquire_cm)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/switchboard/connectors/gmail/user%40example.com/filters",
                json=[{"filter_id": unknown_id, "enabled": True, "priority": 0}],
            )

        assert resp.status_code == 422
        assert unknown_id in resp.json()["detail"]

    async def test_put_multiple_items_inserts_all(self, app):
        """PUT with multiple items inserts all of them."""
        validation_rows = [
            _make_source_filter_id_row(_FILTER_ID_1),
            _make_source_filter_id_row(_FILTER_ID_2),
        ]
        post_update_rows = [
            _make_filter_row(filter_id=_FILTER_ID_1, name="filter-a", enabled=True, priority=0),
            _make_filter_row(filter_id=_FILTER_ID_2, name="filter-b", enabled=False, priority=1),
        ]

        app, mock_conn = self._make_put_app(
            app,
            validation_rows=validation_rows,
            post_update_rows=post_update_rows,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/switchboard/connectors/gmail/user%40example.com/filters",
                json=[
                    {"filter_id": _FILTER_ID_1, "enabled": True, "priority": 0},
                    {"filter_id": _FILTER_ID_2, "enabled": False, "priority": 1},
                ],
            )

        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        # executemany called with 2 rows
        call_args = mock_conn.executemany.call_args
        rows = call_args[0][1]
        assert len(rows) == 2

    async def test_put_returns_updated_assignments_from_get(self, app):
        """PUT returns the updated assignment list (same shape as GET response)."""
        validation_rows = [_make_source_filter_id_row(_FILTER_ID_1)]
        post_update_rows = [
            _make_filter_row(
                filter_id=_FILTER_ID_1,
                name="allowed-domains",
                filter_mode="whitelist",
                source_key_type="domain",
                pattern_count=5,
                enabled=True,
                priority=3,
            )
        ]

        app, _ = self._make_put_app(
            app,
            validation_rows=validation_rows,
            post_update_rows=post_update_rows,
        )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/switchboard/connectors/gmail/user%40example.com/filters",
                json=[{"filter_id": _FILTER_ID_1, "enabled": True, "priority": 3}],
            )

        assert resp.status_code == 200
        item = resp.json()["data"][0]
        assert item["name"] == "allowed-domains"
        assert item["filter_mode"] == "whitelist"
        assert item["source_key_type"] == "domain"
        assert item["pattern_count"] == 5
        assert item["enabled"] is True
        assert item["priority"] == 3
        assert item["incompatible"] is False

    async def test_put_db_unavailable_returns_503(self, app):
        """When DB pool is missing, PUT returns 503."""
        app = _app_with_mock_pool(app, pool_available=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.put(
                "/api/switchboard/connectors/gmail/user%40example.com/filters",
                json=[],
            )

        assert resp.status_code == 503
