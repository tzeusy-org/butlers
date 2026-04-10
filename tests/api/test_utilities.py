"""Condensed utility and domain model tests.

Condensed from:
  test_deps.py (30) + test_secrets.py (28) + test_models.py (25) + test_state.py (19)
  + test_search.py (18) + test_provider_settings.py (18) + test_relationship_models.py (17)
  + test_timeline.py (18) + test_router_discovery.py (14) + test_db.py (21)
  → ~20 tests (bu-egmz6)

Keeps: key model serialization, DB pool creation, dependency contracts,
and endpoint 200/404/503 status codes.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
)
from butlers.api.models import ApiResponse, PaginationMeta
from butlers.api.routers.provider_settings import _get_db_manager as _provider_get_db
from butlers.api.routers.search import _extract_snippet
from butlers.api.routers.search import _get_db_manager as _search_get_db
from butlers.api.routers.secrets import _get_db_manager as _secrets_get_db
from butlers.api.routers.state import _get_db_manager as _state_get_db
from butlers.api.routers.timeline import _get_db_manager as _timeline_get_db
from butlers.credential_store import SecretMetadata

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)
_roster_root = Path(__file__).resolve().parents[2] / "roster"


# ---------------------------------------------------------------------------
# Database Manager
# ---------------------------------------------------------------------------


class TestDatabaseManager:
    @patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
    async def test_add_butler_creates_accessible_pool(self, mock_create):
        pool = AsyncMock()
        mock_create.return_value = pool
        mgr = DatabaseManager(host="localhost", port=5432, user="pg", password="secret")
        await mgr.add_butler("switchboard")
        assert mgr.pool("switchboard") is pool


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


class TestDependencies:
    def test_butler_connection_info_sse_url(self):
        info = ButlerConnectionInfo("switchboard", 41100)
        assert info.sse_url == "http://localhost:41100/sse"
        assert info.mcp_url == "http://localhost:41100/mcp"

    async def test_mcp_manager_get_client_raises_for_unregistered(self):
        mgr = MCPClientManager()
        with pytest.raises(ButlerUnreachableError):
            await mgr.get_client("nonexistent")

    @patch("butlers.api.deps.MCPClient")
    async def test_mcp_manager_uses_canonical_runtime_url(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value = mock_client

        mgr = MCPClientManager()
        mgr.register("switchboard", ButlerConnectionInfo("switchboard", 41100))

        client = await mgr.get_client("switchboard")

        assert client is mock_client
        mock_client_cls.assert_called_once_with(
            "http://localhost:41100/mcp",
            name="dashboard-switchboard",
        )

    def test_mcp_manager_register_and_list(self):
        mgr = MCPClientManager()
        mgr.register("alpha", ButlerConnectionInfo("alpha", 41100))
        assert "alpha" in mgr.butler_names


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestModels:
    def test_api_response_wraps_data(self):
        resp = ApiResponse(data="hello")
        assert resp.data == "hello"
        assert resp.model_dump()["data"] == "hello"

    def test_pagination_meta_fields(self):
        meta = PaginationMeta(total=100, offset=0, limit=20)
        assert meta.total == 100
        assert meta.limit == 20


# ---------------------------------------------------------------------------
# Relationship Pydantic Models
# ---------------------------------------------------------------------------


def _load_rel_models():
    name = "relationship_api_models"
    if name not in sys.modules:
        path = _roster_root / "relationship" / "api" / "models.py"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


class TestRelationshipModels:
    def test_contact_summary_minimal(self):
        from uuid import uuid4

        mods = _load_rel_models()
        cid = uuid4()
        contact = mods.ContactSummary(id=cid, full_name="Alice Smith")
        assert contact.id == cid
        assert contact.full_name == "Alice Smith"
        assert contact.nickname is None

    def test_label_minimal(self):
        from uuid import uuid4

        mods = _load_rel_models()
        lid = uuid4()
        label = mods.Label(id=lid, name="Friend")
        assert label.name == "Friend"
        assert label.color is None


# ---------------------------------------------------------------------------
# Secrets API
# ---------------------------------------------------------------------------


@contextmanager
def _secrets_app(
    app, *, list_return=None, store_side_effect=None, delete_return=True, pool_raises=None
):
    mock_pool = MagicMock()
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_raises:
        mock_db.pool.side_effect = pool_raises
    else:
        mock_db.pool.return_value = mock_pool
    mock_store = AsyncMock()
    mock_store.list_secrets.return_value = list_return or []
    if store_side_effect:
        mock_store.store.side_effect = store_side_effect
    else:
        mock_store.store.return_value = None
    mock_store.delete.return_value = delete_return
    app.dependency_overrides[_secrets_get_db] = lambda: mock_db
    with patch("butlers.api.routers.secrets.CredentialStore", return_value=mock_store):
        yield app, mock_store


class TestSecretsAPI:
    async def test_list_returns_entries(self, app):
        meta = SecretMetadata(
            key="API_KEY",
            category="test",
            description=None,
            is_sensitive=True,
            is_set=True,
            created_at=_NOW,
            updated_at=_NOW,
            expires_at=None,
            source="database",
        )
        with _secrets_app(app, list_return=[meta]) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets")
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    async def test_list_503_when_pool_unavailable(self, app):
        with _secrets_app(app, pool_raises=KeyError("no pool")) as (app, _):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/butlers/atlas/secrets")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# State API
# ---------------------------------------------------------------------------


def _state_app(app, *, fetch_rows=None, fetchrow_result=None, pool_raises=None):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_rows or [])
    pool.fetchrow = AsyncMock(return_value=fetchrow_result)
    db = MagicMock(spec=DatabaseManager)
    if pool_raises:
        db.pool.side_effect = pool_raises
    else:
        db.pool.return_value = pool
    app.dependency_overrides[_state_get_db] = lambda: db
    return app


class TestStateAPI:
    async def test_list_state_returns_array(self, app):
        row = {
            "key": "alpha",
            "value": {"count": 1},
            "updated_at": _NOW,
            "created_at": _NOW,
            "butler_name": "atlas",
        }
        _state_app(app, fetch_rows=[row])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/state")
        assert resp.status_code == 200
        assert "data" in resp.json()

    async def test_get_state_key_404_when_not_found(self, app):
        _state_app(app, fetchrow_result=None)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/state/missing_key")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Search API
# ---------------------------------------------------------------------------


class TestSearchAPI:
    def test_extract_snippet_contains_match(self):
        text = "a" * 100 + "MATCH" + "b" * 100
        snippet = _extract_snippet(text, "MATCH", max_len=40)
        assert "MATCH" in snippet

    async def test_search_returns_200(self, app):
        db = MagicMock(spec=DatabaseManager)
        db.butler_names = ["atlas"]
        db.fan_out = AsyncMock(return_value={})
        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        db.pool = MagicMock(return_value=pool)
        app.dependency_overrides[_search_get_db] = lambda: db
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/search?q=hello")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Provider Settings API
# ---------------------------------------------------------------------------


class TestProviderSettingsAPI:
    def _make_app(self, app, *, fetch_rows=None, fetchrow=None, execute=None):
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=fetch_rows or [])
        pool.fetchrow = AsyncMock(return_value=fetchrow)
        pool.execute = AsyncMock(return_value=execute or "OK")
        db = MagicMock(spec=DatabaseManager)
        db.credential_shared_pool.return_value = pool
        app.dependency_overrides[_provider_get_db] = lambda: db
        return app, pool

    async def test_list_returns_empty(self, app):
        self._make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/settings/providers")
        assert resp.status_code == 200

    async def test_delete_404_when_not_found(self, app):
        app, mock_pool = self._make_app(app)
        mock_pool.execute = AsyncMock(return_value="DELETE 0")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/settings/providers/ollama")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Timeline API
# ---------------------------------------------------------------------------


class TestTimelineAPI:
    def _make_app(self, app, *, fan_out=None):
        db = MagicMock(spec=DatabaseManager)
        db.butler_names = ["atlas"]
        db.fan_out = AsyncMock(return_value=fan_out or {})
        pool = AsyncMock()
        pool.fetch = AsyncMock(return_value=[])
        db.pool = MagicMock(return_value=pool)
        app.dependency_overrides[_timeline_get_db] = lambda: db
        return app

    async def test_timeline_returns_200(self, app):
        self._make_app(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/timeline")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Router Discovery
# ---------------------------------------------------------------------------


class TestRouterDiscovery:
    def test_load_router_module_from_valid_file(self, tmp_path):
        from butlers.api.router_discovery import _load_router_module

        router_file = tmp_path / "router.py"
        router_file.write_text(
            "from fastapi import APIRouter\nrouter = APIRouter(prefix='/api/test')\n"
        )
        mod = _load_router_module(router_file, "test_router_mod")
        assert hasattr(mod, "router")

    def test_discover_butler_routers_skips_missing_files(self, tmp_path):
        from butlers.api.router_discovery import discover_butler_routers

        # A butler dir without an api/router.py should be skipped silently
        butler_dir = tmp_path / "mybutler"
        butler_dir.mkdir()
        routers = discover_butler_routers(tmp_path)
        # No error, just empty or filtered list
        assert isinstance(routers, list)


# ---------------------------------------------------------------------------
# DB env parsing (from test_deps_db_params.py)
# ---------------------------------------------------------------------------


class TestDBParamsFromEnv:
    def test_parses_database_url_sslmode(self, monkeypatch):
        from butlers.api.deps import _db_params_from_env

        monkeypatch.setenv(
            "DATABASE_URL", "postgres://u:p@db.internal:5432/postgres?sslmode=disable"
        )
        params = _db_params_from_env()
        assert params["host"] == "db.internal"
        assert params["ssl"] == "disable"

    def test_uses_postgres_sslmode_fallback(self, monkeypatch):
        from butlers.api.deps import _db_params_from_env

        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "dbhost")
        monkeypatch.setenv("POSTGRES_PORT", "6543")
        monkeypatch.setenv("POSTGRES_USER", "user1")
        monkeypatch.setenv("POSTGRES_PASSWORD", "pass1")
        monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")
        params = _db_params_from_env()
        assert params["host"] == "dbhost"
        assert params["ssl"] == "verify-full"
