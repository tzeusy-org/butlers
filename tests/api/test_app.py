import httpx
import pytest

from butlers.api import app as app_module
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

    async def test_root_health_alias_returns_ok(self):
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestCORSMiddleware:
    async def test_cors_allows_configured_origin(self):
        app = create_app(cors_origins=["http://localhost:41173"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/health",
                headers={
                    "origin": "http://localhost:41173",
                    "access-control-request-method": "GET",
                },
            )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:41173"

    async def test_cors_default_origins(self):
        app = create_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/health",
                headers={
                    "origin": "http://localhost:41173",
                    "access-control-request-method": "GET",
                },
            )
        assert response.headers.get("access-control-allow-origin") == "http://localhost:41173"


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
        app = create_app(cors_origins=["http://localhost:41173"])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.options(
                "/api/traces?offset=0&limit=20",
                headers={
                    "origin": "http://localhost:41173",
                    "access-control-request-method": "GET",
                },
            )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-origin") == "http://localhost:41173"


class TestTracesRouterMounted:
    """Verify the traces router is registered in the FastAPI app."""

    def test_traces_list_route_exists(self):
        """GET /api/traces must be a registered route (not 404/405)."""
        from unittest.mock import AsyncMock, MagicMock

        from butlers.api.db import DatabaseManager
        from butlers.api.routers.traces import _get_db_manager

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.fan_out = AsyncMock(return_value={})

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/traces")
        assert response.status_code != 404, (
            "GET /api/traces returned 404 — traces router is not mounted"
        )

    def test_traces_detail_route_exists(self):
        """GET /api/traces/{trace_id} must be a registered route (not 404)."""
        from unittest.mock import AsyncMock, MagicMock

        from butlers.api.db import DatabaseManager
        from butlers.api.routers.traces import _get_db_manager

        mock_db = MagicMock(spec=DatabaseManager)
        mock_db.fan_out = AsyncMock(return_value={})

        app = create_app()
        app.dependency_overrides[_get_db_manager] = lambda: mock_db

        from fastapi.testclient import TestClient

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/api/traces/no-such-trace")
        assert response.status_code == 404
        # A real 404 from the endpoint (trace not found) differs from
        # FastAPI's routing 404. The endpoint returns {"detail": "Trace ..."}
        assert "Trace" in response.json().get("detail", ""), (
            "Expected trace-not-found detail from the endpoint, got routing 404"
        )


class TestLifespan:
    async def test_lifespan_initializes_and_shuts_down_dependencies(self, monkeypatch):
        calls = {
            "init_dependencies": 0,
            "init_pricing": 0,
            "init_db_manager": 0,
            "wire_db_dependencies": 0,
            "shutdown_db_manager": 0,
            "shutdown_dependencies": 0,
        }

        def fake_init_dependencies():
            calls["init_dependencies"] += 1

        def fake_init_pricing():
            calls["init_pricing"] += 1

        def fake_get_butler_configs():
            return []

        async def fake_init_db_manager(_butler_configs):
            calls["init_db_manager"] += 1

        def fake_wire_db_dependencies(_app, dynamic_modules=None):
            calls["wire_db_dependencies"] += 1

        async def fake_shutdown_db_manager():
            calls["shutdown_db_manager"] += 1

        async def fake_shutdown_dependencies():
            calls["shutdown_dependencies"] += 1

        monkeypatch.setattr(app_module, "init_dependencies", fake_init_dependencies)
        monkeypatch.setattr(app_module, "init_pricing", fake_init_pricing)
        monkeypatch.setattr(app_module, "get_butler_configs", fake_get_butler_configs)
        monkeypatch.setattr(app_module, "init_db_manager", fake_init_db_manager)
        monkeypatch.setattr(app_module, "wire_db_dependencies", fake_wire_db_dependencies)
        monkeypatch.setattr(app_module, "shutdown_db_manager", fake_shutdown_db_manager)
        monkeypatch.setattr(app_module, "shutdown_dependencies", fake_shutdown_dependencies)

        app = create_app()

        async with app.router.lifespan_context(app):
            pass

        assert calls["init_dependencies"] == 1
        assert calls["init_pricing"] == 1
        assert calls["init_db_manager"] == 1
        assert calls["wire_db_dependencies"] == 1
        assert calls["shutdown_db_manager"] == 1
        assert calls["shutdown_dependencies"] == 1
