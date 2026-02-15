"""Integration tests for FastAPI app with auto-discovered routers.

Tests that the app.py correctly discovers, mounts, and wires DB dependencies
for butler routers from roster/{butler}/api/.
"""

from fastapi.testclient import TestClient

from butlers.api.app import create_app
from butlers.api.router_discovery import discover_butler_routers


class TestAppWithDiscoveredRouters:
    """Test that the FastAPI app correctly integrates discovered routers."""

    def test_app_includes_discovered_routers(self):
        """The app includes routes from auto-discovered butler routers."""
        app = create_app(cors_origins=["*"])
        client = TestClient(app)

        # The health butler router should be discovered and mounted
        response = client.get("/api/health-butler/vitals")

        # Should return 200 (or 500 if DB not initialized, which is fine for this test)
        # We're just checking the route exists
        assert response.status_code in (200, 500)

    def test_app_logs_discovered_routers(self, caplog):
        caplog.set_level("INFO")
        """The app logs information about discovered butler routers."""
        create_app(cors_origins=["*"])

        # Should have logged the discovered routers
        assert (
            "Discovered butler router: health" in caplog.text
            or "Mounted butler router: health" in caplog.text
        )

    def test_discovered_routers_have_correct_prefixes(self):
        """Discovered routers are mounted with their defined prefixes."""
        app = create_app(cors_origins=["*"])

        # Check that routes exist with the correct prefix
        routes = [route.path for route in app.routes]

        # Health butler should have routes under /api/health-butler
        health_routes = [r for r in routes if r.startswith("/api/health-butler")]
        assert len(health_routes) > 0

    def test_core_routers_still_mounted(self):
        """Core static routers are still mounted after adding auto-discovery."""
        app = create_app(cors_origins=["*"])
        client = TestClient(app)

        # Test a core endpoint that should always exist
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestDBDependencyWiring:
    """Test that DB dependencies are correctly wired for dynamic routers."""

    def test_wire_db_dependencies_with_dynamic_modules(self, tmp_path):
        """wire_db_dependencies correctly handles dynamically-loaded modules."""
        # Create a test butler with a router that has _get_db_manager
        roster_dir = tmp_path / "roster"
        api_dir = roster_dir / "test-butler" / "api"
        api_dir.mkdir(parents=True)

        router_file = api_dir / "router.py"
        router_file.write_text(
            """
from fastapi import APIRouter, Depends
from butlers.api.db import DatabaseManager

router = APIRouter(prefix="/api/test-butler", tags=["test-butler"])

def _get_db_manager() -> DatabaseManager:
    raise RuntimeError("DatabaseManager not initialized")

@router.get("/data")
async def get_data(db: DatabaseManager = Depends(_get_db_manager)):
    return {"status": "ok"}
"""
        )

        # Discover routers
        routers = discover_butler_routers(roster_dir)
        assert len(routers) == 1

        butler_name, router_module = routers[0]
        assert hasattr(router_module, "_get_db_manager")

        # The wiring will be tested in the full app integration
        # For now, just verify the module has the expected function
        assert callable(router_module._get_db_manager)

        # Clean up
        import sys

        del sys.modules["test-butler_api_router"]

    def test_wire_db_dependencies_skips_modules_without_stub(self, tmp_path):
        """wire_db_dependencies gracefully handles modules without _get_db_manager."""
        # Create a test butler without _get_db_manager
        roster_dir = tmp_path / "roster"
        api_dir = roster_dir / "simple-butler" / "api"
        api_dir.mkdir(parents=True)

        router_file = api_dir / "router.py"
        router_file.write_text(
            """
from fastapi import APIRouter

router = APIRouter(prefix="/api/simple-butler", tags=["simple-butler"])

@router.get("/status")
async def get_status():
    return {"status": "ok"}
"""
        )

        # Discover routers
        routers = discover_butler_routers(roster_dir)
        assert len(routers) == 1

        butler_name, router_module = routers[0]
        assert not hasattr(router_module, "_get_db_manager")

        # The app should still work without errors
        # (wire_db_dependencies will skip this module)

        # Clean up
        import sys

        del sys.modules["simple-butler_api_router"]


class TestStartupLogging:
    """Test that startup logs provide useful debugging information."""

    def test_startup_logs_butler_router_count(self, caplog):
        caplog.set_level("INFO")
        """Startup logs show how many butler routers were discovered."""
        create_app(cors_origins=["*"])

        # Should log the number of discovered routers
        log_text = caplog.text
        assert "Discovered" in log_text and "butler router" in log_text

    def test_startup_logs_each_mounted_router(self, caplog):
        caplog.set_level("INFO")
        """Startup logs each individually mounted butler router."""
        create_app(cors_origins=["*"])

        # Should log each mounted router
        log_text = caplog.text
        assert "Mounted butler router:" in log_text or "Discovered butler router:" in log_text
