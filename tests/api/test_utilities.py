"""Condensed utility and domain model tests.

Condensed from:
  test_deps.py (30) + test_secrets.py (28) + test_models.py (25) + test_state.py (19)
  + test_search.py (18) + test_provider_settings.py (18) + test_relationship_models.py (17)
  + test_timeline.py (18) + test_router_discovery.py (14) + test_db.py (21)
  → ~20 tests (bu-egmz6) → 5 tests (bu-2yw2d)

Keeps: pool creation, dependency contracts, secrets 200/503, state 404,
       DB env parsing (parametrized).
Drops: trivial Pydantic field tests, router discovery internals,
       relationship model round-trips, snippet helpers, duplicate error paths.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from butlers.api.db import DatabaseManager
from butlers.api.deps import (
    ButlerConnectionInfo,
    ButlerUnreachableError,
    MCPClientManager,
    init_db_manager,
)
from butlers.api.routers.secrets import _get_db_manager as _secrets_get_db
from butlers.api.routers.state import _get_db_manager as _state_get_db
from butlers.credential_store import SecretMetadata

pytestmark = pytest.mark.unit

_NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Database Manager + Dependencies (combined)
# ---------------------------------------------------------------------------


class TestDatabaseAndDeps:
    @patch("butlers.api.db.asyncpg.create_pool", new_callable=AsyncMock)
    async def test_add_butler_creates_accessible_pool(self, mock_create):
        pool = AsyncMock()
        mock_create.return_value = pool
        mgr = DatabaseManager(host="localhost", port=5432, user="pg", password="secret")
        await mgr.add_butler("switchboard")
        assert mgr.pool("switchboard") is pool

    async def test_mcp_manager_raises_for_unregistered_and_lists_registered(self):
        mgr = MCPClientManager()
        with pytest.raises(ButlerUnreachableError):
            await mgr.get_client("nonexistent")
        mgr.register("alpha", ButlerConnectionInfo("alpha", 41100))
        assert "alpha" in mgr.butler_names

    async def test_init_db_manager_logs_butler_name_db_and_schema_on_pool_failure(self, caplog):
        """Pool-init failure warning includes butler name, db name, and schema."""
        cfg = ButlerConnectionInfo(
            name="ghost",
            port=41999,
            db_name="butlers",
            db_schema="ghost",
        )
        with (
            patch("butlers.api.deps.Database.from_env", side_effect=RuntimeError("conn refused")),
            patch("butlers.api.deps.shared_db_name_from_env", return_value="butlers"),
            patch("butlers.api.deps.DatabaseManager") as MockMgr,
            caplog.at_level(logging.WARNING, logger="butlers.api.deps"),
        ):
            MockMgr.return_value = AsyncMock()
            await init_db_manager([cfg])

        warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "ghost" in msg and "butlers" in msg and "ghost" in msg for msg in warning_messages
        ), f"Expected butler name/db/schema in warning. Got: {warning_messages}"


# ---------------------------------------------------------------------------
# Secrets API (happy path + 503 fallback)
# ---------------------------------------------------------------------------


@contextmanager
def _secrets_app(app, *, list_return=None, pool_raises=None):
    mock_db = MagicMock(spec=DatabaseManager)
    if pool_raises:
        mock_db.pool.side_effect = pool_raises
    else:
        mock_db.pool.return_value = MagicMock()
    mock_store = AsyncMock()
    mock_store.list_secrets.return_value = list_return or []
    app.dependency_overrides[_secrets_get_db] = lambda: mock_db
    with patch("butlers.api.routers.secrets.CredentialStore", return_value=mock_store):
        yield app


@pytest.mark.parametrize(
    "list_return,pool_raises,expected_status",
    [
        (
            [
                SecretMetadata(
                    key="K",
                    category="c",
                    description=None,
                    is_sensitive=True,
                    is_set=True,
                    created_at=_NOW,
                    updated_at=_NOW,
                    expires_at=None,
                    source="database",
                )
            ],
            None,
            200,
        ),
        (None, KeyError("no pool"), 503),
    ],
    ids=["happy", "503"],
)
async def test_secrets_list(app, list_return, pool_raises, expected_status):
    with _secrets_app(app, list_return=list_return, pool_raises=pool_raises) as a:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=a), base_url="http://test"
        ) as client:
            resp = await client.get("/api/butlers/atlas/secrets")
    assert resp.status_code == expected_status


# ---------------------------------------------------------------------------
# State API — 404 for missing key
# ---------------------------------------------------------------------------


async def test_state_key_404_when_not_found(app):
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    db = MagicMock(spec=DatabaseManager)
    db.pool.return_value = pool
    app.dependency_overrides[_state_get_db] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers/atlas/state/missing_key")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DB env parsing (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_vars,expected_host,expected_ssl",
    [
        (
            {"DATABASE_URL": "postgres://u:p@db.internal:5432/postgres?sslmode=disable"},
            "db.internal",
            "disable",
        ),
        (
            {
                "POSTGRES_HOST": "dbhost",
                "POSTGRES_PORT": "6543",
                "POSTGRES_USER": "user1",
                "POSTGRES_PASSWORD": "pass1",
                "POSTGRES_SSLMODE": "verify-full",
            },
            "dbhost",
            "verify-full",
        ),
    ],
    ids=["database-url", "postgres-env"],
)
def test_db_params_from_env(monkeypatch, env_vars, expected_host, expected_ssl):
    from butlers.api.deps import _db_params_from_env

    monkeypatch.delenv("DATABASE_URL", raising=False)
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    params = _db_params_from_env()
    assert params["host"] == expected_host
    assert params["ssl"] == expected_ssl
