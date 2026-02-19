"""End-to-end smoke tests verifying frontend-to-API connectivity.

These tests verify the contract between the Vite frontend dev server and the
FastAPI backend:

1. The /api/health endpoint returns {"status": "ok"} (simulating what the
   frontend proxy would forward to the backend).
2. The Vite proxy config targets the correct backend port (40200).
3. The frontend API client defaults to "/api" as its base URL, matching the
   proxy path prefix.
4. CORS headers allow the Vite dev server origin (localhost:40173).
"""

import re
from pathlib import Path

import httpx
import pytest

from butlers.api.app import create_app

pytestmark = pytest.mark.unit

# Root of the repo — resolved relative to this test file.
REPO_ROOT = Path(__file__).resolve().parents[2]
VITE_CONFIG_PATH = REPO_ROOT / "frontend" / "vite.config.ts"
CLIENT_TS_PATH = REPO_ROOT / "frontend" / "src" / "api" / "client.ts"


class TestHealthEndpointSmoke:
    """Simulate the frontend proxy hitting /api/health on the backend."""

    async def test_health_returns_ok_via_asgi(self):
        """The health endpoint returns 200 with {"status": "ok"}.

        This is the exact request path the Vite dev-server proxy would
        forward: GET /api/health → backend.
        """
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        body = response.json()
        assert body == {"status": "ok"}

    async def test_health_response_is_json_content_type(self):
        """The health endpoint returns application/json content type."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/health")

        assert "application/json" in response.headers.get("content-type", "")


class TestViteProxyConfig:
    """Verify the Vite proxy configuration matches the backend expectations."""

    def test_vite_config_exists(self):
        """vite.config.ts must exist in the frontend directory."""
        assert VITE_CONFIG_PATH.is_file(), f"Missing {VITE_CONFIG_PATH}"

    def test_proxy_targets_port_40200(self):
        """The /api proxy target must point to localhost:40200."""
        content = VITE_CONFIG_PATH.read_text()
        # Match the proxy target URL — expect http://localhost:40200
        match = re.search(r'target:\s*["\']([^"\']+)["\']', content)
        assert match, "Could not find proxy target in vite.config.ts"
        target_url = match.group(1)
        assert "localhost:40200" in target_url, (
            f"Proxy target should be localhost:40200, got {target_url}"
        )

    def test_proxy_path_prefix_is_api(self):
        """The proxy path prefix must be '/api'."""
        content = VITE_CONFIG_PATH.read_text()
        # The proxy key in the Vite config should be "/api"
        assert '"/api"' in content or "'/api'" in content, (
            "Vite proxy path prefix '/api' not found in vite.config.ts"
        )


class TestFrontendClientConfig:
    """Verify the frontend API client is wired to the correct base URL."""

    def test_client_ts_exists(self):
        """client.ts must exist in the frontend API directory."""
        assert CLIENT_TS_PATH.is_file(), f"Missing {CLIENT_TS_PATH}"

    def test_default_base_url_is_api(self):
        """The API client must default to '/api' as its base URL."""
        content = CLIENT_TS_PATH.read_text()
        # The fallback should be "/api" when VITE_API_URL is not set
        assert '"/api"' in content or "'/api'" in content, (
            "Default API base URL '/api' not found in client.ts"
        )

    def test_health_endpoint_path(self):
        """The getHealth function must call '/health' (relative to base)."""
        content = CLIENT_TS_PATH.read_text()
        # getHealth should fetch "/health"
        assert '"/health"' in content or "'/health'" in content, (
            "getHealth endpoint path '/health' not found in client.ts"
        )


class TestCORSForViteDevServer:
    """Verify CORS headers allow the Vite dev server origin."""

    async def test_cors_preflight_allows_vite_origin(self):
        """CORS preflight from http://localhost:40173 must be allowed."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/health",
                headers={
                    "origin": "http://localhost:40173",
                    "access-control-request-method": "GET",
                    "access-control-request-headers": "content-type",
                },
            )

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:40173"

    async def test_cors_allows_json_content_type_header(self):
        """CORS must allow the Content-Type header the frontend sends."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/health",
                headers={
                    "origin": "http://localhost:40173",
                    "access-control-request-method": "GET",
                    "access-control-request-headers": "content-type,accept",
                },
            )

        allowed_headers = response.headers.get("access-control-allow-headers", "")
        # FastAPI CORSMiddleware with allow_headers=["*"] returns the
        # requested headers back, so we check they're present.
        assert "content-type" in allowed_headers.lower(), (
            f"Content-Type not in allowed headers: {allowed_headers}"
        )

    async def test_cors_actual_get_includes_allow_origin(self):
        """An actual GET from the Vite origin must include the allow-origin header."""
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/api/health",
                headers={"origin": "http://localhost:40173"},
            )

        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:40173"
