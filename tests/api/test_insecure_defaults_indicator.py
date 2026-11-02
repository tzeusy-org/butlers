"""Configuration diagnostics and separate actual API-pool role status."""

from __future__ import annotations

from unittest.mock import patch

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
# role_enforcement_disabled — DB role enforcement indicator (bu-zxxyo)
# ---------------------------------------------------------------------------


async def test_health_role_enforcement_disabled_true_when_db_manager_not_initialized():
    """When DatabaseManager is not initialized, role_enforcement_disabled defaults True."""
    app = _make_ready_app(api_key="")
    # Patch get_db_manager to raise RuntimeError (no DB initialized — test isolation)
    with patch("butlers.api.app.get_db_manager", side_effect=RuntimeError("not initialized")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["role_enforcement_disabled"] is True


async def test_health_role_enforcement_disabled_true_when_no_db_role():
    """When DB manager has no role configured (dev posture), role_enforcement_disabled=True."""
    app = _make_ready_app(api_key="")
    mgr = DatabaseManager()
    # No role is set on any pool — enforcement is disabled (default state).
    assert mgr.role_enforcement_disabled is True
    with patch("butlers.api.app.get_db_manager", return_value=mgr):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["role_enforcement_disabled"] is True


async def test_health_role_enforcement_disabled_false_when_role_active():
    """When DB manager reports role enforcement active, role_enforcement_disabled=False."""
    app = _make_ready_app(api_key="")
    mgr = DatabaseManager()
    mgr.set_role_enforcement_disabled(False)
    with patch("butlers.api.app.get_db_manager", return_value=mgr):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["security"]["role_enforcement_disabled"] is False


async def test_health_security_section_includes_role_enforcement_field():
    """The security section must include role_enforcement_disabled as a bool."""
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "role_enforcement_disabled" in body["security"]
    assert isinstance(body["security"]["role_enforcement_disabled"], bool)


@pytest.mark.parametrize("path", ["/api/health", "/health"])
async def test_both_health_paths_expose_role_enforcement_field(path):
    """Both /api/health and /health must include the role_enforcement_disabled field."""
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert "security" in body
    assert "role_enforcement_disabled" in body["security"]
    assert isinstance(body["security"]["role_enforcement_disabled"], bool)


def test_database_manager_role_enforcement_disabled_default():
    """DatabaseManager defaults to role_enforcement_disabled=True (conservative)."""
    mgr = DatabaseManager()
    assert mgr.role_enforcement_disabled is True


def test_database_manager_set_role_enforcement_disabled_false():
    """set_role_enforcement_disabled(False) clears the flag."""
    mgr = DatabaseManager()
    mgr.set_role_enforcement_disabled(False)
    assert mgr.role_enforcement_disabled is False


def test_database_manager_set_role_enforcement_disabled_true():
    """set_role_enforcement_disabled(True) keeps the flag set."""
    mgr = DatabaseManager()
    mgr.set_role_enforcement_disabled(False)
    mgr.set_role_enforcement_disabled(True)
    assert mgr.role_enforcement_disabled is True
