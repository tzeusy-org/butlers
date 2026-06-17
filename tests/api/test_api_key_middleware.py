"""Lock-in tests for opt-in API-key authentication (ApiKeyMiddleware).

Security doctrine: network isolation (localhost + Tailscale) is the PRIMARY
trust boundary — see about/heart-and-soul/security.md.  ApiKeyMiddleware is
opt-in hardening, NOT fail-closed.  When DASHBOARD_API_KEY is absent the
middleware MUST be a complete no-op.

The four acceptance cases this file locks in:
  1. Key set, bad or missing header  → 401 with UNAUTHORIZED envelope
  2. Key set, correct header          → pass-through (200 from a known dummy route)
  3. Health endpoints                 → always bypass auth (both /health paths)
  4. Key unset                        → no-op, all requests pass through
"""

from __future__ import annotations

import httpx
import pytest

from butlers.api.app import create_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. Key set: bad or missing header → 401
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},  # completely absent header
        {"X-API-Key": "wrong-value"},  # header present but wrong value
        {"X-API-Key": ""},  # header present but empty string
    ],
    ids=["missing-header", "wrong-key", "empty-key"],
)
async def test_bad_or_missing_key_returns_401(headers):
    """When DASHBOARD_API_KEY is configured and the request supplies a bad
    or missing X-API-Key, the middleware must respond 401 with the standard
    UNAUTHORIZED error envelope — regardless of which bad form is presented."""
    app = create_app(api_key="super-secret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/butlers", headers=headers)
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# 2. Key set: correct header → pass-through (not a 401)
# ---------------------------------------------------------------------------


async def test_correct_key_passes_through():
    """When DASHBOARD_API_KEY is configured and the exact key is supplied in
    X-API-Key, the middleware must not block the request.  The downstream
    handler decides the final status code (200 / 404 / 503 etc.) — this test
    asserts only that auth does not reject it."""
    app = create_app(api_key="super-secret")

    @app.get("/api/test-auth-pass")
    async def dummy_route():
        return {"status": "passed"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/test-auth-pass", headers={"X-API-Key": "super-secret"})
    assert resp.status_code == 200
    assert resp.json() == {"status": "passed"}


# ---------------------------------------------------------------------------
# 3. Health endpoints always bypass auth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/api/health", "/health"])
async def test_health_endpoints_bypass_auth(path):
    """Liveness/readiness probe endpoints must be reachable without any
    X-API-Key header even when DASHBOARD_API_KEY is configured.
    Covers both /api/health and /health (both are registered as public)."""
    app = create_app(api_key="super-secret")
    app.state.ready = True  # simulate completed lifespan startup so health returns 200
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)  # intentionally no X-API-Key
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. Key unset → no-op; all requests pass through
# ---------------------------------------------------------------------------


async def test_key_unset_is_noop():
    """When DASHBOARD_API_KEY is not set (api_key='' → force-disabled in
    create_app), the middleware is a complete no-op.  Protected endpoints
    must be reachable without any header — the network boundary is the only
    gate in this configuration."""
    app = create_app(api_key="")  # '' → force-disable auth in create_app()

    @app.get("/api/test-auth-noop")
    async def dummy_route():
        return {"status": "passed"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/test-auth-noop")  # no X-API-Key, no env key
    assert resp.status_code == 200
    assert resp.json() == {"status": "passed"}
