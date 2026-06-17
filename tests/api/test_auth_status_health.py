"""Tests for the auth-status security-posture fields on the health endpoint.

The health endpoints (/api/health, /health) report two boolean security-posture
indicators:

  api_key_auth_enabled
    True  when DASHBOARD_API_KEY is set (ApiKeyMiddleware is active).
    False when DASHBOARD_API_KEY is absent (network-level boundary only).

  export_secret_insecure_default
    True  when DASHBOARD_EXPORT_SECRET is absent.  In dev the export signer
          falls back to a known constant; in production it refuses entirely.
          Either way the posture is insecure.
    False when DASHBOARD_EXPORT_SECRET is explicitly set (a real secret).

Invariants enforced here:
  - The payload is booleans ONLY — no secret values, no key material.
  - Both fields are present in every successful (200) health response.
  - Both /api/health and /health paths expose the fields.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ready_app(**kwargs) -> FastAPI:
    """Create an app and mark it as ready (skips the 503 early-return)."""
    app = create_app(**kwargs)
    app.state.ready = True
    return app


# ---------------------------------------------------------------------------
# api_key_auth_enabled
# ---------------------------------------------------------------------------


async def test_api_key_auth_enabled_when_key_set():
    """When api_key is set, health must report api_key_auth_enabled=True."""
    app = _make_ready_app(api_key="some-secret-key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth"]["api_key_auth_enabled"] is True


async def test_api_key_auth_disabled_when_key_unset():
    """When api_key is '' (force-disabled), health must report api_key_auth_enabled=False."""
    app = _make_ready_app(api_key="")  # '' → force-disable
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth"]["api_key_auth_enabled"] is False


# ---------------------------------------------------------------------------
# export_secret_insecure_default
# ---------------------------------------------------------------------------


async def test_export_secret_insecure_default_when_secret_absent(monkeypatch):
    """When DASHBOARD_EXPORT_SECRET is absent, export_secret_insecure_default must be True."""
    monkeypatch.delenv("DASHBOARD_EXPORT_SECRET", raising=False)
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth"]["export_secret_insecure_default"] is True


async def test_export_secret_insecure_default_false_when_secret_set(monkeypatch):
    """When DASHBOARD_EXPORT_SECRET is set, export_secret_insecure_default must be False."""
    monkeypatch.setenv("DASHBOARD_EXPORT_SECRET", "a-real-strong-secret-value")
    app = _make_ready_app(api_key="")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["auth"]["export_secret_insecure_default"] is False


# ---------------------------------------------------------------------------
# No secret material in payload
# ---------------------------------------------------------------------------


async def test_health_payload_contains_no_secret_material(monkeypatch):
    """The health payload must never contain the literal secret values.

    This is a belt-and-suspenders assertion: we set a recognisable test key
    and verify it does not appear anywhere in the serialised response body.
    The fields are booleans only.
    """
    secret_key = "CANARY_API_KEY_DO_NOT_LEAK"
    secret_export = "CANARY_EXPORT_SECRET_DO_NOT_LEAK"
    monkeypatch.setenv("DASHBOARD_EXPORT_SECRET", secret_export)

    app = _make_ready_app(api_key=secret_key)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/health", headers={"X-API-Key": secret_key})

    assert resp.status_code == 200
    response_text = resp.text
    assert secret_key not in response_text, "API key leaked into health response"
    assert secret_export not in response_text, "Export secret leaked into health response"


# ---------------------------------------------------------------------------
# Both paths expose the auth fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/health", "/health"])
async def test_both_health_paths_expose_auth_fields(monkeypatch, path):
    """Both /api/health and /health must include the auth posture object."""
    monkeypatch.delenv("DASHBOARD_EXPORT_SECRET", raising=False)
    app = _make_ready_app(api_key="test-key")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    body = resp.json()
    assert "auth" in body
    assert "api_key_auth_enabled" in body["auth"]
    assert "export_secret_insecure_default" in body["auth"]
    # Values must be booleans
    assert isinstance(body["auth"]["api_key_auth_enabled"], bool)
    assert isinstance(body["auth"]["export_secret_insecure_default"], bool)
