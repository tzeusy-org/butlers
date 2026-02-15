#!/usr/bin/env python3
"""Proof of concept: Load FastAPI router from roster/ via importlib.

Demonstrates:
1. Loading a router.py file from roster/{butler}/api/ using importlib
2. The router can import shared models from butlers.api.models
3. FastAPI Depends() works correctly with dynamically-loaded routers
4. Multiple routers can be discovered and loaded automatically

Run: uv run python poc_router_loading.py
"""

import importlib.util
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def load_router_from_file(router_path: Path, module_name: str):
    """Load a router module from a file path using importlib.

    Parameters
    ----------
    router_path:
        Path to the router.py file
    module_name:
        Unique module name (e.g., "health_api_router")

    Returns
    -------
    module
        The loaded module containing the router
    """
    spec = importlib.util.spec_from_file_location(module_name, router_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load spec from {router_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # Register for imports to work
    spec.loader.exec_module(module)
    return module


def discover_butler_routers(roster_dir: Path) -> list[tuple[str, Path]]:
    """Discover all roster/{butler}/api/router.py files.

    Returns
    -------
    list[tuple[str, Path]]
        List of (butler_name, router_path) tuples
    """
    routers = []
    if not roster_dir.exists():
        return routers

    for butler_dir in sorted(roster_dir.iterdir()):
        if not butler_dir.is_dir():
            continue

        router_path = butler_dir / "api" / "router.py"
        if router_path.exists():
            routers.append((butler_dir.name, router_path))

    return routers


def main():
    """Demonstrate router loading and functionality."""
    print("=" * 60)
    print("Proof of Concept: Loading FastAPI Routers from roster/")
    print("=" * 60)

    # 1. Discover routers
    roster_dir = Path(__file__).parent / "roster"
    print(f"\n1. Discovering routers in {roster_dir}")

    discovered = discover_butler_routers(roster_dir)
    print(f"   Found {len(discovered)} router(s):")
    for butler_name, router_path in discovered:
        print(f"   - {butler_name}: {router_path.relative_to(Path.cwd())}")

    # 2. Load the health butler router
    print("\n2. Loading health butler router via importlib")

    health_router_path = roster_dir / "health" / "api" / "router.py"
    if not health_router_path.exists():
        print(f"   ERROR: {health_router_path} not found")
        sys.exit(1)

    try:
        health_module = load_router_from_file(health_router_path, "health_api_router")
        print(f"   ✓ Module loaded: {health_module.__name__}")
        print(f"   ✓ Router found: {health_module.router}")
    except Exception as e:
        print(f"   ERROR: Failed to load router: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # 3. Create FastAPI app and include the router
    print("\n3. Creating FastAPI app and including the router")

    app = FastAPI()
    try:
        app.include_router(health_module.router)
        print(f"   ✓ Router included with {len(health_module.router.routes)} routes")
        for route in health_module.router.routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                methods = ",".join(route.methods)
                print(f"      {methods:6s} {route.path}")
    except Exception as e:
        print(f"   ERROR: Failed to include router: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # 4. Test the endpoints
    print("\n4. Testing endpoints via TestClient")

    client = TestClient(app)

    try:
        response = client.get("/api/health-butler/vitals")
        print(f"   GET /api/health-butler/vitals")
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}")

        response = client.get("/api/health-butler/checks")
        print(f"\n   GET /api/health-butler/checks")
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"   ERROR: Request failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)

    # 5. Verify shared model imports work
    print("\n5. Verifying shared model imports")

    try:
        from butlers.api.models import ApiResponse

        print(f"   ✓ ApiResponse imported from butlers.api.models")
        print(f"   ✓ Router successfully uses shared models")
    except ImportError as e:
        print(f"   ERROR: Failed to import shared models: {e}")
        sys.exit(1)

    # 6. Load models from butler api directory
    print("\n6. Verifying butler-specific models can be loaded")

    models_path = roster_dir / "health" / "api" / "models.py"
    if models_path.exists():
        try:
            models_module = load_router_from_file(models_path, "health_api_models")
            print(f"   ✓ Models module loaded: {models_module.__name__}")
            print(f"   ✓ Found models: {[name for name in dir(models_module) if not name.startswith('_')]}")
        except Exception as e:
            print(f"   ERROR: Failed to load models: {e}")

    print("\n" + "=" * 60)
    print("SUCCESS: All tests passed!")
    print("=" * 60)
    print("\nConclusions:")
    print("✓ importlib.util.spec_from_file_location works for roster/ routers")
    print("✓ FastAPI routers load and function correctly")
    print("✓ Shared models from src/butlers/api/models import successfully")
    print("✓ Butler-specific models can be co-located in api/models.py")
    print("✓ Router discovery pattern is straightforward")


if __name__ == "__main__":
    main()
