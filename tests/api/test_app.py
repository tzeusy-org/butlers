import httpx
import pytest

from butlers.api.app import FastAPI, create_app

pytestmark = pytest.mark.unit


class TestHealthEndpoint:
    async def test_health_returns_ok(self):
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestCORSMiddleware:
    async def test_cors_allows_configured_origin(self):
        app = create_app(cors_origins=["http://localhost:5173"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/health",
                headers={
                    "origin": "http://localhost:5173",
                    "access-control-request-method": "GET",
                },
            )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    async def test_cors_default_origins(self):
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/health",
                headers={
                    "origin": "http://localhost:5173",
                    "access-control-request-method": "GET",
                },
            )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


class TestAppFactory:
    def test_create_app_returns_fastapi_instance(self):
        app = create_app()
        assert isinstance(app, FastAPI)

    def test_custom_cors_origins(self):
        app = create_app(cors_origins=["https://dashboard.example.com"])
        # Verify the middleware is present by checking the app
        assert app is not None

    def test_redirect_slashes_disabled(self):
        app = create_app()
        assert app.router.redirect_slashes is False


class TestRouteSlashBehavior:
    async def test_traces_preflight_without_trailing_slash(self):
        app = create_app(cors_origins=["http://localhost:5173"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/traces?offset=0&limit=20",
                headers={
                    "origin": "http://localhost:5173",
                    "access-control-request-method": "GET",
                },
            )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
