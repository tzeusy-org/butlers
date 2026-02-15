"""Tests for roster butler router auto-discovery.

Tests the discovery mechanism that scans roster/{butler}/api/router.py
files and dynamically loads them via importlib.
"""

import sys

import pytest
from fastapi import APIRouter

from butlers.api.router_discovery import _load_router_module, discover_butler_routers


class TestLoadRouterModule:
    """Test the _load_router_module helper."""

    def test_load_valid_module(self, tmp_path):
        """Can load a valid router module from a file path."""
        # Create a simple router.py
        router_file = tmp_path / "router.py"
        router_file.write_text(
            """
from fastapi import APIRouter

router = APIRouter(prefix="/api/test", tags=["test"])

@router.get("/hello")
async def hello():
    return {"message": "hello"}
"""
        )

        module = _load_router_module(router_file, "test_router_module")

        assert hasattr(module, "router")
        assert isinstance(module.router, APIRouter)
        assert module.router.prefix == "/api/test"
        assert "test_router_module" in sys.modules

        # Clean up
        del sys.modules["test_router_module"]

    def test_load_module_with_imports(self, tmp_path):
        """Can load a module that imports from butlers.api.models."""
        router_file = tmp_path / "router.py"
        router_file.write_text(
            """
from fastapi import APIRouter
from butlers.api.models import ApiResponse

router = APIRouter(prefix="/api/test", tags=["test"])

@router.get("/data", response_model=ApiResponse[dict])
async def get_data() -> ApiResponse[dict]:
    return ApiResponse[dict](data={"key": "value"})
"""
        )

        module = _load_router_module(router_file, "test_imports_module")

        assert hasattr(module, "router")
        assert isinstance(module.router, APIRouter)

        # Clean up
        del sys.modules["test_imports_module"]

    def test_load_invalid_path_raises(self, tmp_path):
        """Loading from a non-existent path raises FileNotFoundError."""
        router_file = tmp_path / "nonexistent.py"

        with pytest.raises(FileNotFoundError):
            _load_router_module(router_file, "test_invalid_module")

    def test_load_syntax_error_raises(self, tmp_path):
        """Loading a file with syntax errors raises SyntaxError."""
        router_file = tmp_path / "router.py"
        router_file.write_text("def broken syntax")

        with pytest.raises(SyntaxError):
            _load_router_module(router_file, "test_syntax_error_module")


class TestDiscoverButlerRouters:
    """Test the discover_butler_routers function."""

    def test_discover_valid_butler_router(self, tmp_path):
        """Discovers and loads a valid butler router."""
        # Create roster structure
        roster_dir = tmp_path / "roster"
        butler_dir = roster_dir / "test-butler"
        api_dir = butler_dir / "api"
        api_dir.mkdir(parents=True)

        router_file = api_dir / "router.py"
        router_file.write_text(
            """
from fastapi import APIRouter

router = APIRouter(prefix="/api/test-butler", tags=["test-butler"])

@router.get("/status")
async def get_status():
    return {"status": "ok"}
"""
        )

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 1
        butler_name, module = routers[0]
        assert butler_name == "test-butler"
        assert hasattr(module, "router")
        assert isinstance(module.router, APIRouter)
        assert module.router.prefix == "/api/test-butler"

        # Clean up
        del sys.modules["test-butler_api_router"]

    def test_discover_multiple_butler_routers(self, tmp_path):
        """Discovers multiple butler routers in sorted order."""
        roster_dir = tmp_path / "roster"

        for butler_name in ["zebra", "alpha", "beta"]:
            api_dir = roster_dir / butler_name / "api"
            api_dir.mkdir(parents=True)
            router_file = api_dir / "router.py"
            router_file.write_text(
                f"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/{butler_name}", tags=["{butler_name}"])
"""
            )

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 3
        # Should be sorted alphabetically
        assert routers[0][0] == "alpha"
        assert routers[1][0] == "beta"
        assert routers[2][0] == "zebra"

        # Clean up
        for name in ["alpha", "beta", "zebra"]:
            del sys.modules[f"{name}_api_router"]

    def test_skip_butler_without_api_directory(self, tmp_path):
        """Silently skips butlers without api/ directories."""
        roster_dir = tmp_path / "roster"

        # Create butler with api/router.py
        butler_with_api = roster_dir / "with-api" / "api"
        butler_with_api.mkdir(parents=True)
        (butler_with_api / "router.py").write_text(
            """
from fastapi import APIRouter
router = APIRouter(prefix="/api/with-api", tags=["with-api"])
"""
        )

        # Create butler without api/ directory
        butler_without_api = roster_dir / "without-api"
        butler_without_api.mkdir(parents=True)
        (butler_without_api / "butler.toml").write_text("name = 'without-api'")

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 1
        assert routers[0][0] == "with-api"

        # Clean up
        del sys.modules["with-api_api_router"]

    def test_skip_butler_with_invalid_router_export(self, tmp_path, caplog):
        """Logs warning and skips butler with invalid router export."""
        roster_dir = tmp_path / "roster"

        # Butler with no 'router' variable
        api_dir = roster_dir / "no-router" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "router.py").write_text("# No router variable")

        # Butler with wrong type
        api_dir2 = roster_dir / "wrong-type" / "api"
        api_dir2.mkdir(parents=True)
        (api_dir2 / "router.py").write_text("router = 'not an APIRouter'")

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 0
        assert "does not export 'router' variable" in caplog.text
        assert "not an APIRouter instance" in caplog.text

        # Clean up
        if "no-router_api_router" in sys.modules:
            del sys.modules["no-router_api_router"]
        if "wrong-type_api_router" in sys.modules:
            del sys.modules["wrong-type_api_router"]

    def test_skip_butler_with_load_error(self, tmp_path, caplog):
        """Logs warning and skips butler that fails to load."""
        roster_dir = tmp_path / "roster"

        api_dir = roster_dir / "broken" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "router.py").write_text("raise RuntimeError('intentional error')")

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 0
        assert "Failed to load router module for butler 'broken'" in caplog.text

        # Clean up
        if "broken_api_router" in sys.modules:
            del sys.modules["broken_api_router"]

    def test_nonexistent_roster_directory(self, tmp_path, caplog):
        """Handles non-existent roster directory gracefully."""
        roster_dir = tmp_path / "nonexistent"

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 0
        assert "Roster directory not found" in caplog.text

    def test_empty_roster_directory(self, tmp_path):
        """Handles empty roster directory gracefully."""
        roster_dir = tmp_path / "roster"
        roster_dir.mkdir()

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 0

    def test_skip_files_in_roster_root(self, tmp_path):
        """Skips regular files in roster root (only processes directories)."""
        roster_dir = tmp_path / "roster"
        roster_dir.mkdir()

        # Create a file in roster root
        (roster_dir / "README.md").write_text("# Roster")

        # Create a valid butler
        api_dir = roster_dir / "valid" / "api"
        api_dir.mkdir(parents=True)
        (api_dir / "router.py").write_text(
            """
from fastapi import APIRouter
router = APIRouter(prefix="/api/valid", tags=["valid"])
"""
        )

        routers = discover_butler_routers(roster_dir)

        assert len(routers) == 1
        assert routers[0][0] == "valid"

        # Clean up
        del sys.modules["valid_api_router"]


class TestIntegrationWithRealRoster:
    """Integration tests using the actual roster/health butler."""

    def test_discover_health_butler_router(self):
        """Can discover and load the real health butler router."""
        # Use default roster directory
        routers = discover_butler_routers()

        # Should find at least the health butler
        butler_names = [name for name, _ in routers]
        assert "health" in butler_names

        # Get the health router
        health_router = next(module for name, module in routers if name == "health")
        assert hasattr(health_router, "router")
        assert isinstance(health_router.router, APIRouter)
        assert health_router.router.prefix == "/api/health-butler"

    def test_health_router_imports_shared_models(self):
        """Health router successfully imports ApiResponse from shared models."""
        routers = discover_butler_routers()
        health_router = next((module for name, module in routers if name == "health"), None)

        assert health_router is not None

        # The router module should have imported ApiResponse
        # We can't easily check the import, but we can verify the router works
        assert hasattr(health_router.router, "routes")
        assert len(health_router.router.routes) > 0
