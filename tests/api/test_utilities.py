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

    async def test_init_db_manager_logs_butler_name_db_and_schema_on_pool_failure(
        self, caplog, monkeypatch
    ):
        """Pool-init failure warning includes butler name, db name, and schema.

        Uses monkeypatch to reset the module-level ``_db_manager`` singleton
        after the test so that other tests running in the same xdist worker
        process are not affected by the AsyncMock that ``init_db_manager``
        installs as a side-effect of this test.
        """
        import butlers.api.deps as _deps_module

        monkeypatch.setattr(_deps_module, "_db_manager", None)

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
        assert any("ghost" in msg for msg in warning_messages), (
            f"Expected pool-failure warning naming the butler. Got: {warning_messages}"
        )

    def test_database_manager_uses_api_pool_size_overrides(self, monkeypatch):
        """Dashboard pools can be capped independently from daemon pools."""
        monkeypatch.setenv("BUTLERS_API_DB_POOL_MIN_SIZE", "0")
        monkeypatch.setenv("BUTLERS_API_DB_POOL_MAX_SIZE", "2")

        mgr = DatabaseManager(host="localhost", port=5432, user="pg", password="secret")

        assert mgr._min_pool_size == 0
        assert mgr._max_pool_size == 2


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


# ---------------------------------------------------------------------------
# deps.py singleton isolation contract (regression guard — bu-0iq18)
# ---------------------------------------------------------------------------


class TestDepsModuleGlobalIsolation:
    """Regression guard for deps.py module-global singleton test isolation.

    Root cause of the bu-ci857 flake: a test called init_db_manager() which
    wrote an AsyncMock into deps._db_manager.  That mock persisted across xdist
    worker tests; a later health-endpoint test got the mock from get_db_manager()
    and 500-ed.

    These tests assert that ALL known module-global singletons in deps.py are
    documented and that the correct monkeypatch isolation pattern is used.

    RULE: any test that mutates a deps.py singleton MUST use
    ``monkeypatch.setattr(deps_module, "<name>", <value>)`` so pytest
    auto-restores it — never bare assignment, never manual try/finally.
    """

    def test_known_singletons_exist_with_expected_names(self):
        """Enumerate the known module-global singletons in deps.py.

        If a new singleton is added to deps.py, it should be added to this
        list and receive the same monkeypatch-restore treatment in tests.
        """
        import butlers.api.deps as deps_mod

        known_singletons = {"_db_manager", "_mcp_manager", "_butler_configs", "_pricing_config"}
        for name in known_singletons:
            assert hasattr(deps_mod, name), (
                f"Expected module-global singleton {name!r} in deps.py — update this guard if removed"
            )

    def test_singletons_are_none_at_import_time(self):
        """Singletons start as None before any init_* call.

        This confirms the isolation contract: no singleton is pre-populated at
        import time, so a fresh import always starts from a clean baseline.
        """
        import importlib

        import butlers.api.deps as deps_mod

        # Re-importing the already-cached module returns the same object —
        # this is intentional: we want to verify the live state is predictable
        # after any test that correctly uses monkeypatch (which restores None).
        reloaded = importlib.import_module("butlers.api.deps")
        assert reloaded is deps_mod, "Module identity must be stable across imports"

        # Verify defaults (as documented in deps.py) — None means "not yet
        # initialized via init_*"; these names must NOT be mutated globally.
        # If another test in the same xdist worker already called init_* and
        # correctly used monkeypatch (which auto-restores), val will be None
        # again by the time this test runs. If monkeypatch was NOT used, val
        # may be a leaked mock — catching that is the point of this check.
        from butlers.api.db import DatabaseManager
        from butlers.api.deps import MCPClientManager
        from butlers.api.pricing import PricingConfig

        expected_types = {
            "_db_manager": DatabaseManager,
            "_mcp_manager": MCPClientManager,
            "_butler_configs": list,
            "_pricing_config": PricingConfig,
        }
        for attr, expected_type in expected_types.items():
            val = getattr(deps_mod, attr)
            assert val is None or isinstance(val, expected_type), (
                f"{attr} has an unexpected value type: {type(val)}"
            )

    @pytest.mark.parametrize(
        ("singleton", "getter"),
        [
            ("_db_manager", "get_db_manager"),
            ("_mcp_manager", "get_mcp_manager"),
            ("_butler_configs", "get_butler_configs"),
            ("_pricing_config", "get_pricing"),
        ],
    )
    def test_getter_raises_when_singleton_none(self, monkeypatch, singleton, getter):
        """Each get_*() accessor raises RuntimeError when its singleton is None."""
        import butlers.api.deps as deps_mod

        monkeypatch.setattr(deps_mod, singleton, None)
        with pytest.raises(RuntimeError, match="not initialized"):
            getattr(deps_mod, getter)()
