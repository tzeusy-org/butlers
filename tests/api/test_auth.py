"""Tests for dashboard API authentication middleware.

Verifies that ``ApiKeyMiddleware`` enforces ``X-API-Key`` on all /api/*
routes when ``DASHBOARD_API_KEY`` is configured, while allowing:
- Requests without a key when the env var is absent (opt-in auth)
- Health endpoints regardless of key configuration
- Non-/api/ paths (frontend static assets) when auth is enabled

Issue: bu-q9k1
"""

from __future__ import annotations

import httpx
import pytest

from butlers.api.app import create_app

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_KEY = "test-secret-key-abc123"


def _app_with_auth(api_key: str | None = _VALID_KEY):
    """Create a test app with ApiKeyMiddleware configured."""
    return create_app(api_key=api_key)


def _app_without_auth():
    """Create a test app with auth explicitly disabled (empty string)."""
    return create_app(api_key="")


async def _get(app, path: str, headers: dict | None = None) -> httpx.Response:
    """Issue a GET against the test app."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.get(path, headers=headers or {})


async def _options(app, path: str, headers: dict | None = None) -> httpx.Response:
    """Issue an OPTIONS (CORS preflight) against the test app."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        return await client.options(path, headers=headers or {})


# ---------------------------------------------------------------------------
# Auth disabled (no DASHBOARD_API_KEY)
# ---------------------------------------------------------------------------


class TestAuthDisabled:
    """When no API key is configured, all requests pass through."""

    async def test_health_accessible_without_key(self):
        app = _app_without_auth()
        resp = await _get(app, "/api/health")
        assert resp.status_code == 200

    async def test_api_route_accessible_without_key(self):
        """Any /api/* route is reachable when auth is disabled."""
        app = _app_without_auth()
        # /api/issues exists but returns 500/503 without a real DB — we just
        # care that auth doesn't reject the request (not a 401).
        resp = await _get(app, "/api/issues")
        assert resp.status_code != 401

    async def test_api_route_accessible_with_key_when_auth_disabled(self):
        """Supplying a key when auth is off doesn't break anything."""
        app = _app_without_auth()
        resp = await _get(app, "/api/issues", headers={"X-API-Key": "any-key"})
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# Auth enabled — invalid / missing key
# ---------------------------------------------------------------------------


class TestAuthEnabled_MissingKey:
    async def test_missing_key_returns_401(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues")
        assert resp.status_code == 401

    async def test_missing_key_returns_error_envelope(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues")
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == "UNAUTHORIZED"

    async def test_missing_key_error_message_is_helpful(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues")
        body = resp.json()
        assert "X-API-Key" in body["error"]["message"]

    async def test_wrong_key_returns_401(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    async def test_wrong_key_returns_unauthorized_code(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues", headers={"X-API-Key": "wrong-key"})
        body = resp.json()
        assert body["error"]["code"] == "UNAUTHORIZED"

    async def test_empty_key_header_returns_401(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues", headers={"X-API-Key": ""})
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth enabled — valid key
# ---------------------------------------------------------------------------


class TestAuthEnabled_ValidKey:
    async def test_valid_key_passes_through(self):
        """A valid key must not return 401 (may return other codes depending on DB)."""
        app = _app_with_auth()
        resp = await _get(app, "/api/issues", headers={"X-API-Key": _VALID_KEY})
        assert resp.status_code != 401

    async def test_valid_key_on_butlers_endpoint(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/butlers", headers={"X-API-Key": _VALID_KEY})
        assert resp.status_code != 401

    async def test_valid_key_on_costs_endpoint(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/costs", headers={"X-API-Key": _VALID_KEY})
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# Health endpoints are always public
# ---------------------------------------------------------------------------


class TestPublicHealthEndpoints:
    async def test_api_health_always_public_with_auth_enabled(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_root_health_always_public_with_auth_enabled(self):
        app = _app_with_auth()
        resp = await _get(app, "/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_api_health_public_even_with_wrong_key(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/health", headers={"X-API-Key": "bad-key"})
        assert resp.status_code == 200

    async def test_root_health_public_even_with_no_key(self):
        app = _app_with_auth()
        resp = await _get(app, "/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# ApiKeyMiddleware unit tests (testing the class directly)
# ---------------------------------------------------------------------------


class TestApiKeyMiddlewareClass:
    def test_middleware_disabled_when_no_key(self):
        """When api_key=None, middleware is inactive (no env fallback in middleware itself)."""
        from starlette.applications import Starlette

        from butlers.api.middleware import ApiKeyMiddleware

        inner_app = Starlette()
        mw = ApiKeyMiddleware(inner_app, api_key=None)
        assert mw._api_key is None

    def test_middleware_enabled_when_key_provided(self):
        from starlette.applications import Starlette

        from butlers.api.middleware import ApiKeyMiddleware

        inner_app = Starlette()
        mw = ApiKeyMiddleware(inner_app, api_key="my-key")
        assert mw._api_key == "my-key"

    def test_middleware_empty_string_treated_as_disabled(self):
        """Empty string key is treated as disabled (falsy)."""
        from starlette.applications import Starlette

        from butlers.api.middleware import ApiKeyMiddleware

        inner_app = Starlette()
        mw = ApiKeyMiddleware(inner_app, api_key="")
        assert mw._api_key is None


# ---------------------------------------------------------------------------
# Env-var-driven auth via create_app (integration-style)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CORS preflight (OPTIONS) must pass through auth middleware
# ---------------------------------------------------------------------------


class TestCorsPreflightPassthrough:
    """CORS preflight OPTIONS requests must not be blocked by ApiKeyMiddleware.

    Starlette adds middleware in reverse: CORSMiddleware (added first) ends up
    *inside* ApiKeyMiddleware.  Without an explicit OPTIONS exemption, preflight
    requests to /api/* would receive 401 before the browser sees any CORS header.
    """

    _CORS_HEADERS = {
        "Origin": "http://localhost:40173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "X-API-Key",
    }

    async def test_cors_preflight_passes_when_auth_enabled(self):
        """OPTIONS /api/* must not return 401 even when auth is configured."""
        app = _app_with_auth()
        resp = await _options(app, "/api/issues", headers=self._CORS_HEADERS)
        assert resp.status_code != 401

    async def test_cors_preflight_passes_without_api_key_header(self):
        """CORS preflight must succeed without X-API-Key in request headers."""
        app = _app_with_auth()
        resp = await _options(
            app,
            "/api/issues",
            headers={"Origin": "http://localhost:40173", "Access-Control-Request-Method": "GET"},
        )
        assert resp.status_code != 401

    async def test_cors_preflight_passes_when_auth_disabled(self):
        """OPTIONS requests also pass through when auth is off (baseline check)."""
        app = _app_without_auth()
        resp = await _options(app, "/api/issues", headers=self._CORS_HEADERS)
        assert resp.status_code != 401


# ---------------------------------------------------------------------------
# 401 response headers
# ---------------------------------------------------------------------------


class TestUnauthorizedResponseHeaders:
    """The 401 response must include WWW-Authenticate per RFC 7235 sec. 3.1."""

    async def test_401_includes_www_authenticate_header(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues")
        assert resp.status_code == 401
        assert "www-authenticate" in resp.headers

    async def test_www_authenticate_references_apikey_scheme(self):
        app = _app_with_auth()
        resp = await _get(app, "/api/issues")
        www_auth = resp.headers.get("www-authenticate", "")
        assert "ApiKey" in www_auth


# ---------------------------------------------------------------------------
# Env-var-driven auth via create_app (integration-style)
# ---------------------------------------------------------------------------


class TestEnvVarDrivenAuth:
    async def test_env_var_enables_auth(self, monkeypatch):
        """Setting DASHBOARD_API_KEY in environment enables auth in create_app."""
        monkeypatch.setenv("DASHBOARD_API_KEY", "env-driven-key")
        # api_key=None → reads from env
        app = create_app(api_key=None)
        resp = await _get(app, "/api/issues")
        assert resp.status_code == 401

    async def test_env_var_valid_key_passes(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_API_KEY", "env-driven-key")
        app = create_app(api_key=None)
        resp = await _get(app, "/api/issues", headers={"X-API-Key": "env-driven-key"})
        assert resp.status_code != 401

    async def test_explicit_empty_disables_auth_even_with_env_var(self, monkeypatch):
        """api_key='' must override the env var and disable auth."""
        monkeypatch.setenv("DASHBOARD_API_KEY", "env-driven-key")
        app = create_app(api_key="")
        # No key in request — should NOT return 401 because auth is disabled
        resp = await _get(app, "/api/issues")
        assert resp.status_code != 401
