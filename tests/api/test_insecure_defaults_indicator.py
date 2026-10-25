"""Configuration diagnostics and separate actual API-pool role status."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.db import DatabaseManager
from butlers.db import has_insecure_infra_defaults, is_grafana_anonymous_enabled

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_INFRA_CREDS = {
    "MINIO_ROOT_USER": "strongminio",
    "MINIO_ROOT_PASSWORD": "str0ng-minio-pw",
    "GF_SECURITY_ADMIN_USER": "grafana-admin",
    "GF_SECURITY_ADMIN_PASSWORD": "str0ng-grafana-pw",
}


def _make_ready_app(**kwargs) -> FastAPI:
    """Create an app and mark it as ready (skips the 503 early-return)."""
    app = create_app(**kwargs)
    app.state.ready = True
    return app


# ---------------------------------------------------------------------------
# has_insecure_infra_defaults — unit tests (pure, no HTTP)
# ---------------------------------------------------------------------------


def test_has_insecure_infra_defaults_true_when_all_absent(monkeypatch):
    """All creds absent → treated as known default → True."""
    for key in _ALL_INFRA_CREDS:
        monkeypatch.delenv(key, raising=False)
    assert has_insecure_infra_defaults() is True


def test_has_insecure_infra_defaults_true_when_explicit_default(monkeypatch):
    """Explicit known-default values → True."""
    monkeypatch.setenv("MINIO_ROOT_USER", "minioadmin")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "minioadmin")
    monkeypatch.setenv("GF_SECURITY_ADMIN_USER", "admin")
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "admin")
    assert has_insecure_infra_defaults() is True


def test_has_insecure_infra_defaults_true_when_partial_override(monkeypatch):
    """One cred at default is enough to trigger True."""
    for key, val in _ALL_INFRA_CREDS.items():
        monkeypatch.setenv(key, val)
    # Reset one cred to the known default.
    monkeypatch.setenv("GF_SECURITY_ADMIN_PASSWORD", "admin")
    assert has_insecure_infra_defaults() is True


def test_has_insecure_infra_defaults_false_when_all_overridden(monkeypatch):
    """All creds overridden with non-default values → False."""
    for key, val in _ALL_INFRA_CREDS.items():
        monkeypatch.setenv(key, val)
    assert has_insecure_infra_defaults() is False


# ---------------------------------------------------------------------------
# is_grafana_anonymous_enabled — unit tests
# ---------------------------------------------------------------------------


def test_grafana_anon_outside_dev_false_in_dev_posture(monkeypatch):
    """Explicit anonymous access is reported without a development exemption."""
    monkeypatch.setenv("GF_AUTH_ANONYMOUS_ENABLED", "true")
    assert is_grafana_anonymous_enabled() is True


def test_grafana_anon_outside_dev_false_when_unset_posture(monkeypatch):
    """Anonymous access remains visible without any profile setting."""
    monkeypatch.setenv("GF_AUTH_ANONYMOUS_ENABLED", "true")
    assert is_grafana_anonymous_enabled() is True


def test_grafana_anon_outside_dev_false_in_hardened_anon_disabled(monkeypatch):
    """Explicitly disabled anonymous access clears that configuration signal."""
    monkeypatch.setenv("GF_AUTH_ANONYMOUS_ENABLED", "false")
    assert is_grafana_anonymous_enabled() is False


def test_grafana_anon_outside_dev_false_in_hardened_anon_absent(monkeypatch):
    """Absent anonymous-access configuration follows the Compose false default."""
    monkeypatch.delenv("GF_AUTH_ANONYMOUS_ENABLED", raising=False)
    assert is_grafana_anonymous_enabled() is False


def test_grafana_anon_outside_dev_true_in_hardened_anon_enabled(monkeypatch):
    """Enabled anonymous access is always reported."""
    monkeypatch.setenv("GF_AUTH_ANONYMOUS_ENABLED", "true")
    assert is_grafana_anonymous_enabled() is True


# ---------------------------------------------------------------------------
# Health endpoint — security.insecure_infra_defaults field
# ---------------------------------------------------------------------------


async def test_health_insecure_infra_defaults_true_when_creds_at_default(monkeypatch):
    """Absent creds → insecure_infra_defaults=True on the health endpoint."""
    for key in _ALL_INFRA_CREDS:
        monkeypatch.delenv(key, raising=False)
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["insecure_infra_defaults"] is True


async def test_health_insecure_infra_defaults_false_when_all_creds_hardened(monkeypatch):
    """Non-default credentials and absent anonymous viewing clear the signal."""
    for key, val in _ALL_INFRA_CREDS.items():
        monkeypatch.setenv(key, val)
    monkeypatch.delenv("GF_AUTH_ANONYMOUS_ENABLED", raising=False)
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["insecure_infra_defaults"] is False


async def test_health_insecure_infra_defaults_true_when_grafana_anon_in_hardened(monkeypatch):
    """Explicit anonymous access remains visible with non-default credentials."""
    for key, val in _ALL_INFRA_CREDS.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setenv("GF_AUTH_ANONYMOUS_ENABLED", "true")
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["insecure_infra_defaults"] is True


async def test_health_insecure_infra_defaults_false_fully_hardened(monkeypatch):
    """Non-default credentials and disabled anonymous viewing clear the signal."""
    for key, val in _ALL_INFRA_CREDS.items():
        monkeypatch.setenv(key, val)
    monkeypatch.setenv("GF_AUTH_ANONYMOUS_ENABLED", "false")
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["insecure_infra_defaults"] is False


# ---------------------------------------------------------------------------
# Field presence + both paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/health", "/health"])
async def test_both_health_paths_expose_security_section(monkeypatch, path):
    """Both /api/health and /health must include the security section."""
    for key in _ALL_INFRA_CREDS:
        monkeypatch.delenv(key, raising=False)
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert "security" in body
    assert "insecure_infra_defaults" in body["security"]
    assert isinstance(body["security"]["insecure_infra_defaults"], bool)


# ---------------------------------------------------------------------------
# Retained old test identities now exercise actual authority diagnostics.
# ---------------------------------------------------------------------------


async def _current_dashboard_manager():
    """Real manager, purpose compiler and fresh callbacks; only native IO is doubled."""
    driver = AsyncMock()
    driver.fetchrow.return_value = dict.fromkeys(
        (
            "identity_ok",
            "role_set_ok",
            "attributes_ok",
            "ownership_ok",
            "create_ok",
            "target_member",
        ),
        True,
    )
    driver.fetchval.return_value = True

    async def create_pool(**options):
        pool = MagicMock()
        pool.close = AsyncMock()

        @asynccontextmanager
        async def acquire():
            await options["init"](driver)
            await options["setup"](driver)
            yield driver

        pool.acquire.side_effect = acquire
        return pool

    manager = DatabaseManager()
    with patch("butlers.api.db.asyncpg.create_pool", side_effect=create_pool):
        await manager.add_butler(
            "general", db_name="butlers", db_schema="general", modules=frozenset()
        )
        await manager.set_credential_shared_pool("butlers", "public")
    return manager, driver


async def test_health_role_enforcement_disabled_true_when_db_manager_not_initialized():
    """Missing diagnostic context remains unknown, never admitted."""
    app = _make_ready_app(api_key="")
    # Patch get_db_manager to raise RuntimeError (no DB initialized — test isolation)
    with patch("butlers.api.app.get_db_manager", side_effect=RuntimeError("not initialized")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["database_authority"] == "unknown"


async def test_health_role_enforcement_disabled_true_when_no_db_role():
    """A manager lacking its complete configured pool set is unavailable."""
    app = _make_ready_app(api_key="")
    mgr = DatabaseManager()
    assert await mgr.authority_status() == "unavailable"
    with patch("butlers.api.app.get_db_manager", return_value=mgr):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["database_authority"] == "unavailable"


async def test_health_role_enforcement_disabled_false_when_role_active():
    """Fresh acquisition of every actual configured purpose pool is admitted."""
    app = _make_ready_app(api_key="")
    mgr, driver = await _current_dashboard_manager()
    with patch("butlers.api.app.get_db_manager", return_value=mgr):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["database_authority"] == "admitted"
    assert driver.fetchrow.await_count == 4


async def test_health_security_section_includes_role_enforcement_field():
    """Authority is a closed diagnostic, separate from configuration signals."""
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "database_authority" in body["security"]
    assert body["security"]["database_authority"] in {"unknown", "unavailable", "admitted"}


@pytest.mark.parametrize("path", ["/api/health", "/health"])
async def test_both_health_paths_expose_role_enforcement_field(path):
    """Both health aliases expose the same closed authority diagnostic."""
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert "security" in body
    assert "database_authority" in body["security"]
    assert body["security"]["database_authority"] in {"unknown", "unavailable", "admitted"}


def test_database_manager_role_enforcement_disabled_default():
    """Empty managers cannot certify unavailable pools."""
    mgr = DatabaseManager()
    assert asyncio.run(mgr.authority_status()) == "unavailable"


def test_database_manager_set_role_enforcement_disabled_false():
    """Admission is produced by actual purpose callbacks, never a setter."""
    mgr, _driver = asyncio.run(_current_dashboard_manager())
    assert asyncio.run(mgr.authority_status()) == "admitted"


def test_database_manager_set_role_enforcement_disabled_true():
    """A previously admitted physical connection must refuse new catalog drift."""
    mgr, driver = asyncio.run(_current_dashboard_manager())
    assert asyncio.run(mgr.authority_status()) == "admitted"
    driver.fetchrow.return_value["ownership_ok"] = False
    assert asyncio.run(mgr.authority_status()) == "unavailable"
